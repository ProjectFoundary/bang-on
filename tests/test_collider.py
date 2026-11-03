"""Collider guides must use the current matte and cannot fall back to uncollided stamps."""
import copy
import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from keyfx import keys, people, presets, projects
from keyfx.server import app


@pytest.fixture
def shot(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, 'DATA', tmp_path)
    monkeypatch.setattr(projects.comfy, 'run_graph', lambda *a, **kw: pytest.fail('Unexpected paid inference'))
    p = {'id': 'collider', 'name': 'Collider', 'created': '2026-11-01', 'mode': 'plate', 'frames': 12,
         'source': {}, 'shot': 'A person passes through smoke.', 'renders': [], 'flashes': [],
         'effects': [presets.new_effect('smoke_white', 0, .5, .8, 'smoke', 12)],
         'settings': {'seed': 42, 'guide_mode': 'full', 'strength': 'exact', 'polish': False, 'collider': True},
         'people': {'selection': 'manual', 'mode': 'guide', 'points': [
             {'person': 0, 'frame': 0, 'label': 1, 'x': .4, 'y': .4}], 'feather': 0, 'trim': 0}}
    matte = {'id': 'matte1', 'kind': 'matte', 'status': 'done', 'mask_signature': people.mask_signature(p)}
    p['renders'].append(matte)
    d = projects.pdir(p['id']); (d / 'frames/plate').mkdir(parents=True)
    md = d / 'renders/matte1/masks'; md.mkdir(parents=True)
    for f in range(p['frames']):
        cv2.imwrite(str(d / 'frames/plate' / f'{f:05d}.png'), np.full((540, 960, 3), 90, np.uint8))
        mask = np.zeros((540, 960), np.uint8); mask[80:420, 340:440] = 255
        cv2.imwrite(str(md / f'{f:05d}.png'), mask)
    projects.save(p)
    return p


def layers_for(p):
    _, png = cv2.imencode('.png', np.zeros((540, 960, 4), np.uint8))
    return {f: {'fx': png.tobytes(), 'glow': png.tobytes()} for f, _ in keys.guide_frames(p['effects'], p['frames'], 'full')}


def test_matte_changes_invalidate_enabled_guides_only(shot):
    baseline = projects.effect_signature(shot)
    for change in ('id', 'feather', 'trim', 'points'):
        q = copy.deepcopy(shot)
        if change == 'id': q['renders'][0]['id'] = 'new-matte'
        elif change == 'points': q['people']['points'][0]['x'] = .6
        else: q['people'][change] = 1
        assert projects.effect_signature(q) != baseline
        q['settings'].pop('collider'); shot_off = copy.deepcopy(shot); shot_off['settings'].pop('collider')
        assert projects.effect_signature(q) == projects.effect_signature(shot_off)


def test_missing_matte_or_layers_fails_before_queueing(shot):
    for layers in (None, {}, {0: {'fx': b'not-a-complete-guide'}}):
        with pytest.raises(ValueError, match='all browser-rendered guide layers'):
            projects.start_render(shot['id'], 'effect', layers, _background=False)
    shot['people']['points'][0]['x'] = .6; projects.save(shot)
    with pytest.raises(ValueError, match='current people matte'):
        projects.start_render(shot['id'], 'keys', layers_for(shot), _background=False)
    assert len(projects.load(shot['id'])['renders']) == 1


def test_collider_recipe_is_frozen_and_layers_are_used(shot):
    version = json.dumps(['squib-moving-wall-v2', shot['id'], shot['frames'], 'matte1', True, 'manual', 0, 0])
    r = projects.start_render(shot['id'], 'keys', layers_for(shot), _background=False, collider_input=version)
    rd = projects.pdir(shot['id']) / 'renders' / r['id']
    request = json.loads((rd / 'request.json').read_text())
    assert request['render']['collider_recipe']['matte'] == 'matte1'
    assert r['collider'] is True
    result = projects._build_keys(shot, r, rd)
    assert [f for f, _, _ in result] == [f for f, _ in keys.guide_frames(shot['effects'], shot['frames'], 'full')]
    assert all(path.is_file() for _, path, _ in result)


def test_missing_uploaded_layer_never_falls_back_or_polishes(shot, monkeypatch):
    monkeypatch.setattr(projects.polish, 'polish_frame', lambda *a, **kw: pytest.fail('Should fail before any image edit'))
    r = {'collider': True, 'guide_mode': 'full', 'polish': True, 'id': 'incomplete', 'seed': 42}
    rd = projects.pdir(shot['id']) / 'renders/incomplete'; rd.mkdir()
    with pytest.raises(ValueError, match='missing'):
        projects._build_keys(shot, r, rd)
    (rd / 'layers').mkdir()
    for f, files in layers_for(shot).items():
        (rd / 'layers' / f'fx_{f:04d}.png').write_bytes(files['fx'])
    with pytest.raises(ValueError, match='incomplete'):
        projects._build_keys(shot, r, rd)


def test_mask_requests_refuse_changed_versions(shot):
    client = TestClient(app, base_url='http://127.0.0.1:8766')
    path = f"/api/projects/{shot['id']}/people/frame/0?view=mask"
    assert client.get(path + '&matte=matte1&feather=0&trim=0').status_code == 200
    for mismatch in ('matte=old', 'feather=1', 'trim=1'):
        assert client.get(path + '&' + mismatch).status_code == 409


def test_stale_captured_guides_are_rejected_before_submission(shot):
    for version in (None, 'bad-json', json.dumps([shot['id'], shot['frames'], 'old', True, 'manual', 0, 0])):
        with pytest.raises(ValueError, match='guide version|People matte changed'):
            projects.start_render(shot['id'], 'keys', layers_for(shot), _background=False, collider_input=version)
    assert len(projects.load(shot['id'])['renders']) == 1


@pytest.mark.parametrize('invalid', [1, 'collide', None, [], {}])
def test_invalid_mode_is_rejected(invalid):
    with pytest.raises(ValueError, match='on or off'):
        projects.effect_settings({'settings': {'collider': invalid}})
