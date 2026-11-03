"""Production scene workflow versus the accepted Cloud recipe; inference stays mocked."""
import hashlib
import json
from pathlib import Path

import cv2
import pytest

from keyfx import graphs, media, people, projects
from keyfx.config import SCENE_PROFILE, SCENE_UPSCALE_PROMPT, render_length
from scene_fakes import scene_cloud
from test_opening_workflow import scene


def entry(pid, rid):
    return next(r for r in projects.load(pid)['renders'] if r['id'] == rid)


def test_hd_graph_matches_the_actual_accepted_cloud_graph():
    # Retained from the successful Oct 30 job; only diagnostic SaveLatent outputs
    # and the job-specific output prefix have been removed from the fixture.
    accepted = json.loads((Path(__file__).parent/'fixtures/scene_hd_lms05.json').read_text())
    g, uploads = graphs.scene_upscale_graph(Path('clean.mp4'), n_frames=77)
    assert g == accepted
    assert uploads == {'source': ('file', Path('clean.mp4'))}
    assert not any(k.startswith(('ref_images.', 'ref_videos.')) for k in g['cond']['inputs'])
    shorter, _ = graphs.scene_upscale_graph(Path('short.mp4'), seed=17, n_frames=22)
    assert shorter['cond']['inputs']['length'] == render_length(22)
    assert shorter['noise']['inputs']['noise_seed'] == 17


def test_two_passes_keep_native_references_and_use_only_clean_draft_for_hd(scene, monkeypatch):
    p = scene; pid = p['id']; d = projects.pdir(pid)
    p['people'] = {'selection': 'auto'}
    p['shot'] = ''
    p['renders'].append({'id': 'analysis', 'kind': 'analysis', 'status': 'rendering'})
    # A pending action suggestion is not needed to follow a reference video.
    for path in [* (d/'frames/plate').glob('*.png'), d/'opening.png']:
        cv2.imwrite(str(path), cv2.resize(cv2.imread(str(path)), (1920,1080)))
    original = hashlib.sha256((d/'frames/plate/00000.png').read_bytes()).hexdigest()
    projects.save(p)
    r = projects.start_render(pid, 'environment', _background=False)
    assert r['render_profile'] == SCENE_PROFILE and r['lms_strength'] == .5
    frozen = json.loads((d/'renders'/r['id']/'request.json').read_text())
    assert frozen['render']['lms_strength'] == .5
    raw = media.structure_video(d/'frames/plate', 3, d/'fake.mp4')
    fake = scene_cloud(raw)
    calls = []
    callbacks = {}
    def run(g, uploads, node, out, **kw):
        current = entry(pid, r['id'])
        assert current['status'] != 'done' and not current['files']
        calls.append((g, uploads, out))
        if len(calls) == 1:
            assert current['scene_phase'] == 'draft'
            assert media.probe(uploads['proxy_video'][1])['width'] == 1920
            assert cv2.imread(str(uploads['ref0'][1])).shape[:2] == (1080,1920)
            callbacks.update(kw)
        else:
            assert current['scene_phase'] == 'upscale' and current['progress'] == {} and current['job'] is None
            # A delayed event from job one must not change phase two's queue/bar.
            callbacks['on_progress']({'cloud_status': 'queued', 'step': 99, 'steps': 99})
            callbacks['on_status']('draft-job', 'done')
            current = entry(pid, r['id'])
            assert current['status'] == 'uploading' and current['progress'] == {} and current['job'] is None
            assert set(uploads) == {'source'}
            assert uploads['source'][1] == calls[0][2]/'save_0.mp4'
            assert out == d/'renders'/r['id']/'raw'
        return fake(g, uploads, node, out, **kw)
    monkeypatch.setattr(projects.comfy, 'run_graph', run)
    # Editing a draft while the frozen request runs must not change this render.
    projects.update(pid, lambda q: q.update(shot='He fires a rifle.', environment_seed=99))
    projects._run(pid, r['id'])
    done = entry(pid, r['id']); rd = d/'renders'/r['id']
    assert done['status'] == 'done', done.get('error')
    assert len(calls) == 2 and done['jobs'] == {'draft':'draft-job', 'upscale':'hd-job'}
    assert done['files']['frames'] == 3 and (done['files']['width'],done['files']['height']) == (1920,1080)
    assert calls[1][0]['cond']['inputs']['prompt'] == SCENE_UPSCALE_PROMPT
    assert calls[0][0]['noise']['inputs']['noise_seed'] == calls[1][0]['noise']['inputs']['noise_seed'] == 42
    assert (rd/'draft/raw/save_0.mp4').is_file() and (rd/'raw/save_0.mp4').is_file()
    assert media.probe(rd/'result.mp4')['frames'] == 3
    assert json.loads((rd/'scene_recipe.json').read_text())['draft_sha256'] == hashlib.sha256((rd/done['draft_file']).read_bytes()).hexdigest()
    assert hashlib.sha256((d/'frames/plate/00000.png').read_bytes()).hexdigest() == original
    assert projects.load(pid)['environment'] is None  # no automatic approval
    # Downstream FX/muzzle preparation uses the final master, despite the draft cache.
    stage = projects.stage_dir(projects.load(pid), f'environment:{r["id"]}')
    assert media.read_frame(stage,0).shape == (1080,1920,3)


def test_failed_hd_finish_retains_draft_without_publishing_or_repeating(scene, monkeypatch):
    pid = scene['id']; d = projects.pdir(pid)
    raw = media.structure_video(d/'frames/plate',3,d/'fake.mp4')
    fake = scene_cloud(raw); calls = []
    def run(g, uploads, node, out, **kw):
        calls.append(out)
        if len(calls) == 2:
            kw['on_status']('failed-hd-job','running')
            raise RuntimeError('GPU finish failed')
        return fake(g,uploads,node,out,**kw)
    monkeypatch.setattr(projects.comfy,'run_graph',run)
    r = projects.start_render(pid,'environment',_background=False)
    projects._run(pid,r['id'])
    done = entry(pid,r['id']); rd = d/'renders'/r['id']
    assert done['status'] == 'error' and len(calls) == 2
    assert 'smaller scene is saved' in done['error']
    assert (rd/done['draft_file']).is_file() and not (rd/'result.mp4').exists()
    assert done['jobs']['upscale'] == 'failed-hd-job'
    assert projects.load(pid)['environment'] is None


def test_original_cutout_is_applied_after_hd_finish(scene, monkeypatch):
    scene['people']['mode'] = 'cutout'
    scene['locations'][0]['empty_set'] = True
    pid = scene['id']; d = projects.pdir(pid)
    for path in [*(d/'frames/plate').glob('*.png'), *(d/'renders/matte/masks').glob('*.png'), d/'opening.png']:
        cv2.imwrite(str(path), cv2.resize(cv2.imread(str(path),cv2.IMREAD_UNCHANGED), (1920,1080), interpolation=cv2.INTER_NEAREST))
    projects.save(scene)
    raw = media.structure_video(d/'frames/plate',3,d/'fake.mp4')
    monkeypatch.setattr(projects.comfy,'run_graph',scene_cloud(raw))
    real = people.composite; calls = []
    def composite(video, *args):
        assert media.probe(video)['width'] == 1920
        assert video.parent.name == 'raw' and video.parent.parent.name != 'draft'
        calls.append(video)
        return real(video,*args)
    monkeypatch.setattr(people,'composite',composite)
    r = projects.start_render(pid,'environment',_background=False)
    projects._run(pid,r['id'])
    done = entry(pid,r['id'])
    assert done['status'] == 'done',done.get('error')
    assert len(calls) == 1
    output = media.read_video(d/'renders'/r['id']/'composited.mp4',1)[0]
    source = media.read_frame(d/'frames/plate',0)
    assert abs(media.unpad(output)[400,240].mean()-source[400,240].mean()) < 2


@pytest.mark.parametrize('width,height,frames,fps',[(1920,1088,5,24),(960,544,3,24),(960,544,5,30)])
def test_invalid_draft_cannot_trigger_a_paid_finish(tmp_path,monkeypatch,width,height,frames,fps):
    monkeypatch.setattr(media,'probe',lambda _:dict(width=width,height=height,frames=frames,fps=fps))
    with pytest.raises(RuntimeError):
        projects._scene_video_check(tmp_path/'draft.mp4',960,544,3)
