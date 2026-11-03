"""Offline integration of immutable model assets with native photograph frames.

Only the provider and final container export are fake. Guides, decoded model
assets, integrity manifests and native16-bit composite PNGs are real files.
"""
import copy
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from keyfx import connections, element_api, element_graphs, element_jobs, elements, geometry
from keyfx import native_colour, native_media, presets, projects


class ImmediateThread:
    def __init__(self, target, args=(), kwargs=None, **_):
        self.target, self.args, self.kwargs = target, args, kwargs or {}
    def start(self):
        self.target(*self.args, **self.kwargs)


def png(im):
    ok, data = cv2.imencode('.png', im)
    assert ok
    return data.tobytes()


def catalogue(graphs):
    result = {}
    for graph in graphs:
        for node in graph.values():
            inputs = result.setdefault(node['class_type'], {'input': {'required': {}}})['input']['required']
            for key, value in node['inputs'].items():
                if key in connections._MODEL_FIELDS and isinstance(value, str):
                    choices = inputs.setdefault(key, [[], {}])[0]
                    if value not in choices: choices.append(value)
    return result


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, 'DATA', tmp_path)
    monkeypatch.setattr(element_jobs.threading, 'Thread', ImmediateThread)
    cfg = connections.Connection(mode='local', local_url='http://127.0.0.1:8189', comfyui_url='http://127.0.0.1:8188')
    monkeypatch.setattr(connections, 'current', lambda: cfg)
    monkeypatch.setattr(connections, '_get_json', lambda *a, **kw: pytest.fail('Unexpected backend/network lookup'))
    monkeypatch.setattr(native_media, 'write_display_proxies', lambda *a, **kw: pytest.fail('Dead compositor proxy branch ran'))
    exports = []
    def export(fd, profile, rd, source_audio=None):
        frames = [native_media.read_native_frame(fd, i) for i in range(profile['frames'])]
        exports.append({'frames': frames, 'profile': copy.deepcopy(profile), 'directory': rd})
        for i, frame in enumerate(frames):
            native_media.write_native_frame(rd/'display_frames', i, native_colour.display_frame(frame, profile))
        return {'master': 'master.mov', 'result': 'result.mp4', 'poster': 'poster.jpg'}
    monkeypatch.setattr(native_media, 'export_native', export)
    def create(*, fps='24000/1001', transfer='bt709', primaries='bt709'):
        rate = Fraction(fps); n, w, h = 8, 18, 32
        profile = {'width': w, 'height': h, 'frames': n, 'fps': float(rate), 'fps_rational': fps,
                   'fps_num': rate.numerator, 'fps_den': rate.denominator, 'time_base': f'1/{rate.numerator}',
                   'source_pts': [10000+i*rate.denominator for i in range(n)],
                   'frame_durations_ticks': [rate.denominator]*n, 'timestamps': [float(i/rate) for i in range(n)],
                   'duration': float(n/rate), 'storage_bit_depth': 16, 'bit_depth': 16, 'is_vfr': False,
                   'source_sha256': hashlib.sha256(b'fixture-original').hexdigest(), 'source_file': 'source.mov',
                   'colour': {'transfer': transfer, 'primaries': primaries, 'matrix': 'gbr', 'range': 'pc'}}
        p = {'id': 'shot', 'mode': 'plate', 'name': 'Native fixture', 'frames': n, 'renders': [], 'flashes': [],
             'element_takes': [], 'geometry': geometry.defaults(), 'settings': {'pipeline': 'ltx_elements'},
             'native_profile': profile}
        effect = presets.new_effect('explosion', 2, .5, .5, 'blast', n)
        effect['element'] = {**element_jobs.DEFAULT, 'duration_s': .1, 'blur': 0}
        p['effects'] = [effect]
        root = projects.pdir('shot'); (root/'native').mkdir(parents=True)
        (root/'native'/'profile.json').write_text(json.dumps(profile))
        originals = []
        for i in range(n):
            linear = np.broadcast_to(np.array([2.2, .7, 1.5]) if transfer == 'smpte2084' else [.35, .2, .45], (h,w,3)).copy()
            linear += np.arange(w)[None,:,None] * .001 + i * .0001
            frame = native_colour.from_linear(linear, profile, np.uint16); originals.append(frame)
            native_media.write_native_frame(root/'native'/'frames', i, frame)
            native_media.write_native_frame(root/'frames'/'plate', i, native_colour.display_frame(frame, profile))
        projects.save(p)
        return p, root, originals
    return create, exports, cfg


def prepare(p):
    spec = element_jobs.plan(p, 'blast'); images, visibility = {}, {}
    for k in range(spec['active_frames']):
        guide = np.zeros((spec['height'], spec['width'], 3), np.uint8)
        if k: guide[14:17, 7:10] = 180  # An empty first frame remains valid.
        vis = np.full(guide.shape[:2], 255, np.uint8); vis[:,8:10] = 0
        images[k], visibility[k] = png(guide), png(vis)
    return element_jobs.prepare_upload(p['id'], 'blast', {'signature': spec['signature']}, images, visibility)


def generate(p, monkeypatch):
    row = prepare(p); calls = []
    def provider(*, graph, uploads, out_dir, on_status, **_):
        stage = 'alpha' if 'alpha-gen' in graph['lora']['inputs']['lora_name'] else 'rgb'
        calls.append(stage)
        assert set(uploads) == {'control'}
        assert '/native/' not in str(uploads['control'][1]), 'Photographic source reached LTX/Alpha'
        g = graph['latent']['inputs']; width, height, n = g['width'], g['height'], g['length']
        x, y = (width-row['plan']['width'])//2, (height-row['plan']['height'])//2
        frames = []
        for k in range(n):
            im = np.zeros((height, width, 3), np.uint8)
            if stage == 'rgb': im[y+10:y+21,x+5:x+13] = (20, 50, 100+k*8)
            else: im[y+8:y+23,x+3:x+15] = 128  # Wider than guide support: never cap predicted alpha.
            frames.append(im)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir/'result.mp4'; elements._encode(path, frames)
        on_status('fake-'+stage, 'running')
        return [path]
    actual = elements.run
    nodes = catalogue([g for _,_,g,_ in element_graphs.requirements({})])
    monkeypatch.setattr(elements, 'run', lambda td, on_status=None: actual(td, executor=provider, catalog=nodes, on_status=on_status))
    element_jobs.start(p['id'], row['id'])
    current = projects.load(p['id'])
    assert current['element_takes'][0]['status'] == 'done', current['renders'][0]
    assert calls == ['rgb', 'alpha']
    return current['element_takes'][0], calls


@pytest.mark.parametrize('fps,transfer,primaries', [('24000/1001','bt709','bt709'), ('60/1','smpte2084','bt2020')])
def test_fake_rgb_alpha_to_native_portrait_composite_preserves_plate_and_timing(pipeline, monkeypatch, fps, transfer, primaries):
    create, exports, _ = pipeline
    p, root, originals = create(fps=fps, transfer=transfer, primaries=primaries)
    row, calls = generate(p, monkeypatch)
    sig = element_jobs.generation_signature(p, p['effects'][0])
    job = element_jobs.composite('shot')
    assert projects.load('shot')['renders'][0]['status'] == 'done', projects.load('shot')['renders'][0]
    frames = exports[-1]['frames']
    assert exports[-1]['profile'] == p['native_profile']
    assert all(f.shape == (32,18,3) and f.dtype == np.uint16 for f in frames)
    # Every native frame before onset is exact, including60fps frame1 which
    # rounding alone would incorrectly map to generated frame0.
    assert np.array_equal(frames[0], originals[0]) and np.array_equal(frames[1], originals[1])
    take = elements.inspect(row['take_dir']); x,y,w,h = take['geometry']['content_rect']
    for f, timestamp in enumerate(p['native_profile']['timestamps']):
        k = element_jobs.model_index(row['plan'], timestamp)
        if k is None:
            assert np.array_equal(frames[f], originals[f]); continue
        rgb = cv2.cvtColor(cv2.imread(str(Path(take['assets']['rgb']['frames_dir'])/f'{k:05d}.png'))[y:y+h,x:x+w],cv2.COLOR_BGR2RGB)
        a = cv2.imread(str(Path(take['assets']['alpha']['frames_dir'])/f'{k:05d}.png'),0)[y:y+h,x:x+w] / 255
        vis = cv2.imread(str(root/row['inputs']/'visibility'/f'{k:05d}.png'),0) / 255
        foreground = native_colour.convert_primaries(native_colour.srgb_to_linear(rgb)*vis[...,None], 'bt709', primaries)
        expected = native_colour.from_linear(native_colour.to_linear(originals[f],p['native_profile'])*(1-a[...,None]*vis[...,None])+foreground,p['native_profile'],np.uint16)
        affected = ((a != 0) | np.any(rgb != 0, axis=2)) & (vis != 0)
        assert np.array_equal(frames[f][~affected], originals[f][~affected]), 'Untouched native pixels changed'
        assert np.max(np.abs(frames[f][affected].astype(int)-expected[affected].astype(int))) <= 1
        assert np.any(frames[f][affected] != originals[f][affected])
    for i, original in enumerate(originals):
        assert np.array_equal(native_media.read_native_frame(root/'native',i), original)
    # Post controls reuse model assets, yet produce a new native composite.
    updated = projects.load('shot'); updated['effects'][0]['element'].update(opacity=.5,gain=1.2,light=.3,shadow=.4); projects.save(updated)
    assert element_jobs.generation_signature(updated,updated['effects'][0]) == sig
    assert element_jobs.start('shot',row['id'])['status'] == 'done'
    element_jobs.composite('shot')
    assert calls == ['rgb','alpha'] and len(exports) == 2
    assert not np.array_equal(exports[1]['frames'][2], frames[2])
    assert np.array_equal(exports[1]['frames'][1], originals[1])


def test_post_visibility_and_model_cache_tampering_block_generation_or_composite(pipeline, monkeypatch):
    create, _, _ = pipeline; p, root, _ = create()
    row = prepare(p)
    visibility = root/row['inputs']/'visibility'/'00000.png'
    visibility.write_bytes(png(np.zeros((32,18),np.uint8)))
    monkeypatch.setattr(elements, 'run', lambda *a, **kw: pytest.fail('Tampered guide caused a model spend'))
    with pytest.raises(ValueError, match='visibility/guide input changed'):
        element_jobs.start('shot',row['id'])
    assert projects.load('shot')['renders'] == []


def test_tampered_completed_rgb_is_rejected_before_composite_queue(pipeline, monkeypatch):
    create, _, _ = pipeline; p, _, _ = create()
    row, _ = generate(p, monkeypatch)
    path = Path(row['assets']['rgb']['frames_dir'])/'00000.png'
    im = cv2.imread(str(path)); im[0,0] = 255; cv2.imwrite(str(path), im)
    before = len(projects.load('shot')['renders'])
    with pytest.raises(ValueError, match='cached RGB/alpha asset'):
        element_jobs.composite('shot')
    assert len(projects.load('shot')['renders']) == before


def test_entirely_black_processed_guide_is_actionable_and_never_prepares_model(pipeline, monkeypatch):
    create, _, _ = pipeline; p, _, _ = create()
    spec = element_jobs.plan(p,'blast'); black = png(np.zeros((spec['height'],spec['width'],3),np.uint8))
    frames = {k:black for k in range(spec['active_frames'])}
    monkeypatch.setattr(elements,'prepare',lambda *a,**kw:pytest.fail('Empty collider result reached model preparation'))
    with pytest.raises(ValueError,match='Move the origin/depth or review collision'):
        element_jobs.prepare_upload('shot','blast',{'signature':spec['signature']},frames,frames)
    assert projects.load('shot')['element_takes'] == []


def test_plan_enabled_phases_depth_offset_and_native_pts_sampling(pipeline):
    create, _, _ = pipeline; p, _, _ = create()
    effect = p['effects'][0]
    for key in effect['keys']:
        if key['phase'] == 'onset': key['enabled'] = False
    enabled_stamps = [k['frame'] for k in effect['keys'] if k.get('enabled') is not False and k['phase'] != 'before']
    assert element_jobs.plan(p,'blast')['onset_frame'] == min(enabled_stamps)
    for key in effect['keys']: key['enabled'] = False
    with pytest.raises(ValueError,match='Enable at least one'):
        element_jobs.plan(p,'blast')
    for key in effect['keys']: key['enabled'] = True
    sig = element_jobs.generation_signature(p,effect)
    effect['element']['depth_offset'] = .5
    assert element_jobs.generation_signature(p,effect) != sig
    effect['element']['depth_offset'] = 6
    with pytest.raises(ValueError,match='depth_offset'):
        element_jobs.effect_config(p,effect)
    effect['element']['depth_offset'] = 0
    assert element_jobs.plan(p,'blast')['source_frames'] == [2,3,4]
    spec = {'start_time': .05, 'active_frames': 3}
    assert element_jobs.model_index(spec,.049999) is None
    assert element_jobs.model_index(spec,.05) == 0
    assert element_jobs.model_index(spec,.174999) == 2
    assert element_jobs.model_index(spec,.175001) is None


def test_canonical_onset_wins_over_retimed_earlier_legacy_phases(pipeline):
    create, _, _ = pipeline; p, _, _ = create()
    effect = p['effects'][0]
    effect['keys'] = [{'phase':'before','frame':0}, {'phase':'peak','frame':1}, {'phase':'onset','frame':4}]
    assert element_jobs.plan(p,'blast')['onset_frame'] == 4
    effect['keys'][2]['enabled'] = False
    assert element_jobs.plan(p,'blast')['onset_frame'] == 1
    for preset, phase in [('smoke_white','start'),('smoke_plume','start'),('wall_hit','impact'),('dirt_hit','impact')]:
        effect['preset'] = preset
        effect['keys'] = [{'phase':'before','frame':0}, {'phase':'other_action','frame':1}, {'phase':phase,'frame':3}]
        assert element_jobs.plan(p,'blast')['onset_frame'] == 3


def test_offline_prepared_guide_is_rebound_after_connection_change_without_spending(pipeline,monkeypatch):
    create, _, cfg = pipeline; p, _, _ = create()
    monkeypatch.setattr(connections,'current',lambda:connections.Connection(enabled=False))
    old = prepare(p)
    offline = element_jobs.records(projects.load('shot'))[0]
    assert offline['current'] and offline['backend_current']
    monkeypatch.setattr(connections,'current',lambda:cfg)
    changed = element_jobs.records(projects.load('shot'))[0]
    assert changed['current'] and changed['backend_current'] is False
    public = element_api.public_take('shot',changed)
    assert public['backend_current'] is False and 'backend' not in public and 'identity' not in json.dumps(public)
    monkeypatch.setattr(elements,'run',lambda *a,**kw:pytest.fail('Connection mismatch spent on a model'))
    with pytest.raises(ValueError,match='Prepare a new guide version'):
        element_jobs.start('shot',old['id'])
    assert projects.load('shot')['renders'] == []
    fresh = prepare(projects.load('shot'))
    assert fresh['id'] != old['id']
    assert elements.inspect(fresh['take_dir'])['backend'] == elements._connection_identity(cfg)
    rows = {row['id']:row for row in element_jobs.records(projects.load('shot'))}
    assert rows[old['id']]['current'] and rows[old['id']]['backend_current'] is False
    assert rows[fresh['id']]['current'] and rows[fresh['id']]['backend_current'] is True


def test_completed_assets_remain_current_without_reading_connection(pipeline,monkeypatch):
    create, _, _ = pipeline; p, _, _ = create()
    row, calls = generate(p,monkeypatch)
    monkeypatch.setattr(connections,'current',lambda:pytest.fail('Completed local assets read connection/account'))
    current = element_jobs.records(projects.load('shot'))[0]
    assert current['current'] and current['backend_current']
    assert element_jobs.start('shot',row['id'])['status'] == 'done'
    element_jobs.composite('shot')
    assert calls == ['rgb','alpha'] and projects.load('shot')['renders'][0]['status'] == 'done'


def test_geometry_preflight_rejects_missing_dependencies_before_upload(pipeline):
    create, _, cfg = pipeline; p, _, _ = create()
    geometry.start_job('shot','depth',reference_frame=2,connection=cfg,catalog={},
                       executor=lambda **kw:pytest.fail('Missing depth capability uploaded/submitted pixels'))
    current = projects.load('shot'); job = current['renders'][0]
    assert job['status'] == 'error' and 'no upload or model generation' in job['error']
    assert current['geometry']['depth'] is None


def test_geometry_reference_frame_and_matte_result_dimensions_are_frozen(pipeline):
    create, _, cfg = pipeline; p, root, _ = create()
    seen = []
    def depth(**kw):
        seen.append(kw['uploads']['input'][1].name)
        path = kw['out_dir']/'depth.png'; path.parent.mkdir(parents=True)
        path.write_bytes(png(np.arange(32*18,dtype=np.uint16).reshape(32,18)*100))
        return [path]
    geometry.start_job('shot','depth',reference_frame=2,connection=cfg,catalog=catalogue([geometry.depth_graph('check')]),executor=depth)
    saved = projects.load('shot')
    assert seen == ['00002.png'] and saved['renders'][0]['status'] == 'done'
    assert saved['geometry']['reference_frame'] == saved['geometry']['depth']['reference_frame'] == 2
    points = [{'frame':0,'person':0,'label':1,'x':.5,'y':.5}]
    selection = geometry.people.validate({'selection':'manual','points':points},p['frames'])
    sam = geometry.people.graph(selection,'test',(18,32))
    def wrong(**kw):
        path = kw['out_dir']/'matte.mp4'; path.parent.mkdir(parents=True)
        elements._encode(path,[np.full((16,16,3),128,np.uint8) for _ in range(p['frames'])])
        return [path]
    geometry.start_job('shot','matte',points=points,connection=cfg,catalog=catalogue([sam]),executor=wrong)
    saved = projects.load('shot')
    assert saved['renders'][0]['status'] == 'error' and 'frame count or dimensions' in saved['renders'][0]['error']
    assert saved['geometry']['mattes'] == []


def test_native_final_copies_composite_and_only_swaps_selected_flash(pipeline,monkeypatch):
    create, exports, _ = pipeline; p, root, _ = create()
    generate(p,monkeypatch); render = element_jobs.composite('shot')
    fd = root/'renders'/render['id']/'master_frames'
    flash = native_media.read_native_frame(fd,3); flash[1,1] = [60000,40000,20000]
    path = native_media.write_native_frame(root/'flashes'/'fixture',0,flash)
    current = projects.load('shot'); current['flashes']=[{'id':'flash','frame':3,'status':'done','native_result':str(path.relative_to(root)), 'result':str(path.relative_to(root))}]; projects.save(current)
    monkeypatch.setattr(projects,'flash_base',lambda p:f'render:{render["id"]}')
    monkeypatch.setattr(projects,'flash_current',lambda p,f,base=None:True)
    final = projects.export_final('shot')
    out = root/'renders'/final['id']/'master_frames'
    for i in range(p['frames']):
        expected = path if i == 3 else fd/f'{i:05d}.png'
        assert (out/f'{i:05d}.png').read_bytes() == expected.read_bytes()
    assert final['flashes'] == [3] and exports[-1]['profile']['fps_rational'] == '24000/1001'
