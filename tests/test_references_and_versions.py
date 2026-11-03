"""Workflow regressions. Every render is stubbed; these tests never call Comfy."""
import copy
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from keyfx import flashes, graphs, media, projects
from keyfx.server import app
from scene_fakes import scene_cloud


@pytest.fixture
def shot(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, 'DATA', tmp_path)
    def no_cloud(*a, **kw):
        raise AssertionError('A local workflow test attempted to call Comfy')
    monkeypatch.setattr(projects.comfy, 'run_graph', no_cloud)
    monkeypatch.setattr(flashes, 'generate', no_cloud)
    p = {'id': 'test', 'name': 'Test', 'mode': 'greenscreen', 'frames': 3, 'created': '2026-10-28',
         'source': {}, 'shot': 'A soldier raises his rifle', 'location': 'warehouse',
         'environment': 'a', 'approved_environment': 'a', 'background': 'scene.png',
         'opening_reference': 'opening.png', 'reference_mode': 'opening_only',
         'settings': {'seed': 7, 'strength': 'guide', 'polish': False}, 'environment_seed': 7,
         'location_seed': 11, 'effects': [], 'flashes': [], 'flash_base': 'environment:a',
         'locations': [{'id': 'open', 'file': 'opening.png', 'frame': 0},
                       {'id': 'scene', 'file': 'scene.png', 'frame': 40}],
         'renders': [{'id': 'a', 'kind': 'environment', 'status': 'done', 'files': {}}]}
    d = projects.pdir('test'); d.mkdir(parents=True)
    for name, color in [('opening.png', 80), ('scene.png', 160)]:
        cv2.imwrite(str(d / name), np.full((540, 960, 3), color, np.uint8))
    for folder, color in [(d / 'frames/plate', 30), (d / 'renders/a/frames', 80)]:
        folder.mkdir(parents=True)
        for i in range(3):
            cv2.imwrite(str(folder / f'{i:05d}.png'), np.full((540, 960, 3), color, np.uint8))
    projects.save(p)
    return p


def add_flash(p, base='environment:a', fid='abc123', frame=1):
    result = projects.pdir(p['id']) / f'flashes/{fid}/frame.png'
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_bytes(media.frame_path(projects.stage_dir(p, base), frame).read_bytes())
    f = {'id': fid, 'frame': frame, 'origin': {'x': .4, 'y': .5}, 'tip': {'x': .3, 'y': .5},
         'seed': 1, 'status': 'done', 'result': str(result.relative_to(projects.pdir(p['id']))),
         'result_base': base, 'result_frame_hash': projects.frame_hash(p, base, frame)}
    p['flashes'].append(f)
    projects.save(p)
    return f


def add_environment(p, rid='b', color=180):
    p['renders'].append({'id': rid, 'kind': 'environment', 'status': 'done', 'files': {}})
    fd = projects.pdir(p['id']) / f'renders/{rid}/frames'; fd.mkdir(parents=True)
    for i in range(3):
        cv2.imwrite(str(fd / f'{i:05d}.png'), np.full((540, 960, 3), color, np.uint8))
    projects.save(p)


def test_environment_graph_has_explicit_time_zero_guide(tmp_path):
    scene, opening, video = (tmp_path / n for n in ['scene.png', 'opening.png', 'source.mp4'])
    g, inputs = graphs.environment_graph('same prompt', scene, video, 7, opening=opening)
    assert g['guide_opening']['inputs']['frame_idx'] == 0
    assert g['guider']['inputs']['conditioning'] == ['guide_opening', 0]
    assert g['guide_opening']['inputs']['positive'] == ['cond', 0]
    assert inputs['img_opening'] == ('image', opening)
    assert inputs['ref0'] == ('image', scene)
    assert inputs['proxy_video'] == ('file', video)


def test_candidate_completion_does_not_select_it_and_uses_frozen_request(shot, monkeypatch):
    class Deferred:
        def __init__(self, **kwargs): pass
        def start(self): pass
    monkeypatch.setattr(projects.threading, 'Thread', Deferred)
    r = projects.start_render('test', 'environment')
    p = projects.load('test'); p['environment_seed'] = 99; p['shot'] = 'A different action'; projects.save(p)
    d = projects.pdir('test')
    video = d / 'fake.mp4'
    w = media._writer(video, 960, 544, True)
    for _ in range(3): w.stdin.write(np.full((544, 960, 3), 100, np.uint8).tobytes())
    w.stdin.close(); assert w.wait() == 0
    seen = {}
    monkeypatch.setattr(projects.comfy, 'run_graph', scene_cloud(video, seen))
    projects._run('test', r['id'])
    p = projects.load('test')
    done = next(x for x in p['renders'] if x['id'] == r['id'])
    assert done['status'] == 'done', done.get('error')
    assert p['environment'] == p['approved_environment'] == 'a'
    assert seen['graph']['noise']['inputs']['noise_seed'] == 7
    assert 'different action' not in seen['graph']['cond']['inputs']['prompt']
    assert np.array_equal(cv2.imread(str(seen['uploads']['ref0'][1])), cv2.imread(str(d / 'opening.png')))
    assert len(list((d / f'renders/{r["id"]}/frames').glob('*.png'))) == 3
    assert cv2.imread(str(d / 'renders/a/frames/00000.png')).mean() == 80


def test_approval_invalidates_downstream_and_switching_back_restores_them(shot):
    f = add_flash(shot)
    shot['renders'].append({'id': 'fx-a', 'kind': 'effect', 'status': 'done', 'base': 'environment:a', 'files': {},
                            'created': '2026-10-28 19:00', 'seed': 7})
    add_environment(shot)
    assert projects.flash_current(shot, f)
    b = projects.approve_environment('test', 'b')
    assert b['environment'] == b['approved_environment'] == 'b'
    assert not projects.flash_current(b, f)
    assert not projects.render_current(b, next(r for r in b['renders'] if r['id'] == 'fx-a'))
    assert 'render:fx-a' not in {s['id'] for s in projects.stages(b)}
    with pytest.raises(ValueError, match='outdated'):
        projects.export_final('test')
    a = projects.approve_environment('test', 'a')
    assert projects.flash_current(a, f)
    assert f['result'] == a['flashes'][0]['result']  # retained, never deleted


def test_unreviewed_environment_cannot_start_downstream(shot):
    shot['approved_environment'] = None; projects.save(shot)
    with pytest.raises(ValueError, match='approve'):
        projects.start_flashes('test')
    with pytest.raises(ValueError, match='approve'):
        projects.export_final('test', omit_unready=True)
    shot['effects'] = [{'id': 'e'}]; projects.save(shot)
    with pytest.raises(ValueError, match='approve'):
        projects.start_render('test', 'effect')


def test_legacy_flash_requires_saved_input_proof(shot):
    f = add_flash(shot); f['result_base'] = 'environment'; f.pop('result_frame_hash')
    projects.save(shot)
    assert not projects.flash_current(shot, f)
    clean = (projects.pdir('test') / f['result']).parent / 'clean.png'
    current = cv2.imread(str(projects.stage_dir(shot, 'environment:a') / '00001.png'))
    cv2.imwrite(str(clean), flashes.to_model_canvas(current))
    assert projects.flash_current(shot, f)
    assert projects.public(shot)['flashes'][0]['result_base'] == 'environment:a'
    cv2.imwrite(str(clean), flashes.to_model_canvas(np.full_like(current, 200)))
    assert not projects.flash_current(shot, f)


def test_export_omits_unready_only_when_explicit_and_tracks_flash_changes(shot):
    f = add_flash(shot)
    shot['flashes'].append({'id': 'def456', 'frame': 2, 'status': 'new', 'origin': {'x': .5, 'y': .5}, 'tip': {'x': .4, 'y': .5}})
    projects.save(shot)
    with pytest.raises(ValueError, match='Explicitly omit'):
        projects.export_final('test')
    r = projects.export_final('test', omit_unready=True)
    assert r['flashes'] == [1] and r['omitted_flashes'] == ['def456']
    p = projects.load('test'); assert projects.render_current(p, r)
    p['flashes'][1]['frame'] = 0
    assert not projects.render_current(p, r)


def test_uploaded_references_are_immutable_and_later_poses_cannot_be_openings(shot):
    c = TestClient(app, base_url='http://127.0.0.1:8766')
    enc = lambda v: cv2.imencode('.png', np.full((30, 40, 3), v, np.uint8))[1].tobytes()
    a = c.post('/api/projects/test/background', files={'file': ('first.png', enc(30), 'image/png')}, data={'role': 'opening'})
    assert a.status_code == 200
    first = a.json()['opening_reference']; first_bytes = (projects.pdir('test') / first).read_bytes()
    b = c.post('/api/projects/test/background', files={'file': ('second.png', enc(90), 'image/png')}, data={'role': 'opening'})
    assert b.status_code == 200 and b.json()['opening_reference'] != first
    assert (projects.pdir('test') / first).read_bytes() == first_bytes
    invalid = c.patch('/api/projects/test', json={'opening_reference': 'scene.png'})
    assert invalid.status_code == 400
    assert projects.load('test')['opening_reference'] == b.json()['opening_reference']


def test_room_creation_does_not_use_legacy_prop_boxes(shot, monkeypatch):
    shot.update(location_role='scene',empty_set=True, dressing=[{'id': 'd', 'text': 'a crate', 'frame': 40,
                'box': {'x0': .1, 'y0': .1, 'x1': .3, 'y1': .3}}])
    projects.save(shot)
    class Deferred:
        def __init__(self, **kwargs): pass
        def start(self): pass
    monkeypatch.setattr(projects.threading, 'Thread', Deferred)
    def build(frame, text, seed, out):
        assert np.all(frame == 30)  # original plate, with no painted rectangles
        assert 'crate' not in text
        image = out/'location.png';cv2.imwrite(str(image),frame)
        return image
    monkeypatch.setattr(projects.location,'build',build)
    r=projects.start_render('test','location');projects._run('test',r['id'])
    p=projects.load('test')
    assert next(x for x in p['renders'] if x['id']==r['id'])['status']=='done'
    assert p['locations'][0]['dressing']==[]
    assert p['opening_reference']=='opening.png'


def test_flash_geometry_is_not_editable_while_in_flight(shot):
    f=add_flash(shot); f['status']='generating'
    with pytest.raises(ValueError, match='finish'):
        projects.merge_flash_edits(shot, [{**f, 'frame': 2}])
    assert f['frame']==1


def test_reference_defaults_survive_api_replacements_and_recovery_preserves_recency(shot):
    q=projects.public(shot)
    assert q['location_role']=='opening' and q['reference_mode']=='opening_only'
    assert q['location_seed']==11 and q['environment_seed']==7
    path=projects.pdir('test')/'project.json'; before=path.stat().st_mtime_ns
    assert projects.recover()==0
    assert path.stat().st_mtime_ns==before


def test_same_version_name_is_not_enough_when_frame_hash_differs(shot):
    f=add_flash(shot)
    assert projects.flash_current(shot,f)
    f['result_frame_hash']='wrong'
    assert not projects.flash_current(shot,f)


def test_legacy_original_plate_results_can_be_verified_without_regeneration(shot):
    shot.update(mode='plate', environment=None, flash_base='plate')
    f=add_flash(shot,base='plate'); f.pop('result_frame_hash')
    clean=(projects.pdir('test')/f['result']).parent/'clean.png'
    frame=cv2.imread(str(projects.pdir('test')/'frames/plate/00001.png'))
    cv2.imwrite(str(clean),flashes.to_model_canvas(frame))
    assert projects.flash_current(shot,f)
    assert projects.render_current(shot,{'kind':'effect'})
