"""Per-element review/versioning with real frozen files and offline providers."""
import copy
import json
from pathlib import Path

import cv2
from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
import pytest

from keyfx import element_api, element_graphs, element_jobs, elements, presets, projects
from test_element_pipeline import catalogue, generate, pipeline, png

_RUN = elements.run


def test_approval_pins_reviewed_output_over_newer_unreviewed_take(pipeline, monkeypatch):
    create, exports, _ = pipeline
    p, root, _ = create()
    reviewed, calls = generate(p, monkeypatch)
    approved = element_jobs.approve('shot', reviewed['id'])
    assert approved['approved'] and approved['current'] and not approved['approval_outdated']
    p = projects.load('shot')
    newer = {**copy.deepcopy(reviewed), 'id': 'newer-unreviewed'}
    p['element_takes'].insert(0, newer)
    projects.save(p)
    assert element_jobs.selected_take(p, p['effects'][0])['id'] == reviewed['id']
    monkeypatch.setattr(elements, 'run', lambda *a, **kw: pytest.fail('Review/composite resubmitted a model'))
    job = element_jobs.composite('shot')
    assert projects.load('shot')['renders'][0]['status'] == 'done'
    request = json.loads((root/'renders'/job['id']/'request.json').read_text())
    assert request['elements'] == [reviewed['id']]
    assert len(exports) == 1 and calls == ['rgb', 'alpha']


def test_post_controls_keep_approval_but_guide_change_requires_reapproval(pipeline, monkeypatch):
    create, _, _ = pipeline
    p, _, _ = create()
    row, _ = generate(p, monkeypatch)
    element_jobs.approve('shot', row['id'])
    p = projects.load('shot')
    p['effects'][0]['element'].update(opacity=.4, gain=1.2, light=.8, shadow=.3)
    projects.save(p)
    assert element_jobs.records(p)[0]['approved']
    assert element_jobs.selected_take(p, p['effects'][0])['id'] == row['id']
    p['effects'][0]['size'] = p['effects'][0].get('size', 1) * 1.5
    projects.save(p)
    state = element_jobs.records(p)[0]
    assert state['approval_outdated'] and not state['approved'] and not state['current']
    before = len(p['renders'])
    with pytest.raises(ValueError, match='approved element changed'):
        element_jobs.composite('shot')
    with pytest.raises(ValueError, match='outdated'):
        element_jobs.approve('shot', row['id'])
    assert len(projects.load('shot')['renders']) == before


def test_approval_verifies_completed_assets_before_saving(pipeline, monkeypatch):
    create, _, _ = pipeline
    p, _, _ = create()
    row, _ = generate(p, monkeypatch)
    path = Path(row['assets']['alpha']['frames_dir'])/'00000.png'
    im = cv2.imread(str(path), 0); im[0, 0] = 255
    cv2.imwrite(str(path), im)
    with pytest.raises(ValueError, match='cached RGB/alpha asset'):
        element_jobs.approve('shot', row['id'])
    assert projects.load('shot').get('element_approvals', {}) == {}


def _render_smoke(p, monkeypatch):
    spec = element_jobs.plan(p, 'smoke')
    image = png(np.full((spec['height'], spec['width'], 3), 80, np.uint8))
    visibility = png(np.full((spec['height'], spec['width']), 255, np.uint8))
    row = element_jobs.prepare_upload('shot', 'smoke', {'signature': spec['signature']},
                                     {i: image for i in range(spec['active_frames'])},
                                     {i: visibility for i in range(spec['active_frames'])})
    calls = []
    def provider(*, graph, uploads, out_dir, on_status, **_):
        stage = 'alpha' if 'alpha-gen' in graph['lora']['inputs']['lora_name'] else 'rgb'
        calls.append(stage)
        g = graph['latent']['inputs']
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir/'result.mp4'
        frames = [np.full((g['height'], g['width'], 3), 100 if stage == 'rgb' else 128, np.uint8)
                  for _ in range(g['length'])]
        elements._encode(path, frames)
        on_status('offline-smoke-'+stage, 'running')
        return [path]
    nodes = catalogue([g for _, _, g, _ in element_graphs.requirements({})])
    monkeypatch.setattr(elements, 'run', lambda td, on_status=None: _RUN(td, executor=provider, catalog=nodes, on_status=on_status))
    element_jobs.start('shot', row['id'])
    return calls


def test_smoke_rerun_preserves_approved_explosion_and_only_renders_smoke(pipeline, monkeypatch):
    create, _, _ = pipeline
    p, root, _ = create()
    blast, blast_calls = generate(p, monkeypatch)
    element_jobs.approve('shot', blast['id'])
    p = projects.load('shot')
    blast_effect = copy.deepcopy(p['effects'][0])
    frozen_request = (Path(blast['take_dir'])/'request.json').read_bytes()
    smoke = presets.new_effect('smoke_grenade', 2, .3, .6, 'smoke', p['frames'])
    smoke['element'] = {**element_jobs.DEFAULT, 'duration_s': .1, 'blur': 0, 'seed': 79}
    p['effects'].append(smoke); projects.save(p)
    before = len(p['renders'])
    new = element_jobs.new_version('shot', 'smoke')
    p = projects.load('shot')
    assert new['seed'] != 79 and 0 <= new['seed'] < 2**32
    assert len(p['renders']) == before, 'Starting a version must not submit a model'
    assert p['effects'][0] == blast_effect
    assert p['element_takes'] == [blast]
    assert p['element_approvals'] == {'blast': blast['id']}
    smoke_calls = _render_smoke(p, monkeypatch)
    p = projects.load('shot')
    assert element_jobs.selected_take(p, p['effects'][0])['id'] == blast['id']
    smoke_take = element_jobs.selected_take(p, p['effects'][1])
    assert smoke_take and smoke_take['effect_id'] == 'smoke'
    element_jobs.approve('shot', smoke_take['id'])
    composite = element_jobs.composite('shot')
    assert projects.load('shot')['renders'][0]['status'] == 'done'
    request = json.loads((root/'renders'/composite['id']/'request.json').read_text())
    assert request['elements'] == [blast['id'], smoke_take['id']]
    assert blast_calls == ['rgb', 'alpha'] and smoke_calls == ['rgb', 'alpha']
    assert (Path(blast['take_dir'])/'request.json').read_bytes() == frozen_request
    assert next(r for r in projects.load('shot')['element_takes'] if r['id'] == blast['id']) == blast


def test_explicit_rerun_reseeds_without_reusing_or_deleting_old_take(pipeline, monkeypatch):
    create, _, _ = pipeline
    p, _, _ = create()
    row, _ = generate(p, monkeypatch)
    element_jobs.approve('shot', row['id'])
    before = projects.load('shot')
    a = element_jobs.new_version('shot', 'blast')
    b = element_jobs.new_version('shot', 'blast')
    after = projects.load('shot')
    assert len({row['plan']['settings']['seed'], a['seed'], b['seed']}) == 3
    assert after['element_takes'] == before['element_takes']
    assert after['renders'] == before['renders']
    assert after['element_approvals'] == {}
    assert not element_jobs.records(after)[0]['current']
    assert element_jobs.selected_take(after, after['effects'][0]) is None


def test_review_version_endpoints_are_local_strict_and_busy_guarded(pipeline, monkeypatch):
    create, _, _ = pipeline
    p, _, _ = create()
    row, _ = generate(p, monkeypatch)
    app = FastAPI(); app.include_router(element_api.router)
    client = TestClient(app)
    approval = client.post(f'/api/projects/shot/element-takes/{row["id"]}/approve')
    assert approval.status_code == 200, approval.text
    assert approval.json()['approved'] is True
    assert 'take_dir' not in approval.json() and 'inputs' not in approval.json()
    assert client.post('/api/projects/shot/element-takes/missing/approve').status_code == 400
    assert client.post('/api/projects/shot/elements/missing/new-version').status_code == 400
    new = client.post('/api/projects/shot/elements/blast/new-version')
    assert new.status_code == 200 and new.json()['effect_id'] == 'blast'
    assert new.json()['seed'] != row['plan']['settings']['seed']
    assert client.post(f'/api/projects/shot/element-takes/{row["id"]}/approve').status_code == 400
    p = projects.load('shot'); p['renders'].append({'id': 'active', 'kind': 'element', 'status': 'queued'}); projects.save(p)
    assert client.post('/api/projects/shot/elements/blast/new-version').status_code == 409
    assert client.post(f'/api/projects/shot/element-takes/{row["id"]}/approve').status_code == 409
