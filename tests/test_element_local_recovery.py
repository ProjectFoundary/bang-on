"""Downloaded-output recovery uses real tiny media and no network/provider."""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from keyfx import development_gate as gate, elements
from test_development_gate import setup_gate, worker, approve, wait_for, pending
from test_elements import catalog, fake_provider


@pytest.mark.parametrize('spread,accepted', [(0, True), (8, True), (12, True), (13, False), (80, False)])
def test_alpha_near_gray_policy_is_bounded_and_keeps_linear_channel_mean(tmp_path, monkeypatch, spread, accepted):
    im = np.empty((64, 64, 3), np.uint8); im[:] = [100, 100 + spread, 100 + spread]
    monkeypatch.setattr(elements, '_read_video', lambda _: ([im], 24.))
    raw = tmp_path / 'raw.mp4'; raw.write_bytes(b'synthetic decoder-policy fixture')
    request = {'recipe': {'gen_fps': 24, 'active_frames': 1, 'model_frames': 1},
               'geometry': {'width': 64, 'height': 64, 'content_rect': [0, 0, 64, 64]}}
    out = tmp_path / 'out'; out.mkdir()
    if not accepted:
        with pytest.raises(elements.ElementError, match=f'maximum channel spread {spread}; allowed 12'):
            elements._validate_output(raw, request, 'alpha', out)
        assert not (out / 'frames').exists()
        return
    asset = elements._validate_output(raw, request, 'alpha', out)
    matte = cv2.imread(str(Path(asset['frames_dir']) / '00000.png'), cv2.IMREAD_UNCHANGED)
    assert np.all(matte == np.floor(im.mean(2) + .5))
    assert asset['alpha_channel_spread_max'] == spread and asset['alpha_channel_spread_limit'] == 12
    assert asset['colour_space'] == 'linear_data' and asset['colour_association'] == 'opacity'


@pytest.mark.parametrize('spread,count,accepted', [(18, 1, True), (24, 4, True), (25, 1, False),
                                                 (13, 4096, False), (18, 5, False), (255, 1, False)])
def test_alpha_sparse_chroma_has_independent_per_frame_and_hard_bounds(tmp_path, monkeypatch, spread, count, accepted):
    frames = [np.full((64, 64, 3), 100, np.uint8) for _ in range(9)]
    colour = [0, 0, 255] if spread == 255 else [100, 100 + spread, 100 + spread]
    frames[0].reshape(-1, 3)[:count] = colour
    monkeypatch.setattr(elements, '_read_video', lambda _: (frames, 24.))
    raw = tmp_path / 'raw.mp4'; raw.write_bytes(b'synthetic sparse-chroma decoder fixture')
    request = {'recipe': {'gen_fps': 24, 'active_frames': 5, 'model_frames': 9},
               'geometry': {'width': 64, 'height': 64, 'content_rect': [0, 0, 64, 64]}}
    out = tmp_path / 'out'; out.mkdir()
    if not accepted:
        with pytest.raises(elements.ElementError, match='near-grayscale matte'):
            elements._validate_output(raw, request, 'alpha', out)
        assert not (out / 'frames').exists()
        return
    asset = elements._validate_output(raw, request, 'alpha', out)
    assert asset['alpha_channel_spread_max'] == spread
    assert asset['alpha_channel_spread_hard_limit'] == 24
    assert asset['alpha_channel_outlier_count_total'] == asset['alpha_channel_outlier_count_max'] == count
    assert asset['alpha_channel_outlier_fraction_max'] == pytest.approx(count / 4096)
    assert asset['alpha_channel_statistics_frames'] == 9
    assert asset['alpha_validation_policy'] == 'near_gray_sparse_chroma_v2'
    matte = cv2.imread(str(Path(asset['frames_dir']) / '00000.png'), cv2.IMREAD_UNCHANGED)
    assert np.array_equal(matte, np.floor(frames[0].mean(2) + .5).astype(np.uint8))


def test_alpha_chroma_guard_also_checks_discarded_model_tail(tmp_path, monkeypatch):
    frames = [np.full((64, 64, 3), 100, np.uint8) for _ in range(9)]
    frames[-1][0, 0] = [100, 125, 125]
    monkeypatch.setattr(elements, '_read_video', lambda _: (frames, 24.))
    raw = tmp_path / 'raw.mp4'; raw.write_bytes(b'synthetic tail guard fixture')
    request = {'recipe': {'gen_fps': 24, 'active_frames': 5, 'model_frames': 9},
               'geometry': {'width': 64, 'height': 64, 'content_rect': [0, 0, 64, 64]}}
    out = tmp_path / 'out'; out.mkdir()
    with pytest.raises(elements.ElementError, match='hard limit 24'):
        elements._validate_output(raw, request, 'alpha', out)
    assert not (out / 'frames').exists()


def failed_download(tmp_path, monkeypatch):
    project, audit, config, take = setup_gate(tmp_path, monkeypatch)
    calls = []; base = fake_provider(calls)
    def near_gray_alpha(**kw):
        if 'alpha-gen' not in kw['graph']['lora']['inputs']['lora_name']: return base(**kw)
        calls.append(('alpha', kw['idempotency_key'], kw['uploads']))
        kw['on_status']('fake-job-alpha', 'running')
        kw['out_dir'].mkdir(parents=True, exist_ok=True)
        out = kw['out_dir'] / 'result.mp4'
        frames = [np.full((64, 64, 3), (120, 126, 130), np.uint8) for _ in range(9)]
        elements._encode(out, frames); return [out]
    # Reproduce the old strict validator against an actual retained MP4.
    monkeypatch.setattr(elements, 'ALPHA_CHANNEL_SPREAD_LIMIT', 4)
    with worker(audit, lambda: elements.run(take['take_dir'], executor=near_gray_alpha, catalog=catalog())) as future:
        approve(wait_for(lambda: pending(audit, 'rgb')))
        approve(wait_for(lambda: pending(audit, 'alpha')))
        with pytest.raises(elements.ElementError, match='near-grayscale matte'): future.result(timeout=8)
    monkeypatch.setattr(elements, 'ALPHA_CHANNEL_SPREAD_LIMIT', 12)
    state = elements.inspect(take['take_dir'])
    assert 'maximum channel spread' in state['error'] and 'allowed 4' in state['error']
    return project, audit, take, calls


def test_explicit_local_recovery_preserves_paid_assets_claims_receipts_and_consumed_allowance(tmp_path, monkeypatch):
    project, audit, take, calls = failed_download(tmp_path, monkeypatch)
    td = Path(take['take_dir']); state = elements.inspect(td)
    sp = Path(state['stages']['alpha']['cache_dir']); previous = (sp / 'stage.json').read_bytes()
    partial = sp / 'frames'; partial.mkdir(exist_ok=True)
    (partial / 'legacy-partial-frame.bin').write_bytes(b'preserve prior partial decoded artifacts')
    immutable = [td / 'request.json', Path(state['guide']['path']), Path(state['source_guide']['path']),
                 sp / 'claim', sp / 'graph.json', sp / 'raw/result.mp4', partial / 'legacy-partial-frame.bin',
                 Path(state['assets']['rgb']['path']), Path(state['assets']['rgb']['frame_manifest'])]
    before = {str(p): elements._sha(p) for p in immutable}
    ledger_before = json.loads((audit / 'ledger.json').read_text())['attempts']
    assert [r['status'] for r in ledger_before] == ['done', 'failed']
    candidate = elements.downloaded_stage_candidate(td, 'alpha')
    monkeypatch.setattr(elements, '_execute_with_client', lambda **_: pytest.fail('Local recovery called provider'))
    monkeypatch.setattr(elements.connections, 'current', lambda: pytest.fail('Local recovery read credentials'))
    done = elements.recover_downloaded_stage(td, 'alpha', expected_raw_sha256=candidate['raw_sha256'])
    assert done['status'] == 'done' and set(done['assets']) == {'rgb', 'alpha'}
    assert [c[0] for c in calls] == ['rgb', 'alpha']
    assert all(elements._sha(Path(path)) == sha for path, sha in before.items())
    new_receipt = json.loads((sp / 'stage.json').read_text())
    old_receipt = json.loads(previous)
    for key in ('job', 'idempotency_key', 'input_sha256', 'graph_sha256', 'started_at'):
        assert new_receipt[key] == old_receipt[key]
    recovery = Path(new_receipt['local_recovery'])
    assert recovery.with_name('stage-before.json').read_bytes() == previous
    assert json.loads(recovery.read_text())['new_inference_submissions'] == 0
    assert Path(done['assets']['alpha']['frames_dir']).is_relative_to(sp / 'recovery')
    elements._verify_asset(done['assets']['alpha'])
    ledger_after = json.loads((audit / 'ledger.json').read_text())['attempts']
    assert len(ledger_after) == len(ledger_before) == 2
    assert [a['id'] for a in ledger_after] == [a['id'] for a in ledger_before]
    assert [a['idempotency_key'] for a in ledger_after] == [a['idempotency_key'] for a in ledger_before]
    assert [a['status'] for a in ledger_after] == ['done', 'done']
    with pytest.raises(gate.GateError, match='already attempted'): gate._check_allowance(gate._config(), 1)
    again = elements.recover_downloaded_stage(td, 'alpha', expected_raw_sha256=candidate['raw_sha256'])
    assert again['last_local_recovery'] == done['last_local_recovery']
    assert len(json.loads((audit / 'ledger.json').read_text())['attempts']) == 2


@pytest.mark.parametrize('tamper', ['hash', 'uncertain', 'graph'])
def test_recovery_refuses_unreviewed_output_unknown_completion_or_changed_provenance(tmp_path, monkeypatch, tamper):
    project, audit, take, calls = failed_download(tmp_path, monkeypatch)
    td = Path(take['take_dir']); state = elements.inspect(td)
    sp = Path(state['stages']['alpha']['cache_dir'])
    candidate = elements.downloaded_stage_candidate(td, 'alpha')
    expected = candidate['raw_sha256']
    if tamper == 'hash': expected = '0' * 64
    elif tamper == 'uncertain':
        state = json.loads((td / 'state.json').read_text()); state['status'] = 'uncertain'; elements._write(td / 'state.json', state)
    else: (sp / 'graph.json').write_text('{}')
    monkeypatch.setattr(elements, '_execute_with_client', lambda **_: pytest.fail('Rejected recovery called provider'))
    with pytest.raises(elements.ElementError): elements.recover_downloaded_stage(td, 'alpha', expected_raw_sha256=expected)
    assert len(calls) == 2 and not (sp / 'recovery').exists()
    assert [a['status'] for a in json.loads((audit / 'ledger.json').read_text())['attempts']] == ['done', 'failed']
