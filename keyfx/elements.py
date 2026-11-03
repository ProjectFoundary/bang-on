"""Immutable per-element LTX/alpha takes with independent content-addressed stages.

This module never accepts a photograph for a model graph. The caller supplies
an isolated simulation guide and later composites these assets over its master.
No credentials are persisted; no public service fallback or automatic job retry.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from fractions import Fraction
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from comfy_sdk.exceptions import JobFailed

from . import comfy, connections, development_gate, element_graphs as graphs

_lock = threading.RLock()
# Generative mattes and H.264 chroma decoding need not be bit-identical RGB.
# Most pixels must be near-gray; sparse tint/ringing gets a separate hard cap.
# Scalar opacity stays the rounded channel mean without gamma/levels/blur.
ALPHA_CHANNEL_SPREAD_LIMIT = 12
ALPHA_CHANNEL_OUTLIER_HARD_LIMIT = 24
ALPHA_CHANNEL_OUTLIER_FRACTION_LIMIT = .001


class ElementError(RuntimeError):
    pass


class AlreadySubmitted(ElementError):
    """A submitted/uncertain/failed stage cannot silently create another charge."""


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
    os.replace(temp, path)


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _connection_identity(cfg: connections.Connection) -> dict:
    # A hash binds the selected account/token without storing its secret.
    return {'mode': cfg.mode, 'url': connections.CLOUD_URL if cfg.mode == 'cloud' else cfg.local_url,
            'identity': graphs.digest([cfg.mode, cfg.local_url, cfg.comfyui_url,
                                       cfg.cloud_api_key if cfg.mode == 'cloud' else cfg.local_token])}


def _recipe(recipe: dict) -> dict:
    return graphs.normalize_recipe(recipe)


def _request_key(effect_id: str, guide: Path, recipe: dict, provenance: dict, backend=None) -> str:
    return graphs.digest({'effect_id': effect_id, 'guide_sha256': _sha(guide), 'recipe': _recipe(recipe),
                          'provenance': provenance, 'backend': backend})


def _safe_effect_id(effect_id: str) -> None:
    if not isinstance(effect_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', effect_id):
        raise ValueError('Invalid element id.')


def find_matching(takes_root: Path, effect_id: str, guide_video: Path, recipe: dict,
                  *, provenance: dict | None = None, connection: connections.Connection | None = None) -> dict | None:
    _safe_effect_id(effect_id)
    key = _request_key(effect_id, Path(guide_video), recipe, provenance or {},
                       _connection_identity(connection) if connection else None)
    path = Path(takes_root) / 'takes' / effect_id / key / 'request.json'
    return inspect(path.parent) if path.exists() else None


def _read_video(path: Path) -> tuple[list[np.ndarray], float]:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    frames = []
    while True:
        ok, im = cap.read()
        if not ok:
            break
        if max(im.shape[:2]) > 1920 or min(im.shape[:2]) > 1088:
            cap.release()
            raise ValueError('Reduce only the element render size to fit1920×1088; preserve the native plate separately.')
        frames.append(im)
        if len(frames) > graphs.MAX_FRAMES:
            cap.release()
            raise ValueError('Use a short isolated element of121frames or fewer.')
    cap.release()
    if not frames or not np.isfinite(fps) or fps <= 0:
        raise ValueError('The isolated element guide is unreadable.')
    return frames, fps


def _encode(path: Path, frames: list[np.ndarray]) -> None:
    h, w = frames[0].shape[:2]
    args = ['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{w}x{h}',
            '-r', str(graphs.GEN_FPS), '-i', '-', '-an', '-c:v', 'libx264', '-qp', '0', '-pix_fmt',
            'yuv444p', '-movflags', '+faststart', str(path)]
    p = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for frame in frames:
            p.stdin.write(frame.tobytes())
        p.stdin.close()
        error = p.stderr.read()
        if p.wait():
            raise ElementError('Could not encode the isolated guide: ' + error.decode(errors='replace')[:300])
    except BaseException:
        if p.poll() is None:
            p.kill()
        p.wait()
        raise


def _time_map(recipe: dict) -> dict:
    fps = recipe.get('source_fps', 24)
    if isinstance(fps, (list, tuple)) and len(fps) == 2:
        rate = Fraction(int(fps[0]), int(fps[1]))
    elif isinstance(fps, dict):
        rate = Fraction(int(fps['num']), int(fps['den']))
    else:
        rate = Fraction(str(fps))
    if rate <= 0:
        raise ValueError('Source frame rate must be positive.')
    start = float(recipe.get('start_time_seconds', Fraction(recipe['start_frame'], 1) / rate))
    if not np.isfinite(start) or start < 0:
        raise ValueError('Element start time must be non-negative.')
    return {'gen_fps': graphs.GEN_FPS, 'source_fps': str(rate), 'start_frame': recipe['start_frame'],
            'start_time_seconds': start, 'source_window': copy.deepcopy(recipe.get('source_window')),
            'active_frames': recipe['active_frames'], 'model_frames': recipe['model_frames'],
            'model_to_active_index': [min(i, recipe['active_frames'] - 1) for i in range(recipe['model_frames'])],
            'active_time_seconds': [i / graphs.GEN_FPS for i in range(recipe['active_frames'])],
            'timeline_time_seconds': [start + i / graphs.GEN_FPS for i in range(recipe['active_frames'])]}


def prepare(takes_root: Path, effect_id: str, guide_video: Path, recipe: dict,
            *, provenance: dict | None = None, connection: connections.Connection | None = None) -> dict:
    """Freeze one local request. This does not read credentials or contact a provider."""
    _safe_effect_id(effect_id)
    guide_video = Path(guide_video).resolve()
    provenance = copy.deepcopy(provenance or {})
    if provenance.get('kind') != 'isolated_simulation_guide' or provenance.get('contains_plate', False):
        raise ValueError('Only an isolated_simulation_guide without photographed plate pixels may be submitted.')
    r = _recipe(recipe)
    mapping = _time_map(r)
    key = _request_key(effect_id, guide_video, r, provenance, _connection_identity(connection) if connection else None)
    root = Path(takes_root).resolve()
    td = root / 'takes' / effect_id / key
    with _lock:
        if (td / 'request.json').exists():
            return inspect(td)
        frames, fps = _read_video(guide_video)
        if abs(fps - graphs.GEN_FPS) > .01:
            raise ValueError('Prepare the isolated guide at24fps; native plate PTS are mapped separately.')
        if len(frames) not in (r['active_frames'], r['model_frames']):
            raise ValueError('Guide length must equal its active or padded model frame count.')
        sh, sw = frames[0].shape[:2]
        width, height = 64 * ((sw + 63) // 64), 64 * ((sh + 63) // 64)
        if max(width, height) > 1920 or min(width, height) > 1088:
            raise ValueError('Reduce only the element guide to fit1920×1088; retain the native plate separately.')
        left, top = (width - sw) // 2, (height - sh) // 2
        active = frames[:r['active_frames']]
        if not any(im.max() > 0 for im in active):
            raise ValueError('The isolated guide is empty; no paid generation was prepared.')
        if any(im.shape != frames[0].shape for im in frames):
            raise ValueError('Guide dimensions changed within the clip.')
        if len(frames) > r['active_frames'] and any(not np.array_equal(im, active[-1]) for im in frames[r['active_frames']:]):
            raise ValueError('Extra guide frames must be exact final-frame holds, not new motion.')
        padded = [cv2.copyMakeBorder(im, top, height - sh - top, left, width - sw - left,
                                    cv2.BORDER_CONSTANT, value=0) for im in active]
        padded += [padded[-1]] * (r['model_frames'] - r['active_frames'])
        td.mkdir(parents=True, exist_ok=True)
        inp = td / 'inputs'; inp.mkdir(exist_ok=True)
        frozen = inp / 'guide.mp4'
        if (width, height) == (sw, sh) and len(frames) == r['model_frames']:
            shutil.copy2(guide_video, frozen)
        else:
            _encode(frozen, padded)
        geometry = {'width': width, 'height': height, 'content_rect': [left, top, sw, sh],
                    'source_guide_size': [sw, sh], 'resized': False, 'padding': [left, top, width - sw - left, height - sh - top]}
        request = {'version': 1, 'id': key, 'effect_id': effect_id, 'take_dir': str(td), 'takes_root': str(root),
                   'created_at': time.time(), 'recipe': r, 'geometry': geometry, 'timing': mapping,
                   'provenance': provenance, 'source_guide': {'path': str(guide_video), 'sha256': _sha(guide_video)},
                   'guide': {'path': str(frozen), 'sha256': _sha(frozen)},
                   'backend': _connection_identity(connection) if connection is not None else None,
                   'model_identity_note': 'Model names/revisions are frozen declarations. Catalogue readiness alone does not attest remote weight contents.'}
        _write(td / 'request.json', request)
        _write(td / 'state.json', {'status': 'prepared', 'assets': {}, 'stages': {}, 'request_sha256': _sha(td / 'request.json')})
    return inspect(td)


def inspect(take_dir: Path) -> dict:
    td = Path(take_dir)
    request = _load(td / 'request.json')
    state = _load(td / 'state.json')
    return {**request, **state}


def check_capabilities(catalog: dict | None, recipe: dict) -> dict:
    checks = []
    for stage, label, g, required in graphs.requirements(recipe):
        status, details = connections.check_graph(catalog, g)
        checks.append({'id': stage, 'label': label, 'status': status, 'details': details, 'required': required})
    return {'ready': all(c['status'] == 'ready' for c in checks), 'checks': checks,
            'note': 'Advertised nodes/model names only; not proof of weight revision, memory, credits or output quality.'}


def fetch_capabilities(recipe: dict, *, connection: connections.Connection | None = None) -> dict:
    """Read-only readiness for the selected backend, called only when requested."""
    cfg = connection or connections.current()
    if not cfg.configured:
        return {'ready': False, 'checks': [], 'error': 'Connect Comfy before generating elements.'}
    try:
        catalog = connections.node_catalog(cfg)
        return check_capabilities(catalog, recipe)
    except Exception:
        return {'ready': False, 'checks': [], 'error': 'Could not verify the selected Comfy node/model catalogue. No generation started.'}


def _profile(r: dict, stage: str) -> dict:
    names = ['transformer', 'text_encoder', 'video_vae', 'audio_vae', 'alpha' if stage == 'alpha' else 'fluid']
    models = {n: r['models'][n] for n in names}
    revisions = {n: r['model_revisions'].get(n) for n in names}
    if stage == 'alpha':
        for role in graphs.ALPHA_MODEL_ROLES:
            override = f'alpha_{role}'
            if override in r['models']:
                models[role] = r['models'][override]
                revisions[role] = r['model_revisions'].get(override)
    return {'profile': r['profile'], 'models': models,
            'model_revisions': revisions,
            'fps': r['gen_fps'], 'active_frames': r['active_frames'], 'model_frames': r['model_frames'],
            'seed': r.get('alpha_seed', r['seed']) if stage == 'alpha' else r['seed'],
            'steps': r['steps'], 'cfg': r['cfg'], 'sampler': r['sampler'], 'sigmas': r['sigmas'],
            **({'prompt': r['prompt'], 'negative_prompt': r['negative_prompt'], 'colour_association': r['colour_association']}
               if stage == 'rgb' else {'prompt': '', 'reference_downscale_factor': 1.0})}


def _stage_path(request: dict, stage: str, input_sha: str, backend: dict) -> Path:
    signature = graphs.digest({'input_sha256': input_sha, 'profile': _profile(request['recipe'], stage),
                               'geometry': request['geometry'], 'backend': backend,
                               # Nonce is supplied only for an explicit user-requested retry.
                               # An alpha-only nonce preserves the completed RGB cache.
                               'explicit_attempt': request['recipe'].get('stage_attempts', {}).get(stage)})
    return Path(request['takes_root']) / 'cache' / stage / signature


def _verify_asset(asset: dict) -> None:
    if _sha(Path(asset['path'])) != asset['sha256']:
        raise ElementError('A cached element asset changed; it was not silently regenerated.')
    manifest = Path(asset['frame_manifest'])
    if _sha(manifest) != asset['frame_manifest_sha256']:
        raise ElementError('A cached decoded-frame manifest changed.')
    for path, digest in _load(manifest).items():
        if _sha(Path(path)) != digest:
            raise ElementError('A cached decoded element frame changed.')


def _cached(path: Path) -> dict | None:
    file = path / 'stage.json'
    if not file.exists():
        return None
    state = _load(file)
    if state.get('status') == 'done':
        asset = state['asset']
        _verify_asset(asset)
        return asset
    raise AlreadySubmitted(f"Element stage is {state.get('status')}; inspect its saved job before an explicit new attempt.")


def _validate_output(path: Path, request: dict, stage: str, directory: Path) -> dict:
    frames, fps = _read_video(path)
    r, g = request['recipe'], request['geometry']
    if abs(fps - r['gen_fps']) > .01 or len(frames) != r['model_frames']:
        raise ElementError(f'Model output timing does not match the frozen element recipe: expected '
                           f'{r["model_frames"]} frames at {r["gen_fps"]}fps, got {len(frames)} at {fps:g}fps.')
    if any(im.shape != (g['height'], g['width'], 3) for im in frames):
        raise ElementError(f'Model output dimensions do not match the frozen element canvas '
                           f'{g["width"]}×{g["height"]}.')
    if not any(im.max() > 0 for im in frames[:r['active_frames']]):
        raise ElementError('Model returned an empty active element; inspect the raw output before a new attempt.')
    alpha_stats = {}
    if stage == 'alpha':
        maxima, counts, fractions = [], [], []
        # Validate the entire downloaded model sequence, including discarded
        # padding. A per-frame bound prevents a coloured frame being diluted by
        # a long black tail or by aggregation over the rest of the clip.
        for im in frames:
            spread = np.ptp(im.astype(np.int16), axis=2)
            count = int(np.count_nonzero(spread > ALPHA_CHANNEL_SPREAD_LIMIT))
            maxima.append(int(spread.max())); counts.append(count); fractions.append(count / spread.size)
        alpha_spread, outlier_fraction = max(maxima), max(fractions)
        if (alpha_spread > ALPHA_CHANNEL_OUTLIER_HARD_LIMIT
                or outlier_fraction > ALPHA_CHANNEL_OUTLIER_FRACTION_LIMIT):
            raise ElementError(f'Alpha Gen returned coloured RGB content rather than a near-grayscale matte '
                               f'(maximum channel spread {alpha_spread}; allowed {ALPHA_CHANNEL_SPREAD_LIMIT} '
                               f'normally, hard limit {ALPHA_CHANNEL_OUTLIER_HARD_LIMIT} of 255; '
                               f'outlier limit {ALPHA_CHANNEL_OUTLIER_FRACTION_LIMIT:.3%} per frame, '
                               f'observed {outlier_fraction:.4%}). Inspect the downloaded alpha; '
                               'no automatic retry was started.')
        alpha_stats = {'alpha_channel_spread_max': alpha_spread,
                       'alpha_channel_spread_limit': ALPHA_CHANNEL_SPREAD_LIMIT,
                       'alpha_channel_spread_hard_limit': ALPHA_CHANNEL_OUTLIER_HARD_LIMIT,
                       'alpha_channel_outlier_fraction_limit': ALPHA_CHANNEL_OUTLIER_FRACTION_LIMIT,
                       'alpha_channel_outlier_count_total': sum(counts),
                       'alpha_channel_outlier_count_max': max(counts),
                       'alpha_channel_outlier_fraction_max': outlier_fraction,
                       'alpha_channel_statistics_frames': len(frames),
                       'alpha_validation_policy': 'near_gray_sparse_chroma_v2',
                       'alpha_scalar_conversion': 'rounded_channel_mean_no_gamma'}
    fd = directory / 'frames'; fd.mkdir(exist_ok=True)
    for i, im in enumerate(frames[:r['active_frames']]):
        if stage == 'alpha':
            # Codec-decoded data values: no gamma, blur, levels or simulation-alpha cap.
            im = np.uint8(np.floor(im.astype(np.float32).mean(2) + .5))
        if not cv2.imwrite(str(fd / f'{i:05d}.png'), im):
            raise ElementError('Could not preserve decoded element frames.')
    fm = directory / 'frames.json'
    _write(fm, {str(p.resolve()): _sha(p) for p in sorted(fd.glob('*.png'))})
    return {'path': str(path.resolve()), 'sha256': _sha(path), 'frames_dir': str(fd.resolve()),
            'frame_manifest': str(fm.resolve()), 'frame_manifest_sha256': _sha(fm),
            'frames': r['active_frames'], 'model_frames': r['model_frames'], 'fps': fps,
            'width': g['width'], 'height': g['height'], 'content_rect': g['content_rect'],
            'decoded_bits': 8, 'colour_space': 'linear_data' if stage == 'alpha' else 'srgb',
            'colour_association': 'opacity' if stage == 'alpha' else r['colour_association'],
            **alpha_stats}


def _downloaded_recovery_context(take_dir: Path, stage: str):
    """Validate local provenance only. Never reads a connection or contacts it."""
    if stage not in ('rgb', 'alpha'): raise ElementError('Choose RGB or alpha for local revalidation.')
    td = Path(take_dir).resolve(); request, state = _load(td / 'request.json'), _load(td / 'state.json')
    if (_sha(td / 'request.json') != state.get('request_sha256')
            or _sha(Path(request['guide']['path'])) != request['guide']['sha256']
            or _sha(Path(request['source_guide']['path'])) != request['source_guide']['sha256']):
        raise ElementError('Frozen element request/guide changed; local recovery is blocked.')
    backend = request.get('backend') or state.get('backend')
    if not backend or state.get('backend') != backend:
        raise ElementError('Downloaded stage backend provenance does not match its frozen request.')
    if stage == 'alpha':
        rgb = state.get('assets', {}).get('rgb')
        if not rgb: raise ElementError('A verified completed RGB input is required before alpha recovery.')
        _verify_asset(rgb); source = Path(rgb['path'])
    else: source = Path(request['guide']['path'])
    sp = _stage_path(request, stage, _sha(source), backend).resolve()
    if (Path(state.get('stages', {}).get(stage, {}).get('cache_dir', '')).resolve() != sp
            or not sp.is_relative_to(Path(request['takes_root']).resolve() / 'cache' / stage)
            or not (sp / 'claim').is_file()):
        raise ElementError('Downloaded stage claim/cache provenance is missing or changed.')
    receipt = _load(sp / 'stage.json')
    if (receipt.get('stage') != stage or receipt.get('backend') != backend
            or receipt.get('input_sha256') != _sha(source)
            or receipt.get('graph_sha256') != _sha(sp / 'graph.json')
            or not receipt.get('job') or not receipt.get('idempotency_key')):
        raise ElementError('Downloaded stage job/input/graph provenance is missing or changed.')
    if receipt.get('status') not in ('failed', 'done') or state.get('status') in ('running', 'submitting', 'uncertain'):
        raise ElementError('Only a known completed downloaded stage can be revalidated; inspect uncertain jobs first.')
    files = [p for p in (sp / 'raw').iterdir() if p.is_file()]
    if len(files) != 1 or files[0].suffix.lower() != '.mp4':
        raise ElementError('Local revalidation requires exactly one retained downloaded MP4; no model job was started.')
    return td, request, state, sp, receipt, files[0]


def downloaded_stage_candidate(take_dir: Path, stage: str) -> dict:
    """Read-only candidate metadata; validation can still reject its content."""
    _, _, _, _, receipt, raw = _downloaded_recovery_context(take_dir, stage)
    if receipt.get('status') != 'failed' or receipt.get('error') != 'ElementError':
        raise ElementError('No retained local output-validation failure needs revalidation for this stage.')
    return {'stage': stage, 'raw_sha256': _sha(raw), 'job': receipt['job'],
            'output_filename': raw.name, 'error_detail': receipt.get('error_detail', receipt.get('error', ''))}


def recover_downloaded_stage(take_dir: Path, stage: str, *, expected_raw_sha256: str) -> dict:
    """Explicit zero-inference revalidation of a retained completed output.

    No retry/resubmission. Original inputs/raw/graph/claim/job/idempotency stay
    intact. Decoded frames go into a new directory and old metadata is saved.
    """
    if not isinstance(expected_raw_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_raw_sha256):
        raise ElementError('Supply the reviewed downloaded-output SHA256 for local revalidation.')
    with _lock:
        td, request, state, sp, receipt, raw = _downloaded_recovery_context(take_dir, stage)
        if _sha(raw) != expected_raw_sha256:
            raise ElementError('The downloaded model output changed; local recovery is blocked.')
        if receipt['status'] == 'done':
            asset = receipt.get('asset')
            if not asset or asset.get('sha256') != expected_raw_sha256:
                raise ElementError('Completed local recovery metadata does not match the reviewed output.')
            _verify_asset(asset)
            if state.get('assets', {}).get(stage) != asset:
                raise ElementError('Completed stage/take metadata differ; inspect the saved recovery.')
            return inspect(td)
        if state.get('status') != 'failed' or receipt.get('error') != 'ElementError':
            raise ElementError('This is not a retained local output-validation failure; inspect its saved job first.')
        recovery = sp / 'recovery' / uuid.uuid4().hex
        recovery.mkdir(parents=True)
        shutil.copy2(sp / 'stage.json', recovery / 'stage-before.json')
        shutil.copy2(td / 'state.json', recovery / 'take-before.json')
        before = {str(p): _sha(p) for p in (td / 'request.json', sp / 'graph.json', sp / 'claim', raw,
                                           Path(request['guide']['path']), Path(request['source_guide']['path']))}
        asset = _validate_output(raw, request, stage, recovery)
        if any(_sha(Path(path)) != sha for path, sha in before.items()):
            raise ElementError('A frozen input/output changed during local revalidation; nothing was accepted.')
        if stage == 'alpha': _verify_asset(state['assets']['rgb'])
        evidence = {'version': 1, 'kind': 'local_output_revalidation', 'stage': stage,
                    'take_id': request['id'], 'provider_job': receipt['job'],
                    'idempotency_key': receipt['idempotency_key'], 'raw_sha256': expected_raw_sha256,
                    'new_inference_submissions': 0, 'original_files': before,
                    'previous_status': receipt['status'], 'previous_error': receipt.get('error_detail', receipt.get('error')),
                    'validated_at': time.time(), 'asset': asset}
        _write(recovery / 'recovery.json', evidence)
        development_gate.note_local_recovery(request, stage, receipt, asset, recovery / 'recovery.json')
        receipt.update(status='done', asset=asset, local_recovery=str(recovery / 'recovery.json'))
        _write(sp / 'stage.json', receipt)
        state.setdefault('assets', {})[stage] = asset
        state['stages'][stage].update(status='done', recovered_locally=True)
        state['status'] = 'done' if {'rgb', 'alpha'} <= set(state['assets']) else 'prepared'
        state['last_local_recovery'] = str(recovery / 'recovery.json'); state.pop('error', None)
        _write(td / 'state.json', state)
        return inspect(td)


def _execute_with_client(cfg, graph, uploads, out_node, out_dir, on_status, idempotency_key):
    c = comfy.client(config=cfg)
    try:
        workflow = c.workflows.from_json(graph)
        for name, (field, path) in uploads.items():
            workflow.set_input(name, field, c.assets.from_file(Path(path)))
        # Exactly one submit, with identity persisted by the caller before this function.
        job = c.submit(workflow, idempotency_key=idempotency_key)
        on_status(job.id, 'running')
        job.result()
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for i, out in enumerate(job.get_outputs(out_node)):
            path = out_dir / f'{out_node}_{i}{Path(out.name or "").suffix or ".mp4"}'
            out.to_file(path)
            files.append(path)
        return files
    finally:
        c.close()


def run(take_dir: Path, *, executor: Callable | None = None, catalog: dict | None = None,
        on_status: Callable | None = None) -> dict:
    """Run only unsubmitted stages. Completed RGB and alpha are independently reusable.

    Injected executors must accept graph/uploads/out_node/out_dir/on_status/
    idempotency_key keyword arguments and return output Paths; tests supply a
    synthetic catalogue. No injected executor falls through to a network client.
    """
    td = Path(take_dir)
    request, state = _load(td / 'request.json'), _load(td / 'state.json')
    if _sha(td / 'request.json') != state['request_sha256'] or _sha(Path(request['guide']['path'])) != request['guide']['sha256']:
        raise ElementError('Frozen element request or guide changed; prepare a new take.')
    if state['status'] == 'done':
        for asset in state['assets'].values():
            _verify_asset(asset)
        return inspect(td)
    if state['status'] in ('submitting', 'running', 'failed', 'uncertain'):
        raise AlreadySubmitted('This take already attempted generation; inspect the saved job rather than automatically retrying.')
    cfg = None
    if executor is None:
        cfg = connections.current()
        if not cfg.configured:
            raise ElementError('Connect Comfy before generating elements.')
        backend = _connection_identity(cfg)
        if request.get('backend') and backend != request['backend']:
            raise ElementError('The selected Comfy account/target changed; prepare a new take.')
    else:
        backend = request.get('backend') or {'mode': 'injected', 'identity': 'test-executor'}
    rgb_path = _stage_path(request, 'rgb', request['guide']['sha256'], backend)
    rgb = _cached(rgb_path)
    alpha_path = _stage_path(request, 'alpha', rgb['sha256'], backend) if rgb else None
    alpha = _cached(alpha_path) if alpha_path else None
    needed = [] if alpha else ['alpha'] if rgb else ['rgb', 'alpha']
    if needed:
        if catalog is None:
            if executor is not None:
                raise ElementError('An injected executor needs an explicit capability catalogue.')
            try:
                catalog = connections.node_catalog(cfg)
            except Exception:
                raise ElementError('Could not verify the selected Comfy model catalogue. No generation started.') from None
        checks = check_capabilities(catalog, request['recipe'])
        missing = [v for v in checks['checks'] if v['id'] in needed and v['status'] != 'ready']
        if missing:
            detail = '; '.join(d for v in missing for d in v['details'])
            raise ElementError('Element dependencies are not ready. No generation started. ' + detail)
        state['capabilities'] = checks
    state['backend'] = backend

    def persist(status=None, **kw):
        state.update(kw)
        if status:
            state['status'] = status
        _write(td / 'state.json', state)
        if on_status:
            try:
                on_status(copy.deepcopy(state))
            except Exception:
                # Progress/UI delivery must not turn a submitted job into a new
                # charge on retry. The saved stage remains authoritative.
                pass

    for stage, cached in [('rgb', rgb), ('alpha', alpha)]:
        if cached:
            state['assets'][stage] = cached
            state['stages'][stage] = {'status': 'done', 'cached': True}
            continue
        source = Path(request['guide']['path']) if stage == 'rgb' else Path(state['assets']['rgb']['path'])
        sp = _stage_path(request, stage, _sha(source), backend)
        g, uploads, output = graphs.graph(stage, source, request['recipe'], width=request['geometry']['width'],
                                          height=request['geometry']['height'], prefix=f'bang-on/elements/{stage}_{sp.name[:16]}')
        def awaiting(phase):
            # Development review is not rendering and creates no provider job.
            state['status'] = phase
            _write(td / 'state.json', state)
            if on_status:
                try: on_status(phase)
                except Exception: pass
        try:
            permit = development_gate.before_stage(request, stage, g, uploads, output, sp, awaiting=awaiting)
        except development_gate.GateError as exc:
            persist('blocked', error=str(exc))
            raise ElementError(str(exc)) from exc
        # O_EXCL protects identical content from concurrent workers/processes.
        sp.mkdir(parents=True, exist_ok=True)
        try:
            claim = os.open(sp / 'claim', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(claim)
        except FileExistsError:
            asset = _cached(sp)
            if asset:
                state['assets'][stage] = asset
                state['stages'][stage] = {'status': 'done', 'cached': True}
                continue
            raise AlreadySubmitted('An identical element stage is already claimed; no duplicate job was started.')
        _write(sp / 'graph.json', g)
        job_state = {'stage': stage, 'status': 'submitting', 'idempotency_key': str(uuid.uuid4()),
                     'input_sha256': _sha(source), 'graph_sha256': _sha(sp / 'graph.json'), 'backend': backend,
                     'uploads': {k: [field, str(path), _sha(path)] for k, (field, path) in uploads.items()}, 'started_at': time.time()}
        _write(sp / 'stage.json', job_state)
        state['stages'][stage] = {'status': 'submitting', 'cache_dir': str(sp)}
        persist('submitting')

        def status(job_id, value):
            job_state.update(job=job_id, status='running')
            _write(sp / 'stage.json', job_state)
            state['stages'][stage].update(job=job_id, status='running')
            if permit: permit.update('running', job=job_id)
            persist('running')

        execution_returned = False
        try:
            if permit: permit.reserve(job_state['idempotency_key'])
            call = executor or (lambda **kw: _execute_with_client(cfg, **kw))
            files = call(graph=g, uploads=uploads, out_node=output, out_dir=sp / 'raw',
                         on_status=status, idempotency_key=job_state['idempotency_key'])
            execution_returned = True
            if len(files) != 1:
                raise ElementError('The element stage must return exactly one video.')
            output_path = Path(files[0]).resolve()
            if not output_path.is_relative_to(sp.resolve()):
                destination = sp / 'raw' / ('actual' + output_path.suffix)
                destination.parent.mkdir(exist_ok=True)
                shutil.copy2(output_path, destination)
                output_path = destination
            asset = _validate_output(output_path, request, stage, sp)
            job_state.update(status='done', asset=asset, finished_at=time.time())
            _write(sp / 'stage.json', job_state)
            state['assets'][stage] = asset
            state['stages'][stage].update(status='done', cached=False)
            if permit: permit.update('done')
            persist('prepared')
        except Exception as exc:
            # A receipt proves submission, not completion. A polling/transport
            # failure can leave a known job running and must stop further spend.
            # Successful executor return proves the remote work finished even
            # if local output validation fails. JobFailed is the SDK's explicit
            # non-success *terminal* result; other raised executor errors remain
            # uncertain whether or not a job ID was already recorded.
            failure = 'failed' if execution_returned or isinstance(exc, JobFailed) else 'uncertain'
            job_state.update(status=failure, error=type(exc).__name__, finished_at=time.time())
            detail = str(exc)[:700] if isinstance(exc, ElementError) else type(exc).__name__
            job_state['error_detail'] = detail
            _write(sp / 'stage.json', job_state)
            state['stages'][stage].update(status=failure)
            if permit: permit.update(failure)
            persist(failure, error=f'{stage}: {detail}; inspect the saved stage before a new explicit attempt.')
            raise ElementError(state['error']) from exc
    persist('done', finished_at=time.time())
    return inspect(td)
