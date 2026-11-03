"""Offline HTTP and attachment QA; only the explicit PNG model executor is fake."""
import copy
import io
import json
import zipfile

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keyfx import connections, element_api, element_jobs, elements, geometry, native_media, people_alpha, people_alpha_api, presets, projects
from test_element_pipeline import pipeline, png
from test_people_alpha import alpha_catalog, execute


def client_for(p, monkeypatch, calls):
    monkeypatch.setattr(connections, 'node_catalog', lambda _: alpha_catalog(projects.load(p['id'])))
    provider = execute(calls)
    monkeypatch.setattr(elements, '_execute_with_client', lambda cfg, **kw: provider(**kw))
    app = FastAPI(); app.include_router(element_api.router); app.include_router(people_alpha_api.router)
    return TestClient(app)


def completed(client):
    prep = client.post('/api/projects/shot/people-alpha/prepare', json={})
    assert prep.status_code == 200, prep.text
    tid = prep.json()['id']
    assert client.get(f'/api/projects/shot/people-alpha/{tid}/source.mp4').status_code == 200
    assert client.get(f'/api/projects/shot/people-alpha/{tid}/alpha.mp4').status_code == 404
    start = client.post(f'/api/projects/shot/people-alpha/{tid}/generate', json={})
    assert start.status_code == 200, start.text
    assert projects.load('shot')['people_alpha_takes'][0]['status'] == 'done'
    return tid


def tracked_matte(root, n):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for frame in range(n):
            image = np.zeros((32, 18), np.uint8); image[8:24, 7:10] = 255
            archive.writestr(f'{frame:05d}.png', png(image))
    matte = geometry.import_matte(root, stream.getvalue(), 'person.zip', n, name='SAM2 actor', soft=True)
    matte['colliderBlur'] = 8
    return matte


@pytest.mark.parametrize('bits', [8, 16])
def test_http_prepare_png_generate_review_and_optical_only_approval_preserve_native_plate(pipeline, monkeypatch, bits):
    create, _, _ = pipeline; p, root, originals = create(fps='60/1')
    if bits == 8:
        p['native_profile'].update(storage_bit_depth=8, bit_depth=8)
        for f, image in enumerate(originals): native_media.write_native_frame(root/'native/frames', f, np.uint8(np.floor(image/257+.5)))
    smoke = presets.new_effect('smoke_plume', 2, .4, .4, 'smoke', p['frames']); p['effects'].append(smoke)
    projects.save(p)
    before = {str(f.relative_to(root)): f.read_bytes() for d in ('native/frames', 'frames/plate') for f in (root/d).glob('*.png')}
    calls = []; client = client_for(p, monkeypatch, calls)
    assert client.get('/api/projects/shot/people-alpha/plan').json()['fps'] == 24
    assert client.get('/api/projects/shot/people-alpha/capabilities').json()['ready'] is True
    assert client.post('/api/projects/shot/people-alpha/prepare', json={'range': [0, 2]}).status_code == 400
    tid = completed(client)
    for suffix in ('alpha.mp4', 'frames/3.png', 'review/3.png?view=overlay', 'review/3.png?view=checker', 'review/3.png?view=alpha'):
        response = client.get(f'/api/projects/shot/people-alpha/{tid}/{suffix}')
        assert response.status_code == 200, response.text
        if '.png' in suffix:
            image = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_UNCHANGED)
            assert image.shape[:2] == (32, 18)
    endpoint = f'/api/projects/shot/people-alpha/{tid}/approve'
    for body in ({}, {'reviewed': 'true'}, {'reviewed': True, 'effect_id': 'missing'}):
        assert client.post(endpoint, json=body).status_code == 400
    approved = client.post(endpoint, json={'reviewed': True, 'effect_id': 'blast'})
    assert approved.status_code == 200, approved.text
    mid = approved.json()['matte_id']; current = projects.load('shot'); matte = current['geometry']['mattes'][0]
    assert matte['role'] == 'holdout' and matte['holdoutPost'] and matte['holdoutBaseline'] == 'none'
    assert matte['holdout']['take_id'] == tid and matte['holdout']['width'] == 18 and matte['holdout']['height'] == 32
    assert current['effects'][0]['element']['colliders'] == [mid]
    assert current['effects'][1].get('element', {}).get('colliders', []) == []
    repeated = client.post(endpoint, json={'reviewed': True, 'effect_id': 'blast'})
    assert repeated.status_code == 200 and repeated.json()['matte_id'] == mid
    assert len(projects.load('shot')['geometry']['mattes']) == 1 and len(calls) == 1
    assert client.post(f'/api/projects/shot/geometry/mattes/{mid}/holdout', json={'mode': 'original'}).status_code == 400
    assert {str(f.relative_to(root)): f.read_bytes() for d in ('native/frames', 'frames/plate') for f in (root/d).glob('*.png')} == before


def test_approval_attaches_to_tracked_person_without_mutating_physical_mask_and_reverts(pipeline, monkeypatch):
    create, _, _ = pipeline; p, root, _ = create(); matte = tracked_matte(root, p['frames'])
    original = copy.deepcopy(matte); physical = {f.name: f.read_bytes() for f in (root/matte['directory']).glob('*.png')}
    p['geometry']['mattes'] = [matte]; projects.save(p)
    calls = []; client = client_for(p, monkeypatch, calls); tid = completed(client)
    response = client.post(f'/api/projects/shot/people-alpha/{tid}/approve', json={'reviewed': True, 'matte_id': matte['id'], 'effect_id': 'blast'})
    assert response.status_code == 200, response.text
    stored = projects.load('shot')['geometry']['mattes'][0]
    assert all(stored[k] == v for k, v in original.items())
    assert stored['holdoutBaseline'] == 'original' and stored['holdoutPost'] and stored['holdout']['take_id'] == tid
    restored = client.post(f'/api/projects/shot/geometry/mattes/{matte["id"]}/holdout', json={'mode': 'original'})
    assert restored.status_code == 200, restored.text
    stored = projects.load('shot')['geometry']['mattes'][0]
    assert 'holdout' not in stored and stored['holdoutPost']
    assert all(stored[k] == v for k, v in original.items())
    assert {f.name: f.read_bytes() for f in (root/matte['directory']).glob('*.png')} == physical


def test_approval_enables_optical_holdout_on_collision_only_person_without_changing_model_signature(pipeline, monkeypatch):
    create, _, _ = pipeline; p, root, _ = create(); matte = tracked_matte(root, p['frames']); matte['role'] = 'collide'
    original = copy.deepcopy(matte); physical = {f.name: f.read_bytes() for f in (root/matte['directory']).glob('*.png')}
    p['geometry']['mattes'] = [matte]; p['effects'][0]['element']['colliders'] = [matte['id']]; projects.save(p)
    signature = element_jobs.generation_signature(p, p['effects'][0])
    calls = []; client = client_for(p, monkeypatch, calls); tid = completed(client)
    response = client.post(f'/api/projects/shot/people-alpha/{tid}/approve', json={'reviewed': True, 'matte_id': matte['id'], 'effect_id': 'blast'})
    assert response.status_code == 200, response.text
    current = projects.load('shot'); stored = current['geometry']['mattes'][0]
    assert stored['role'] == 'both' and stored['holdoutPost'] and stored['holdout']['take_id'] == tid
    assert all(stored[k] == v for k, v in original.items() if k != 'role')
    assert element_jobs.generation_signature(current, current['effects'][0]) == signature, 'Enabling optical alpha must preserve an existing collider-only beauty/alpha cache'
    assert {f.name: f.read_bytes() for f in (root/matte['directory']).glob('*.png')} == physical


def test_stale_or_tampered_alpha_cannot_be_attached_and_busy_requests_are_guarded(pipeline, monkeypatch):
    create, _, _ = pipeline; p, root, _ = create(); client = client_for(p, monkeypatch, []); tid = completed(client)
    endpoint = f'/api/projects/shot/people-alpha/{tid}/approve'
    original = projects.load('shot'); changed = copy.deepcopy(original)
    changed['native_profile']['source_sha256'] = 'changed'; projects.save(changed)
    assert client.post(endpoint, json={'reviewed': True}).status_code == 400
    assert projects.load('shot')['geometry']['mattes'] == []
    projects.save(original)
    path = people_alpha.asset_path('shot', tid, 'frame', 0); path.write_bytes(b'corrupt')
    assert client.post(endpoint, json={'reviewed': True}).status_code == 400
    assert client.get(f'/api/projects/shot/people-alpha/{tid}/frames/0.png').status_code == 404
    assert projects.load('shot')['geometry']['mattes'] == []
    current = projects.load('shot'); current['renders'].append({'id': 'busy', 'kind': 'element', 'status': 'rendering'}); projects.save(current)
    assert client.post('/api/projects/shot/people-alpha/prepare', json={}).status_code == 409
    assert client.post(endpoint, json={'reviewed': True}).status_code == 409


def test_captured_source_depth_is_validated_and_frozen_with_element_guide(pipeline, monkeypatch):
    create, _, _ = pipeline; p, root, _ = create(); client = client_for(p, monkeypatch, [])
    spec = element_jobs.plan(p, 'blast')
    guide = png(np.full((spec['height'], spec['width'], 3), 180, np.uint8))
    visible = png(np.full((spec['height'], spec['width']), 255, np.uint8))
    files = [(f'{kind}_{i:04d}.png', ('frame.png', data, 'image/png'))
             for i in range(spec['active_frames']) for kind, data in [('guide', guide), ('visibility', visible)]]
    endpoint = '/api/projects/shot/elements/blast/prepare'
    for depth in [True, -1, 0, .0001, 1e6+1, float('nan'), float('inf')]:
        response = client.post(endpoint, data={'metadata': json.dumps({'signature': spec['signature'], 'source_depth': depth})}, files=files)
        assert response.status_code == 400, response.text
    assert projects.load('shot')['element_takes'] == []
    response = client.post(endpoint, data={'metadata': json.dumps({'signature': spec['signature'], 'source_depth': .4})}, files=files)
    assert response.status_code == 200, response.text
    row = projects.load('shot')['element_takes'][0]
    assert row['plan']['source_depth'] == .4
    assert elements.inspect(row['take_dir'])['provenance']['plan']['source_depth'] == .4
    row['plan']['source_depth'] = 4
    with pytest.raises(ValueError, match='timing or geometric post assets changed'):
        element_jobs._verify_inputs(root, row)


@pytest.mark.parametrize('status', ['queued', 'submitting', 'rendering'])
def test_interrupted_people_alpha_is_uncertain_and_cannot_resubmit(pipeline, monkeypatch, status):
    create, _, cfg = pipeline; p, root, _ = create(); row = people_alpha.prepare('shot', connection=cfg)
    p = projects.load('shot'); p['people_alpha_takes'][0]['status'] = status
    p['renders'].append({'id': 'dead', 'kind': 'people_alpha', 'people_alpha_take': row['id'], 'status': status, 'pid': -1})
    projects.save(p)
    monkeypatch.setattr(projects, 'list_all', lambda: [{'id': 'shot'}]); monkeypatch.setattr(projects, '_alive', lambda _: False)
    assert projects.recover() == 1
    current = projects.load('shot')
    assert current['renders'][0]['status'] == 'error' and current['people_alpha_takes'][0]['status'] == 'uncertain'
    calls = []; client = client_for(p, monkeypatch, calls)
    assert client.post(f'/api/projects/shot/people-alpha/{row["id"]}/generate', json={}).status_code == 400
    assert calls == []
