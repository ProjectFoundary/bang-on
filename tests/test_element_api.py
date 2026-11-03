"""HTTP boundaries tested with local fixtures/fake jobs; no provider activity."""
import hashlib
import json

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keyfx import element_api, element_jobs, elements, geometry, projects


def png(im):
    ok, data = cv2.imencode('.png', im)
    assert ok
    return data.tobytes()


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, 'DATA', tmp_path)
    monkeypatch.setattr(projects.comfy, 'run_graph', lambda *a, **kw: pytest.fail('Unexpected paid job'))
    p = {'id': 'shot', 'name': 'Fixture', 'mode': 'plate', 'created': 'test', 'frames': 3,
         'effects': [], 'renders': [], 'flashes': [], 'element_takes': [],
         'geometry': geometry.defaults(), 'settings': {'pipeline': 'ltx_elements'},
         'native_profile': {'width': 1920, 'height': 1080, 'frames': 3, 'fps': 25,
                            'fps_rational': '25/1', 'timestamps': [0., .04, .091], 'duration': .131}}
    root = projects.pdir(p['id']); root.mkdir(parents=True)
    projects.save(p)
    track = root / 'track_plate.json'
    track.write_text(json.dumps({'n': 3, 'w': 480, 'h': 270,
                                 'camera': [[1, 0, 0, 0], [1, 0, 10, 2], [1, 0, 20, 4]]}))
    monkeypatch.setattr(projects, 'track_file', lambda _: track)
    app = FastAPI(); app.include_router(element_api.router)
    return TestClient(app), root


def test_geometry_import_patch_and_reference_relative_camera(api):
    client, root = api
    free_scene = client.get('/api/projects/shot/geometry/scene')
    assert free_scene.status_code == 200
    assert free_scene.json()['collisionMode'] == 'matte2d'
    depth = png(np.arange(12, dtype=np.uint16).reshape(3, 4) * 5000)
    response = client.post('/api/projects/shot/geometry/depth', data={'reference_frame': '1'},
                           files={'file': ('depth.png', depth, 'image/png')})
    assert response.status_code == 200, response.text
    response = client.post('/api/projects/shot/geometry/mattes', data={'name': 'Shelf', 'kind': 'hard'},
                           files={'file': ('shelf.png', png(np.full((256, 512), 65535, np.uint16)), 'image/png')})
    assert response.status_code == 200, response.text
    mid = response.json()['asset']['id']
    stored = projects.load('shot')['geometry']['mattes'][0]
    original = root / stored['directory'] / '00000.png'
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    response = client.patch('/api/projects/shot/geometry', json={'mattes': [{'id': mid, 'role': 'both', 'kind': 'soft', 'thickness': .4, 'softRadius': 12, 'drag': 30, 'depthOffset': -.1}]})
    assert response.status_code == 200, response.text
    scene = client.get('/api/projects/shot/geometry/scene').json()
    assert scene['width'] == 960 and scene['height'] == 540
    assert scene['sourceTimes'] == [0., .04, .091] and scene['fps'] == 25
    assert scene['referenceFrame'] == 1
    h = scene['camera']['homographies']
    assert h[1] == [1., 0., 0., 0., 1., 0., 0., 0., 1.]
    assert h[0][2] == -20 and h[2][2] == 20 and h[2][5] == 4
    assert scene['mattes'][0]['coordinates'] == 'reference'
    assert '{frame}' in scene['mattes'][0]['maskUrl']
    result = client.get(scene['mattes'][0]['maskUrl'].replace('{frame}', '2'))
    image = cv2.imdecode(np.frombuffer(result.content, np.uint8), cv2.IMREAD_UNCHANGED)
    assert result.status_code == 200 and image.dtype == np.uint8 and image.shape == (128, 256)
    assert np.all(image == 255)
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before, 'Preview modified original16-bit matte'


def test_geometry_cannot_replace_asset_paths_or_mutate_during_generation(api, monkeypatch):
    client, _ = api
    r = client.post('/api/projects/shot/geometry/mattes', files={'file': ('matte.png', png(np.ones((3, 4), np.uint8) * 255), 'image/png')})
    mid = r.json()['asset']['id']
    for body in [{'depth': {'file': '/private/file'}}, {'mattes': [{'id': mid, 'directory': '../../secret'}]},
                 {'mattes': [{'id': mid, 'drag': True}]}, {'reference_frame': True},
                 {'mattes': [{'id': 'missing', 'role': 'both'}]}]:
        assert client.patch('/api/projects/shot/geometry', json=body).status_code == 400
    p = projects.load('shot'); p['renders'].append({'id': 'running', 'kind': 'element', 'status': 'rendering'}); projects.save(p)
    monkeypatch.setattr(geometry, 'start_job', lambda *a, **kw: pytest.fail('Busy request started a model'))
    assert client.patch('/api/projects/shot/geometry', json={'depth_scale': 5}).status_code == 409
    assert client.post('/api/projects/shot/geometry/generate', json={'kind': 'depth'}).status_code == 409
    assert client.post('/api/projects/shot/elements/composite').status_code == 409


def test_static_matte_repeats_but_moving_matte_never_silently_clamps(api):
    client, root = api
    directory = root / 'geometry' / 'motion'; directory.mkdir(parents=True)
    for i in range(2):
        (directory / f'{i:05d}.png').write_bytes(png(np.full((3, 4), 100 + i, np.uint8)))
    p = projects.load('shot'); p['geometry']['mattes'] = [{'id': 'moving', 'directory': 'geometry/motion', 'frames': 2, 'static': False}]; projects.save(p)
    assert client.get('/api/projects/shot/geometry/mattes/moving/frames/1.png').status_code == 200
    assert client.get('/api/projects/shot/geometry/mattes/moving/frames/2.png').status_code == 404
    assert client.get('/api/projects/shot/geometry/mattes/moving/frames/-1.png').status_code == 404
    assert client.get('/api/projects/shot/geometry/mattes/moving/frames/0.png?max_edge=0').status_code == 422


def test_prepare_validates_all_fields_and_does_not_leak_disk_paths(api, monkeypatch):
    client, root = api
    monkeypatch.setattr(element_jobs, 'plan', lambda p, eid: {'width': 4, 'height': 3, 'active_frames': 2, 'signature': 'current'})
    calls = []
    def prepare(pid, eid, meta, images, visibility):
        calls.append((meta, images, visibility))
        return {'id': 'take', 'effect_id': eid, 'status': 'prepared', 'take_dir': str(root / 'private'), 'inputs': '/private/input'}
    monkeypatch.setattr(element_jobs, 'prepare_upload', prepare)
    payload = png(np.ones((3, 4, 3), np.uint8))
    files = [(f'{name}_{i:04d}.png', ('frame.png', payload, 'image/png')) for name in ('guide', 'visibility') for i in range(2)]
    url = '/api/projects/shot/elements/smoke/prepare'
    r = client.post(url, data={'metadata': json.dumps({'signature': 'current'})}, files=files)
    assert r.status_code == 200 and len(calls) == 1
    assert 'take_dir' not in r.json() and str(root) not in r.text
    for invalid in [files[:-1], files + files[:1], [('guide_9999.png', ('frame.png', payload, 'image/png'))],
                    [('guide_0000.png', ('../../evil.png', payload, 'image/png'))]]:
        assert client.post(url, data={'metadata': '{}'}, files=invalid).status_code == 400
    assert len(calls) == 1


def test_capabilities_are_explicit_and_geometry_jobs_are_strict(api, monkeypatch):
    client, _ = api
    checks, jobs = [], []
    monkeypatch.setattr(elements, 'fetch_capabilities', lambda r: checks.append(r) or {'ready': False, 'checks': []})
    monkeypatch.setattr(geometry, 'start_job', lambda pid, kind, **kw: jobs.append((kind, kw)) or {'id': 'job', 'status': 'queued'})
    assert checks == [] and jobs == []
    assert client.get('/api/projects/shot/elements/capabilities').status_code == 200
    assert len(checks) == 1
    assert client.post('/api/projects/shot/geometry/generate', json={'kind': 'depth', 'extra': 'bad'}).status_code == 400
    assert client.post('/api/projects/shot/geometry/generate', json={'kind': 'unknown'}).status_code == 400
    assert client.post('/api/projects/shot/geometry/generate', json={'kind': 'depth', 'reference_frame': True}).status_code == 400
    assert client.post('/api/projects/shot/geometry/generate', json={'kind': 'depth', 'reference_frame': 3}).status_code == 400
    assert jobs == []
    assert client.post('/api/projects/shot/geometry/generate', json={'kind': 'depth'}).status_code == 200
    assert jobs == [('depth', {'points': None, 'name': 'Object', 'soft': False})]
    assert client.post('/api/projects/shot/geometry/generate', json={'kind': 'depth', 'reference_frame': 2}).status_code == 200
    assert jobs[-1][1]['reference_frame'] == 2


def test_asset_serving_is_project_bound_and_public_records_have_urls(api, tmp_path,monkeypatch):
    client, root = api
    raw = root / 'elements' / 'test.mp4'; raw.parent.mkdir(); raw.write_bytes(b'test-video')
    outside = tmp_path / 'outside.mp4'; outside.write_bytes(b'not-project-data')
    p = projects.load('shot'); p['element_takes'] = [{'id': 'take', 'effect_id': 'gone', 'generation_signature': 'old', 'status': 'done',
                                                   'take_dir': str(root / 'private'), 'assets': {'rgb': {'path': str(raw)}, 'alpha': {'path': str(outside)}}}]; projects.save(p)
    # Isolate HTTP path/public-field checks; lifecycle tests use real frozen
    # inputs and verify that stage serving rejects tampered decoded assets.
    (root/'private').mkdir()
    monkeypatch.setattr(elements,'inspect',lambda td:{'assets':p['element_takes'][0]['assets']})
    monkeypatch.setattr(element_jobs,'validated_stage',lambda project_root,row,stage:row['assets'][stage])
    listing = client.get('/api/projects/shot/elements')
    assert str(root) not in listing.text and str(outside) not in listing.text
    assets = listing.json()['takes'][0]['assets']
    assert assets['rgb']['url'].endswith('/take/rgb.mp4')
    assert client.get(assets['rgb']['url']).content == b'test-video'
    assert client.get(assets['alpha']['url']).status_code == 404
    assert client.get('/api/projects/shot/element-takes/take/secret.mp4').status_code == 404


def test_prepared_guide_is_reviewable_without_starting_models(api):
    client, root = api
    directory = root / 'element_inputs' / 'input'; directory.mkdir(parents=True)
    (directory / 'guide.mp4').write_bytes(b'prepared-blurred-guide')
    p = projects.load('shot'); p['element_takes'] = [{'id': 'take', 'effect_id': 'gone', 'status': 'prepared', 'inputs': 'element_inputs/input'}]; projects.save(p)
    take = client.get('/api/projects/shot/elements').json()['takes'][0]
    assert client.get(take['guide_url']).content == b'prepared-blurred-guide'
    assert client.get('/api/projects/shot/element-takes/take/rgb.mp4').status_code == 404
    p['element_takes'][0]['inputs'] = '../../outside'; projects.save(p)
    assert client.get(take['guide_url']).status_code == 404


def test_bad_matte_zip_fails_cleanly_without_registering_asset(api):
    client, _ = api
    r = client.post('/api/projects/shot/geometry/mattes', files={'file': ('bad.zip', b'notzip', 'application/zip')})
    assert r.status_code == 400
    assert projects.load('shot')['geometry']['mattes'] == []
