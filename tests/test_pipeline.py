"""Offline checks: presets load, keys compose, prompts and graphs are well formed (no Comfy calls)."""
from pathlib import Path

import numpy as np

from keyfx import graphs, keys, presets, prompts


def test_presets_and_stamps():
    ps = presets.load_all()
    assert {'explosion', 'muzzle_side', 'wall_hit', 'smoke_plume'} <= set(ps)
    for p in ps.values():
        for ph in p['phases']:
            if ph.get('stamp') and ph['stamp'] != 'hole':
                assert presets.stamp(p['id'], ph['stamp']).shape[2] == 4
        for lk in p['looks']:
            assert (Path(p['dir']) / lk['file']).exists()


def _effects():
    return [presets.new_effect('explosion', 30, 0.7, 0.5, 'e1', 100), presets.new_effect('muzzle_side', 10, 0.4, 0.4, 'e2', 100)]


def test_state_and_compose(tmp_path):
    e = _effects()
    assert keys.state_at(e[0], 27) is None                     # before the explosion
    assert keys.state_at(e[0], 31)['phase'] == 'onset'
    assert keys.state_at(e[0], 99)['phase'] == 'aftermath'     # persists
    assert keys.state_at(e[1], 50) is None                     # muzzle smoke has ended
    base = np.full((540, 960, 3), 80, np.float32)
    out = keys.compose(base, e, 36)
    assert out.shape == base.shape and out.max() > 100
    hard = dict(keys.key_frames(e))
    assert hard[10] is True and hard[30] is False


def test_prompt_and_graph(tmp_path):
    e = _effects()
    text = prompts.effect_prompt('A man fires at a wall', e)
    assert '<Picture 1>' in text and '1.25 seconds' in text and 'No shafts' in text
    ks = [(10, tmp_path / 'a.png', True), (28, tmp_path / 'b.png', False)]
    g, up = graphs.effect_graph(text, [tmp_path / 'l.png'], tmp_path / 's.mp4', tmp_path / 'o.png', ks, 'guide', 7)
    assert g['early']['inputs']['end'] == 0.377 and g['late']['inputs']['start'] == 0.377
    assert 'img_h10' in g and 'img_h28' not in g                # only hard keys stay on late
    refs = {v[0] for k in g.values() for v in k['inputs'].values() if isinstance(v, list)}
    assert refs <= set(g), refs - set(g)                       # no dangling links
    assert up['proxy_video'][0] == 'file'


def test_polish_prompt_and_mask():
    from keyfx import polish
    e = _effects()
    e[0]['where'] = 'the crates'
    text = polish.prompt_for(e, 36)                              # explosion fireball is polished
    assert text and 'fireball' in text and 'the crates' in text and 'exactly the same' in text
    assert polish.prompt_for(e, 10) is None                     # muzzle flash: small effects are left as placed
    base = np.full((540, 960, 3), 80, np.float32)
    mask = np.zeros((540, 960), np.float32)
    keys.compose(base, e, 36, mask)
    grown = polish.edit_mask(mask)
    assert grown.shape == (540, 960, 1) and grown.max() > 0.99 and (grown > 0.01).sum() > (mask > 0.05).sum()
    assert grown[0, 0, 0] < 0.01                                # far from the stamp the plate is untouched
    g = polish.graph(text, 3)
    refs = {v[0] for k in g.values() for v in k['inputs'].values() if isinstance(v, list)}
    assert refs <= set(g)


def test_repeat_keys_for_persisting_effects():
    e = presets.new_effect('explosion', 22, 0.8, 0.6, 'e1', 55)
    after = [k for k in e['keys'] if k['phase'] == 'aftermath']
    assert len(after) >= 2 and after[-1]['frame'] == 54                    # editable repeat keys up to the last frame
    assert keys.hold_frames([e], 55) == {}                                 # nothing left to add automatically
    legacy = {**e, 'keys': [k for k in e['keys'] if k is not after[-1] and k not in after[1:]]}
    holds = keys.hold_frames([legacy], 55)                                 # older projects without repeats still get holds
    assert holds and 54 in holds
    frames = [f for f, _ in keys.key_frames([legacy], 55)]
    assert frames == sorted(frames) and set(holds) <= set(frames)
    m = presets.new_effect('muzzle_side', 10, 0.4, 0.4, 'm', 55)
    assert keys.hold_frames([m], 55) == {} and all(k['phase'] != 'clear' or k['frame'] == 18 for k in m['keys'])


def test_direction_carries_into_prompts():
    from keyfx import polish
    e = presets.new_effect('smoke_plume', 4, 0.7, 0.55, 's', 55)
    assert e['anchors'] == [{'frame': 4, 'x': 0.7, 'y': 0.55}] and e['size'] == 1.0
    assert prompts.direction_phrase(e) == ''
    e.update(angle=90, strength=0.95)
    ph = prompts.direction_phrase(e)
    assert 'toward the right of the frame' in ph and 'strong wind' in ph
    assert ph in prompts.effect_prompt('Two people talk', [e])
    assert polish.prompt_for([e], 40) is None          # Squib smoke is sent as simulated, not polished
    e.update(angle=270, strength=0.3)
    assert 'gently toward the left' in prompts.direction_phrase(e)
    m = presets.new_effect('muzzle_side', 10, 0.5, 0.4, 'm', 55)
    assert prompts.direction_phrase(m) == 'firing toward the right of the frame'


def test_keys_from_browser_layers(tmp_path):
    import cv2
    from keyfx import projects
    base = tmp_path / 'frames'; base.mkdir()
    for i in range(4):
        cv2.imwrite(str(base / f'{i:05d}.png'), np.full((540, 960, 3), 100, np.uint8))
    ld = tmp_path / 'layers'; ld.mkdir()
    fx = np.zeros((540, 960, 4), np.uint8); fx[100:200, 100:200] = (0, 0, 0, 255)          # opaque black square
    glow = np.zeros((540, 960, 4), np.uint8); glow[300:310, 300:310] = (0, 0, 255, 128)    # half-strength red light
    cv2.imwrite(str(ld / 'fx_0003.png'), fx); cv2.imwrite(str(ld / 'glow_0003.png'), glow)
    img, mask = projects._layer_comp(base, ld, 3)
    assert img[150, 150].max() == 0 and mask[150, 150] == 1.0 and mask[0, 0] == 0
    assert abs(img[305, 305, 2] - (100 + 255 * 128 / 255)) < 1 and img[0, 0, 0] == 100
    assert projects._layer_comp(base, ld, 4) is None


def test_preroll_smoke():
    e = presets.new_effect('smoke_plume', -48, 0.7, 0.55, 'p', 55)
    assert min(k['frame'] for k in e['keys']) < 0 and max(k['frame'] for k in e['keys']) == 54
    assert keys.prerolled(e) and keys.state_at(e, 0) is not None
    fr = [f for f, _ in keys.key_frames([e], 55)]
    assert fr[0] == 0 and all(0 <= f <= 54 for f in fr) and keys.hold_frames([e], 55) == {}
    text = prompts.effect_prompt('Two people talk', [e])
    assert 'already pouring' in text and '-' not in text.split('seconds')[0][-8:] and 'before the shot begins' not in text
    e.update(angle=90, strength=0.9)
    assert 'streaming away on the wind' in prompts.effect_prompt('x', [e])
    late = presets.new_effect('smoke_plume', 10, 0.7, 0.55, 'q', 55)
    assert not keys.prerolled(late) and 'untouched at' in prompts.effect_prompt('x', [late])
    f = presets.new_effect('fire', -30, 0.5, 0.6, 'f', 55)
    assert 'already burning' in prompts.effect_prompt('x', [f])
    w = presets.new_effect('sparks', -30, 0.5, 0.6, 's', 55)          # no pre-roll text: generic sentence, no negative times
    t = prompts.effect_prompt('x', [w]); assert '-1.' not in t


def test_server_refuses_cross_origin_and_foreign_hosts():
    from fastapi.testclient import TestClient
    from keyfx.server import app
    c = TestClient(app, base_url='http://127.0.0.1:8765')
    assert c.get('/api/health').status_code == 200
    assert c.post('/api/projects/x/render', json={'kind': 'effect'}, headers={'origin': 'https://evil.example'}).status_code == 403
    assert c.post('/api/projects/x/render', data={'kind': 'effect'}, headers={'origin': 'http://127.0.0.1:8765'}).status_code == 404
    assert c.get('/api/health', headers={'host': 'attacker.example'}).status_code == 403


def test_flash_canvas_prompt_mask_and_budget(tmp_path, monkeypatch):
    from keyfx import flashes, projects
    f = {'frame': 3, 'origin': {'x': 0.44, 'y': 0.52}, 'tip': {'x': 0.30, 'y': 0.50}}
    g = flashes.geometry(f)
    assert 260 < g['length_px'] < 280
    text = flashes.prompt(f)
    assert 'muzzle flash' in text and '2048 by 1152' in text and 'medium-sized' in text
    base = np.random.default_rng(0).integers(0, 255, (540, 960, 3), dtype=np.uint8)
    canvas = flashes.to_model_canvas(base)
    assert canvas.shape == (1152, 2048, 3) and canvas[:36].max() == 0
    back = flashes.from_model_canvas(canvas)
    assert back.shape == (540, 960, 3)
    m = flashes.region(f)
    assert m.shape == (540, 960, 1) and m[281, 355, 0] > 0.99 and m[20, 940, 0] == 0
    assert flashes.graph(text, 5)[flashes.BIND['model']]['inputs']['seed'] == 5
    monkeypatch.setenv('KEYFX_FLASH_CREDITS', '0')
    monkeypatch.setattr(projects, 'DATA', tmp_path)
    p = {'id': 'p1', 'frames': 5, 'mode': 'plate', 'environment': None, 'renders': [], 'flashes': [dict(f, id='a', seed=1, status='new')]}
    (tmp_path / 'projects' / 'p1').mkdir(parents=True); projects.save(p)
    import pytest
    with pytest.raises(ValueError, match='allowance'):
        projects.start_flashes('p1')


def test_flash_patch_cannot_inject_paths(tmp_path, monkeypatch):
    from keyfx import projects
    monkeypatch.setattr(projects, 'DATA', tmp_path)
    (tmp_path / 'projects' / 'p2').mkdir(parents=True)
    good = {'id': 'abc123', 'frame': 3, 'origin': {'x': .4, 'y': .5}, 'tip': {'x': .3, 'y': .5}, 'seed': 1, 'status': 'done',
            'result': 'flashes/abc123_1/frame.png', 'result_base': 'plate'}
    p = {'id': 'p2', 'frames': 10, 'mode': 'plate', 'environment': None, 'renders': [], 'flashes': [good]}
    projects.merge_flash_edits(p, [dict(good, result='../../../etc/passwd'), {'id': 'zzzzzz', 'frame': 1, 'result': 'x'}])
    assert p['flashes'] == [good]                                    # unknown id dropped, result not overwritten
    projects.merge_flash_edits(p, [dict(good, tip={'x': .2, 'y': .5})])
    assert p['flashes'][0]['result'] is None and p['flashes'][0]['status'] == 'new'
    import pytest
    with pytest.raises(ValueError):
        projects.stage_dir(p, 'render:../../x')


def test_location_prompt_uses_descriptions_without_grey_placeholders():
    from keyfx import location
    d = [{'text': 'steel drums', 'box': {'x0': .2, 'y0': .4, 'x1': .4, 'y1': .8}}, {'text': 'a drum just in front of the muzzle', 'box': None}]
    t = location.edit_prompt('a warehouse', d, 'overcast')
    assert 'grey rectangle' not in t and 'steel drums' in t and 'just in front of the muzzle' in t and 'original face, hair or headwear' in t
    assert 'Remove the people' in location.edit_prompt('a warehouse', [], '', keep_people=False)
    assert '20%' in t and '40%' in t
    assert location.describe_dressing(d)[1] == 'a drum just in front of the muzzle'
