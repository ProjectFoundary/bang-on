"""Matte-only collision remains usable without depth or a configured provider."""
import copy

import cv2
import numpy as np
import pytest

from keyfx import connections, element_jobs, geometry, people, presets, projects
from test_element_api import api, png


def depth(client):
    raw = np.arange(12, dtype=np.uint16).reshape(3, 4)*5000
    r = client.post('/api/projects/shot/geometry/depth',
                    files={'file': ('depth.png', png(raw), 'image/png')})
    assert r.status_code == 200, r.text
    return r.json()['asset']


def test_missing_depth_defaults_to_matte_2d_while_existing_depth_defaults_to_3d():
    assert geometry.validate({}, 3)['collision_mode'] == 'matte2d'
    assert geometry.validate({'depth': {'id': 'legacy-depth'}}, 3)['collision_mode'] == 'depth3d'
    assert geometry.validate({'depth': {'id': 'legacy-depth'}, 'collision_mode': 'matte2d'}, 3)['collision_mode'] == 'matte2d'


def test_no_depth_scene_uses_flat_matte_geometry_and_preserves_camera_tracking(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(connections, 'current', lambda: pytest.fail('Matte-only scene read Comfy credentials'))
    r = client.get('/api/projects/shot/geometry/scene')
    assert r.status_code == 200, r.text
    scene = r.json()
    assert scene['collisionMode'] == 'matte2d'
    assert scene['depth']['width'] == scene['depth']['height'] == 1
    assert len(scene['depth']['values']) == 1
    assert np.isfinite(scene['depth']['values'][0])
    assert scene['sourceTimes'] == [0., .04, .091]
    assert len(scene['camera']['homographies']) == 3
    assert scene['camera']['homographies'][0] == [1., 0., 0., 0., 1., 0., 0., 0., 1.]
    assert scene['camera']['homographies'][1][2] == 20
    assert scene['camera']['homographies'][2][2] == 40
    assert scene['mattes'] == []
    assert projects.load('shot')['geometry'].get('depth') is None
    assert projects.load('shot')['renders'] == []


def test_explicit_depth_3d_without_depth_refuses_scene_with_actionable_error(api):
    client, _ = api
    r = client.patch('/api/projects/shot/geometry', json={'collision_mode': 'depth3d'})
    assert r.status_code == 200, r.text
    scene = client.get('/api/projects/shot/geometry/scene')
    assert scene.status_code == 400
    assert 'depth' in scene.text.lower()
    assert projects.load('shot')['geometry']['collision_mode'] == 'depth3d'
    assert projects.load('shot')['renders'] == []


def test_existing_depth_shot_retains_3d_mode_and_explicit_2d_does_not_delete_depth(api):
    client, root = api
    entry = depth(client)
    p = projects.load('shot'); p['geometry'].pop('collision_mode', None); projects.save(p)
    legacy = client.get('/api/projects/shot/geometry')
    assert legacy.status_code == 200 and legacy.json()['collision_mode'] == 'depth3d'
    scene = client.get('/api/projects/shot/geometry/scene')
    assert scene.status_code == 200 and scene.json()['collisionMode'] == 'depth3d'
    original = root/projects.load('shot')['geometry']['depth']['file']
    original_bytes = original.read_bytes()
    r = client.patch('/api/projects/shot/geometry', json={'collision_mode': 'matte2d'})
    assert r.status_code == 200 and r.json()['collision_mode'] == 'matte2d'
    assert r.json()['depth']['id'] == entry['id']
    scene = client.get('/api/projects/shot/geometry/scene')
    assert scene.status_code == 200 and scene.json()['collisionMode'] == 'matte2d'
    assert original.read_bytes() == original_bytes


def test_mode_change_invalidates_scene_and_element_cache_and_bad_mode_is_atomic(api):
    client, _ = api
    depth(client)
    p = projects.load('shot')
    p['effects'] = [presets.new_effect('explosion', 0, .5, .5, 'blast', p['frames'])]
    projects.save(p)
    scene3d = client.get('/api/projects/shot/geometry/scene').json()
    sig3d = element_jobs.generation_signature(p, p['effects'][0])
    r = client.patch('/api/projects/shot/geometry', json={'collision_mode': 'matte2d'})
    assert r.status_code == 200, r.text
    scene2d = client.get('/api/projects/shot/geometry/scene').json()
    p = projects.load('shot')
    assert scene2d['key'] != scene3d['key']
    assert element_jobs.generation_signature(p, p['effects'][0]) != sig3d
    before = copy.deepcopy(p)
    for invalid in ['2d', 'depth', '', True, 1, None, []]:
        r = client.patch('/api/projects/shot/geometry', json={'collision_mode': invalid, 'depth_scale': 12})
        assert r.status_code == 400, r.text
        assert projects.load('shot') == before


def test_standalone_matte_import_assignment_and_scene_work_without_depth(api, monkeypatch):
    client, root = api
    mask = np.zeros((8, 12), np.uint8); mask[2:6, 3:9] = 255
    r = client.post('/api/projects/shot/geometry/mattes', data={'name': 'Shelf', 'kind': 'hard'},
                    files={'file': ('shelf.png', png(mask), 'image/png')})
    assert r.status_code == 200, r.text
    mid = r.json()['asset']['id']
    p = projects.load('shot')
    assert p['geometry'].get('depth') is None
    e = presets.new_effect('explosion', 0, .5, .5, 'blast', p['frames'])
    e['element'] = {**element_jobs.DEFAULT, 'colliders': [mid]}
    p['effects'] = [e]; projects.save(p)
    assert element_jobs.effect_config(p, e)['colliders'] == [mid]
    monkeypatch.setattr(geometry, 'depth_data', lambda *a, **kw: pytest.fail('Matte2d requested a depth pass'))
    monkeypatch.setattr(connections, 'current', lambda: pytest.fail('Standalone matte used Comfy credentials'))
    r = client.get('/api/projects/shot/geometry/scene')
    assert r.status_code == 200, r.text
    scene = r.json()
    assert scene['collisionMode'] == 'matte2d'
    assert [m['id'] for m in scene['mattes']] == [mid]
    assert scene['mattes'][0]['role'] == 'both'
    url = scene['mattes'][0]['maskUrl'].replace('{frame}', '2')
    preview = client.get(url)
    assert preview.status_code == 200
    assert np.array_equal(cv2.imdecode(np.frombuffer(preview.content, np.uint8), 0), mask)
    assert projects.load('shot')['renders'] == []


def test_explicit_2d_ignores_stale_depth_reference_for_matte_simulation(api, monkeypatch):
    client, _ = api
    depth(client)
    assert client.patch('/api/projects/shot/geometry', json={'reference_frame': 1, 'collision_mode': 'matte2d'}).status_code == 200
    monkeypatch.setattr(geometry, 'depth_data', lambda *a, **kw: pytest.fail('Matte2d decoded stale reference depth'))
    scene = client.get('/api/projects/shot/geometry/scene')
    assert scene.status_code == 200, scene.text
    assert scene.json()['collisionMode'] == 'matte2d'
    assert scene.json()['referenceFrame'] == 1


def test_unconfigured_depth_generation_fails_before_job_or_pixels_are_submitted(api, monkeypatch):
    client, root = api
    monkeypatch.setattr(connections, 'current', lambda: connections.Connection(enabled=False))
    monkeypatch.setattr(connections, '_get_json', lambda *a, **kw: pytest.fail('Unconfigured depth checked remote models'))
    monkeypatch.setattr(geometry, 'depth_graph', lambda *a, **kw: pytest.fail('Unconfigured depth prepared a model graph'))
    before = projects.load('shot')
    r = client.post('/api/projects/shot/geometry/generate', json={'kind': 'depth', 'reference_frame': 0})
    assert r.status_code == 400 and 'Connect Comfy' in r.text
    assert projects.load('shot') == before


def tracked_people(root):
    p = projects.load('shot')
    p['people'] = {'selection': 'manual', 'points': [{'frame': 0, 'person': 0, 'label': 1, 'x': .5, 'y': .5}]}
    p['effects'] = [presets.new_effect('explosion', 0, .5, .5, 'blast', p['frames']),
                    presets.new_effect('smoke_grenade', 0, .5, .5, 'smoke', p['frames'])]
    row = {'id': 'people-current', 'kind': 'matte', 'status': 'done', 'mask_signature': people.mask_signature(p)}
    p['renders'].append(row); projects.save(p)
    masks = root/'renders'/row['id']/'masks'; masks.mkdir(parents=True)
    originals = []
    for frame in range(p['frames']):
        mask = np.zeros((8, 12), np.uint8); mask[2:7, 3+frame:6+frame] = 255
        raw = png(mask); (masks/f'{frame:05d}.png').write_bytes(raw); originals.append(raw)
    return originals, masks


def test_existing_people_matte_reuse_is_local_immutable_idempotent_and_element_specific(api, monkeypatch):
    client, root = api
    originals, masks = tracked_people(root)
    monkeypatch.setattr(connections, 'current', lambda: pytest.fail('Cached matte reuse read Cloud credentials'))
    monkeypatch.setattr(geometry, 'start_job', lambda *a, **kw: pytest.fail('Cached matte reuse generated another matte'))
    before = copy.deepcopy(projects.load('shot')['renders'])
    r = client.post('/api/projects/shot/geometry/use-people-matte', json={'effect_id': 'blast'})
    assert r.status_code == 200, r.text
    asset = r.json()['asset']; mid = asset['id']
    assert 'directory' not in asset
    assert asset['source_people_matte'] == 'people-current'
    assert asset['kind'] == 'soft' and asset['role'] == 'both' and asset['colliderBlur'] == 8
    assert asset['static'] is False and asset['frames'] == 3
    p = projects.load('shot'); saved = p['geometry']['mattes'][0]
    assert p['renders'] == before and p['geometry'].get('depth') is None
    assert p['effects'][0]['element']['colliders'] == [mid]
    assert element_jobs.effect_config(p, p['effects'][1])['colliders'] == []
    copied = root/saved['directory']
    for frame, raw in enumerate(originals):
        original = cv2.imdecode(np.frombuffer(raw, np.uint8), 0)
        assert np.array_equal(cv2.imread(str(copied/f'{frame:05d}.png'), 0), original)
        assert (masks/f'{frame:05d}.png').read_bytes() == raw
    again = client.post('/api/projects/shot/geometry/use-people-matte', json={'effect_id': 'blast'})
    assert again.status_code == 200 and again.json()['asset']['id'] == mid
    p = projects.load('shot')
    assert len(p['geometry']['mattes']) == 1 and p['effects'][0]['element']['colliders'] == [mid]
    assert client.get('/api/projects/shot/geometry/scene').status_code == 200


def test_cached_people_matte_reuse_refuses_missing_stale_unsafe_or_busy_inputs(api):
    client, root = api
    route = '/api/projects/shot/geometry/use-people-matte'
    assert client.post(route, json={}).status_code == 400
    _, masks = tracked_people(root)
    assert client.post(route, json={'effect_id': 'missing'}).status_code == 400
    assert client.post(route, json={'arbitrary': True}).status_code == 400
    (masks/'00001.png').unlink()
    assert client.post(route, json={}).status_code == 404
    assert projects.load('shot')['geometry']['mattes'] == []
    p = projects.load('shot'); p['renders'][-1]['id'] = '../../outside'; projects.save(p)
    assert client.post(route, json={}).status_code == 404
    p = projects.load('shot'); p['renders'][-1]['id'] = 'people-current'
    p['people']['points'][0]['x'] = .4; projects.save(p)
    assert client.post(route, json={}).status_code == 400
    p['renders'].append({'id': 'busy', 'kind': 'element', 'status': 'queued'}); projects.save(p)
    assert client.post(route, json={}).status_code == 409
    assert projects.load('shot')['geometry']['mattes'] == []


def test_boundary_offset_roundtrips_without_changing_original_mask(api):
    client,root=api
    response=client.post('/api/projects/shot/geometry/mattes',files={'file':('mask.png',png(np.full((8,12),255,np.uint8)),'image/png')})
    mid=response.json()['asset']['id'];stored=projects.load('shot')['geometry']['mattes'][0]
    original=root/stored['directory']/'00000.png';before=original.read_bytes()
    response=client.patch('/api/projects/shot/geometry',json={'mattes':[{'id':mid,'colliderDisplacement':-12,'colliderBlur':32,'drag':24,'softRadius':8,'depthOffset':0}]})
    assert response.status_code==200,response.text
    assert response.json()['mattes'][0]['colliderDisplacement']==-12
    assert type(projects.load('shot')['geometry']['mattes'][0]['drag']) is type(stored['drag'])
    scene=client.get('/api/projects/shot/geometry/scene').json()
    assert scene['mattes'][0]['colliderDisplacement']==-12
    assert scene['mattes'][0]['colliderBlur']==32
    for value in [-129,129,True]:
        response=client.patch('/api/projects/shot/geometry',json={'mattes':[{'id':mid,'colliderDisplacement':value}]})
        assert response.status_code==400
        assert projects.load('shot')['geometry']['mattes'][0]['colliderDisplacement']==-12
    assert original.read_bytes()==before
