"""Local depth picking and immutable scene geometry; no provider activity."""
import base64
import copy
import hashlib

import cv2
import numpy as np
import pytest

from keyfx import depth_selection, element_jobs, geometry, presets, projects
from test_element_api import api, png


def shelf():
    depth = np.full((16, 20), 60000, np.uint16)
    mask = np.zeros(depth.shape, bool)
    mask[3:14, 3] = True; mask[3:14, 12] = True
    mask[5, 3:13] = True; mask[10, 3:13] = True
    depth[mask] = 20000
    depth[2:5, 16:19] = 20000  # Same depth, disconnected object.
    return depth, mask


def import_depth(client, depth=None, frame=0):
    depth = shelf()[0] if depth is None else depth
    r = client.post('/api/projects/shot/geometry/depth', data={'reference_frame': str(frame)},
                    files={'file': ('depth.png', png(depth), 'image/png')})
    assert r.status_code == 200, r.text
    return r.json()['asset']


def preview(client, **kwargs):
    r = client.post('/api/projects/shot/geometry/depth-selection/preview',
                    json={'points': [{'x': .175, 'y': .4}], 'tolerance': .01, **kwargs})
    assert r.status_code == 200, r.text
    return r.json()


def decode(url):
    return cv2.imdecode(np.frombuffer(base64.b64decode(url.split(',', 1)[1]), np.uint8), cv2.IMREAD_UNCHANGED)


def save_body(view, **kwargs):
    return {k: view[k] for k in ('points', 'tolerance', 'bounds', 'selection_key')} | kwargs


def test_connected_shelf_selection_preserves_gaps_and_excludes_disconnected_depth_island(api):
    client, root = api
    _, expected = shelf()
    entry = import_depth(client)
    before = (root/entry.get('file', projects.load('shot')['geometry']['depth']['file'])).read_bytes()
    view = preview(client)
    selected = decode(view['mask_url'])
    assert np.array_equal(selected, np.uint8(expected)*255)
    assert selected[7, 7] == 0 and selected[3, 17] == 0
    assert view['pixels'] == np.count_nonzero(expected)
    assert view['coverage'] == pytest.approx(expected.mean())
    overlay = decode(view['overlay_url'])
    assert overlay.shape == (*expected.shape, 4)
    assert np.all(overlay[~expected, 3] == 0)
    p = projects.load('shot')
    assert p['geometry']['mattes'] == []
    assert (root/p['geometry']['depth']['file']).read_bytes() == before
    assert p['renders'] == []


def test_shift_seed_union_selects_disconnected_same_depth_objects(api):
    client, _ = api
    import_depth(client)
    view = preview(client, points=[{'x': .175, 'y': .4}, {'x': .875, 'y': .2}])
    expected = shelf()[1]; expected[2:5, 16:19] = True
    assert np.array_equal(decode(view['mask_url']), np.uint8(expected)*255)


def test_tolerance_is_absolute_to_seed_not_a_flood_across_gradual_depth_drift(api):
    client, _ = api
    depth = np.tile(np.arange(20000, 28000, 1000, dtype=np.uint16), (3, 1))
    import_depth(client, depth)
    narrow = decode(preview(client, points=[{'x': .01, 'y': .5}], tolerance=.03)['mask_url'])
    wide = decode(preview(client, points=[{'x': .01, 'y': .5}], tolerance=.08)['mask_url'])
    assert np.all(narrow[:, :2] == 255) and np.all(narrow[:, 2:] == 0)
    assert np.all(wide[:, :6] == 255) and np.all(wide[:, 6:] == 0)


def test_region_bounds_clip_connected_selection_and_reject_outside_seeds(api):
    client, _ = api
    import_depth(client)
    bounds = [0., 0., .5, 1.]
    view = preview(client, bounds=bounds)
    expected = shelf()[1]; expected[:, 10:] = False
    assert np.array_equal(decode(view['mask_url']), np.uint8(expected)*255)
    r = client.post('/api/projects/shot/geometry/depth-selection/preview',
                    json={'points': [{'x': .175, 'y': .4}], 'bounds': [.5, 0, 1, 1]})
    assert r.status_code == 400 and 'inside' in r.text


@pytest.mark.parametrize('patch', [
    {'points': []}, {'points': [{'x': True, 'y': .5}]}, {'points': [{'x': -.1, 'y': .5}]},
    {'points': [{'x': .5, 'y': .5, 'label': 1}]}, {'points': [{'x': .5, 'y': .5}]*33},
    {'tolerance': True}, {'tolerance': .51}, {'bounds': [0, 0, 0, 1]},
    {'bounds': [-.1, 0, 1, 1]}, {'bounds': [0, 0, 1]}, {'extra': 'unsupported'},
])
def test_invalid_selection_settings_are_rejected_without_scene_mutation(api, patch):
    client, _ = api
    import_depth(client)
    before = copy.deepcopy(projects.load('shot'))
    r = client.post('/api/projects/shot/geometry/depth-selection/preview',
                    json={'points': [{'x': .175, 'y': .4}], **patch})
    assert r.status_code == 400, r.text
    assert projects.load('shot') == before


def test_save_requires_exact_preview_key_and_current_reference_depth(api):
    client, _ = api
    import_depth(client)
    view = preview(client)
    r = client.post('/api/projects/shot/geometry/depth-selection', json=save_body(view, selection_key='stale'))
    assert r.status_code == 409
    assert projects.load('shot')['geometry']['mattes'] == []
    assert client.patch('/api/projects/shot/geometry', json={'reference_frame': 1}).status_code == 200
    r = client.post('/api/projects/shot/geometry/depth-selection', json=save_body(view))
    assert r.status_code == 400 and 'current reference' in r.text
    assert projects.load('shot')['geometry']['mattes'] == []
    import_depth(client, frame=1)
    r = client.post('/api/projects/shot/geometry/depth-selection', json=save_body(view))
    assert r.status_code == 409
    fresh = preview(client)
    assert fresh['selection_key'] != view['selection_key']
    assert fresh['reference_frame'] == 1


def test_reimported_depth_invalidates_old_preview_even_with_same_pixels(api):
    client, _ = api
    import_depth(client)
    view = preview(client)
    import_depth(client)
    r = client.post('/api/projects/shot/geometry/depth-selection', json=save_body(view))
    assert r.status_code == 409
    assert projects.load('shot')['geometry']['mattes'] == []


def test_depth_bytes_are_immutable_even_when_tamper_does_not_change_picked_mask(api):
    client, root = api
    import_depth(client)
    view = preview(client)
    p = projects.load('shot'); entry = p['geometry']['depth']
    path = root/entry['file']
    depth = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    depth[0, 0] = 59000  # Away from selection; same mask would otherwise hide tampering.
    cv2.imwrite(str(path), depth)
    assert hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']
    r = client.post('/api/projects/shot/geometry/depth-selection/preview',
                    json={'points': view['points'], 'tolerance': view['tolerance']})
    assert r.status_code == 400 and 'depth asset changed' in r.text
    r = client.post('/api/projects/shot/geometry/depth-selection', json=save_body(view))
    assert r.status_code == 400 and 'depth asset changed' in r.text
    assert projects.load('shot')['geometry']['mattes'] == []


def test_save_adds_static_holdout_and_assigns_only_selected_element(api):
    client, root = api
    import_depth(client)
    p = projects.load('shot')
    p['effects'] = [presets.new_effect('explosion', 0, .5, .5, 'blast', p['frames']),
                    presets.new_effect('smoke_grenade', 0, .3, .7, 'smoke', p['frames'])]
    projects.save(p)
    view = preview(client)
    r = client.post('/api/projects/shot/geometry/depth-selection',
                    json=save_body(view, name='Shelf', kind='hard', role='holdout', effect_id='blast'))
    assert r.status_code == 200, r.text
    asset = r.json()['asset']
    assert 'directory' not in asset
    assert asset['static'] and asset['role'] == 'holdout' and asset['reference_frame'] == 0
    assert asset['selection']['selection_key'] == view['selection_key']
    assert asset['colliderBlur'] == 0
    p = projects.load('shot'); matte = p['geometry']['mattes'][0]
    assert np.array_equal(cv2.imread(str(root/matte['directory']/'00000.png'), 0), decode(view['mask_url']))
    assert element_jobs.effect_config(p, p['effects'][0])['colliders'] == [asset['id']]
    assert element_jobs.effect_config(p, p['effects'][1])['colliders'] == []
    assert p['renders'] == []
    invalid = client.post('/api/projects/shot/geometry/depth-selection', json=save_body(view, effect_id='missing'))
    assert invalid.status_code == 400
    assert len(projects.load('shot')['geometry']['mattes']) == 1


def test_collider_blur_is_atomic_persistent_and_sent_to_scene_without_mutating_matte(api):
    client, root = api
    import_depth(client)
    view = preview(client)
    saved = client.post('/api/projects/shot/geometry/depth-selection', json=save_body(view))
    assert saved.status_code == 200
    mid = saved.json()['asset']['id']
    p = projects.load('shot'); matte = p['geometry']['mattes'][0]
    path = root/matte['directory']/'00000.png'; original = path.read_bytes()
    r = client.patch('/api/projects/shot/geometry', json={'mattes': [{'id': mid, 'colliderBlur': 13.5}]})
    assert r.status_code == 200 and r.json()['mattes'][0]['colliderBlur'] == 13.5
    scene = client.get('/api/projects/shot/geometry/scene')
    assert scene.status_code == 200, scene.text
    assert scene.json()['mattes'][0]['colliderBlur'] == 13.5
    before = projects.load('shot')
    for bad in [True, -1, 129, '10']:
        r = client.patch('/api/projects/shot/geometry', json={'depth_scale': 12, 'mattes': [{'id': mid, 'colliderBlur': bad}]})
        assert r.status_code == 400
        assert projects.load('shot') == before, 'Invalid blur patch partially changed calibration'
    assert path.read_bytes() == original


def test_depth_pick_save_is_busy_guarded_and_preview_is_read_only(api):
    client, _ = api
    import_depth(client)
    view = preview(client)
    p = projects.load('shot'); p['renders'].append({'id': 'busy', 'kind': 'element', 'status': 'queued'}); projects.save(p)
    assert preview(client)['selection_key'] == view['selection_key']
    r = client.post('/api/projects/shot/geometry/depth-selection', json=save_body(view))
    assert r.status_code == 409 and projects.load('shot')['geometry']['mattes'] == []


def test_take_approval_changes_flattened_signature_but_not_generation_signature(api):
    _, _ = api
    p = projects.load('shot')
    p['effects'] = [presets.new_effect('explosion', 0, .5, .5, 'blast', p['frames'])]
    old_flat = projects.effect_signature(p)
    old_element = element_jobs.generation_signature(p, p['effects'][0])
    p['element_approvals'] = {'blast': 'reviewed-take'}
    assert projects.effect_signature(p) != old_flat
    assert element_jobs.generation_signature(p, p['effects'][0]) == old_element
