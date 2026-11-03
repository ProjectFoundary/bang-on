"""Real tiny-media lifecycle with synthetic providers only: no accounts or spend."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

import pytest
from comfy_sdk.exceptions import JobFailed

from keyfx import development_gate as gate, element_graphs as graphs, elements
from test_elements import PROVENANCE, catalog, fake_provider, guide


def setup_gate(tmp_path, monkeypatch, *, maximum=4):
    project = tmp_path / 'project'; project.mkdir()
    source = guide(project)
    audit = tmp_path / 'audit'
    cfg = {'version': 1, 'session_id': 'offline-four-stage-test', 'project_root': str(project),
           'audit_dir': str(audit), 'max_submissions': maximum, 'poll_seconds': .01,
           'allowed_stages': [{'effect_id': e, 'stage': s} for e in ('smoke', 'blast') for s in ('rgb', 'alpha')]}
    config = tmp_path / 'gate.json'; config.write_text(json.dumps(cfg))
    monkeypatch.setenv(gate.ENV, str(config))
    take = elements.prepare(project / 'elements', 'smoke', source, {'active_frames': 5}, provenance=PROVENANCE)
    return project, audit, config, take


def wait_for(predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        value = predicate()
        if value: return value
        time.sleep(.01)
    raise AssertionError('Offline gate fixture did not reach expected state')


def pending(audit, stage):
    return next((p for p in audit.glob(f'pending/*/*/{stage}/*/pending.json') if not p.with_name('qc.json').exists()), None)


def approve(path, **override):
    qc = {'verdict': 'pass', 'pending_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
          'reviewer': 'synthetic independent test reviewer', 'checked_at': datetime.now(timezone.utc).isoformat()}
    qc.update(override)
    gate._write(path.with_name('qc.json'), qc)


@contextmanager
def worker(audit, fn):
    pool = ThreadPoolExecutor(max_workers=1); future = pool.submit(fn)
    try: yield future
    finally:
        audit.mkdir(parents=True, exist_ok=True); (audit / 'cancel').touch()
        try: future.result(timeout=3)
        except Exception: pass
        pool.shutdown(wait=True)
        (audit / 'cancel').unlink(missing_ok=True)


def test_both_stages_wait_before_upload_and_alpha_reviews_actual_rgb(tmp_path, monkeypatch):
    project, audit, config, take = setup_gate(tmp_path, monkeypatch)
    calls, statuses = [], []
    with worker(audit, lambda: elements.run(take['take_dir'], executor=fake_provider(calls),
                                           catalog=catalog(), on_status=statuses.append)) as future:
        rgb = wait_for(lambda: pending(audit, 'rgb'))
        wait_for(lambda: 'awaiting_rgb_qc' in statuses)
        assert calls == [] and not (audit / 'ledger.json').exists()
        assert not list((project / 'elements').glob('cache/**/claim'))
        approve(rgb)
        alpha = wait_for(lambda: pending(audit, 'alpha'))
        assert [c[0] for c in calls] == ['rgb']
        frozen = json.loads(alpha.read_text())
        actual = elements.inspect(take['take_dir'])['assets']['rgb']
        assert frozen['uploads']['control']['path'] == actual['path']
        assert frozen['input_sha256'] == actual['sha256']
        alpha_graph = json.loads(alpha.with_name('graph.json').read_text())
        assert alpha_graph['pos']['inputs']['text'] == alpha_graph['neg']['inputs']['text'] == ''
        approve(alpha)
        done = future.result(timeout=8)
    assert done['status'] == 'done'
    assert [c[0] for c in calls] == ['rgb', 'alpha']
    attempts = json.loads((audit / 'ledger.json').read_text())['attempts']
    assert [a['status'] for a in attempts] == ['done', 'done']
    assert [a['job'] for a in attempts] == ['fake-job-rgb', 'fake-job-alpha']
    assert len({a['idempotency_key'] for a in attempts}) == 2
    elements.run(take['take_dir'], executor=lambda **_: pytest.fail('Completed cache spent again'))
    assert len(json.loads((audit / 'ledger.json').read_text())['attempts']) == 2


@pytest.mark.parametrize('bad', [{'verdict': 'fail'}, {'pending_sha256': 'wrong'}, {'reviewer': ''}, {'checked_at': ''}])
def test_failed_or_unbound_qc_never_calls_provider(tmp_path, monkeypatch, bad):
    project, audit, config, take = setup_gate(tmp_path, monkeypatch)
    calls = []
    with worker(audit, lambda: elements.run(take['take_dir'], executor=fake_provider(calls), catalog=catalog())) as future:
        path = wait_for(lambda: pending(audit, 'rgb')); approve(path, **bad)
        with pytest.raises(elements.ElementError, match='QC did not pass'): future.result(timeout=5)
    assert calls == [] and not (audit / 'ledger.json').exists()


def test_stale_qc_fails_and_post_review_tampering_is_rechecked(tmp_path, monkeypatch):
    project, audit, config, take = setup_gate(tmp_path, monkeypatch)
    request = elements.inspect(take['take_dir'])
    graph, uploads, out = graphs.graph('rgb', Path(request['guide']['path']), request['recipe'], width=64, height=64)
    call = lambda: gate.before_stage(request, 'rgb', graph, uploads, out, project / 'elements/cache/rgb/test')
    with worker(audit, call) as future:
        path = wait_for(lambda: pending(audit, 'rgb')); approve(path)
        permit = future.result(timeout=5)
    source = Path(request['guide']['path']); original = source.read_bytes(); source.write_bytes(original + b'changed')
    with pytest.raises(gate.GateError, match='input changed'): permit.reserve('no-submit-key')
    assert not (audit / 'ledger.json').exists()
    # A QC file older than its exact pending record also fails closed.
    source.write_bytes(original)
    os.utime(path.with_name('qc.json'), ns=(1, 1))
    with pytest.raises(gate.GateError, match='pending inputs'): call()


def test_one_attempt_budget_retains_rgb_and_blocks_alpha_without_requesting_qc(tmp_path, monkeypatch):
    project, audit, config, take = setup_gate(tmp_path, monkeypatch, maximum=1)
    calls = []
    with worker(audit, lambda: elements.run(take['take_dir'], executor=fake_provider(calls), catalog=catalog())) as future:
        approve(wait_for(lambda: pending(audit, 'rgb')))
        with pytest.raises(elements.ElementError, match='allowance exhausted'): future.result(timeout=8)
    saved = elements.inspect(take['take_dir'])
    assert 'rgb' in saved['assets'] and 'alpha' not in saved['assets']
    assert [c[0] for c in calls] == ['rgb'] and not pending(audit, 'alpha')
    assert len(json.loads((audit / 'ledger.json').read_text())['attempts']) == 1


@pytest.mark.parametrize('known_receipt', [False, True])
def test_uncertain_submit_uses_one_allowance_and_blocks_later_effect(tmp_path, monkeypatch, known_receipt):
    project, audit, config, take = setup_gate(tmp_path, monkeypatch)
    calls = []
    def lost(**kw):
        calls.append(kw['idempotency_key'])
        if known_receipt: kw['on_status']('known-job-still-running', 'running')
        raise TimeoutError('provider completion remains unknown')
    with worker(audit, lambda: elements.run(take['take_dir'], executor=lost, catalog=catalog())) as future:
        approve(wait_for(lambda: pending(audit, 'rgb')))
        with pytest.raises(elements.ElementError, match='inspect the saved stage'): future.result(timeout=5)
    ledger = json.loads((audit / 'ledger.json').read_text())
    assert len(ledger['attempts']) == 1 and ledger['attempts'][0]['status'] == 'uncertain'
    if known_receipt: assert ledger['attempts'][0]['job'] == 'known-job-still-running'
    blast = elements.prepare(project / 'elements', 'blast', project / 'guide.mp4',
                             {'active_frames': 5, 'seed': 43}, provenance=PROVENANCE)
    with pytest.raises(elements.ElementError, match='outstanding/uncertain'):
        elements.run(blast['take_dir'], executor=lambda **_: pytest.fail('Unknown receipt was bypassed'), catalog=catalog())
    assert len(calls) == 1


def test_verified_terminal_provider_failure_is_not_mistaken_for_unknown_completion(tmp_path, monkeypatch):
    project, audit, config, take = setup_gate(tmp_path, monkeypatch)
    def terminal_failure(**kw):
        kw['on_status']('known-terminal-failed-job', 'running')
        raise JobFailed('job reached verified non-success terminal state')
    with worker(audit, lambda: elements.run(take['take_dir'], executor=terminal_failure, catalog=catalog())) as future:
        approve(wait_for(lambda: pending(audit, 'rgb')))
        with pytest.raises(elements.ElementError): future.result(timeout=5)
    attempt = json.loads((audit / 'ledger.json').read_text())['attempts'][0]
    assert attempt['status'] == 'failed' and attempt['job'] == 'known-terminal-failed-job'
    # This failed call still consumes its slot, while a distinct later test may
    # proceed because the provider explicitly proved that job is terminal.
    gate._check_allowance(gate._config(), 2)


def test_known_alpha_receipt_with_unknown_completion_keeps_rgb_and_blocks_later_spend(tmp_path, monkeypatch):
    project, audit, config, take = setup_gate(tmp_path, monkeypatch)
    calls = []
    rgb_provider = fake_provider(calls)
    def lost_alpha(**kw):
        if 'alpha-gen' in kw['graph']['lora']['inputs']['lora_name']:
            kw['on_status']('known-alpha-job-still-running', 'running')
            raise TimeoutError('alpha completion is unknown')
        return rgb_provider(**kw)
    with worker(audit, lambda: elements.run(take['take_dir'], executor=lost_alpha, catalog=catalog())) as future:
        approve(wait_for(lambda: pending(audit, 'rgb')))
        approve(wait_for(lambda: pending(audit, 'alpha')))
        with pytest.raises(elements.ElementError): future.result(timeout=8)
    saved = elements.inspect(take['take_dir'])
    assert saved['status'] == 'uncertain' and 'rgb' in saved['assets'] and 'alpha' not in saved['assets']
    elements._verify_asset(saved['assets']['rgb'])
    attempts = json.loads((audit / 'ledger.json').read_text())['attempts']
    assert [a['status'] for a in attempts] == ['done', 'uncertain']
    assert attempts[1]['job'] == 'known-alpha-job-still-running'
    with pytest.raises(gate.GateError, match='outstanding/uncertain'): gate._check_allowance(gate._config(), 2)


def test_scope_and_configuration_ceiling_fail_before_provider(tmp_path, monkeypatch):
    project, audit, config, take = setup_gate(tmp_path, monkeypatch)
    wrong = elements.prepare(project / 'elements', 'outside', project / 'guide.mp4', {'active_frames': 5}, provenance=PROVENANCE)
    with pytest.raises(elements.ElementError, match='outside the configured'):
        elements.run(wrong['take_dir'], executor=lambda **_: pytest.fail('Out-of-scope job'), catalog=catalog())
    cfg = json.loads(config.read_text()); cfg['max_submissions'] = 5; config.write_text(json.dumps(cfg))
    with pytest.raises(elements.ElementError, match='at most four'):
        elements.run(take['take_dir'], executor=lambda **_: pytest.fail('Exceeded ceiling'), catalog=catalog())


def test_four_stages_exhaust_contract_and_new_seed_cannot_repeat_smoke(tmp_path, monkeypatch):
    project, audit, config, take = setup_gate(tmp_path, monkeypatch)
    calls = []
    for effect, seed in [('smoke', 42), ('blast', 43)]:
        take = elements.prepare(project / 'elements', effect, project / 'guide.mp4',
                                {'active_frames': 5, 'seed': seed}, provenance=PROVENANCE)
        with worker(audit, lambda: elements.run(take['take_dir'], executor=fake_provider(calls), catalog=catalog())) as future:
            approve(wait_for(lambda: pending(audit, 'rgb')))
            approve(wait_for(lambda: pending(audit, 'alpha')))
            assert future.result(timeout=8)['status'] == 'done'
    assert [c[0] for c in calls] == ['rgb', 'alpha', 'rgb', 'alpha']
    assert len(json.loads((audit / 'ledger.json').read_text())['attempts']) == 4
    new = elements.prepare(project / 'elements', 'smoke', project / 'guide.mp4',
                           {'active_frames': 5, 'seed': 99}, provenance=PROVENANCE)
    with pytest.raises(elements.ElementError, match='allowance exhausted'):
        elements.run(new['take_dir'], executor=lambda **_: pytest.fail('Fifth attempt'), catalog=catalog())
