"""Reviewable raw-plate AlphaGen people/foreground mattes, separate from FX.

AlphaGen accepts RGB alone, not SAM2 masks or a subject-selection prompt. This
lane never replaces photography and never weakens elements.prepare's plate-free
FX invariant. A completed matte still needs explicit review/geometry approval.
"""
from __future__ import annotations

import copy
import math
import os
from pathlib import Path
import threading
import time
import uuid

import cv2
import numpy as np

from . import connections, element_graphs as graphs, element_jobs, elements, media, projects

PROFILE = 'people-alpha-raw-rgb-png-v2'
MODEL_ROLES = ('transformer', 'text_encoder', 'video_vae', 'audio_vae', 'alpha')


def _recipe(p, seed=42, active_frames=49):
    settings = p.get('settings', {})
    return graphs.normalize_recipe({'seed': seed, 'active_frames': active_frames,
        'models': {k: v for k, v in settings.get('element_models', {}).items() if k in MODEL_ROLES},
        'model_revisions': {k: v for k, v in settings.get('element_model_revisions', {}).items() if k in MODEL_ROLES}})


def plan(p, seed=42):
    profile = element_jobs.source_profile(p)
    n = p['frames']; times = np.asarray(profile['timestamps'], np.float64)
    duration = float(profile['duration'])
    if n < 1 or len(times) != n or not np.isfinite(times).all() or times[0] != 0 or np.any(np.diff(times) <= 0):
        raise ValueError('People AlphaGen needs a valid full source timeline beginning at zero.')
    if not np.isfinite(duration) or duration <= times[-1]:
        raise ValueError('People AlphaGen needs valid source frame durations.')
    if duration > 5 + 1e-8:
        raise ValueError('People AlphaGen currently covers a whole shot of five seconds or less. Import a shorter shot; no frames were silently truncated.')
    active = max(1, int(math.ceil(duration * 24 - 1e-8)))
    recipe = _recipe(p, seed, active)
    w, h = int(profile['width']), int(profile['height'])
    if w < 1 or h < 1:
        raise ValueError('Invalid source dimensions.')
    scale = min(1., 1920/max(w, h), 1088/min(w, h))
    sw, sh = max(1, round(w*scale)), max(1, round(h*scale))
    width, height = max(64, 64*math.ceil(sw/64)), max(64, 64*math.ceil(sh/64))
    x, y = (width-sw)//2, (height-sh)//2
    source_frames = [int(np.argmin(np.abs(times-i/24))) for i in range(active)]
    native_to_model = [min(active-1, int(math.floor(t*24+.5))) for t in times]
    signature = graphs.digest([PROFILE, profile, {k: recipe['models'][k] for k in MODEL_ROLES},
                               recipe['model_revisions'], seed, [width, height, x, y, sw, sh]])
    model_profile_hash = graphs.digest([PROFILE, {k: recipe['models'][k] for k in MODEL_ROLES}, recipe['model_revisions']])
    return {'signature': signature, 'profile': PROFILE, 'seed': seed, 'frames': n,
            'model_profile_hash': model_profile_hash, 'model_source_colour': 'RGB8 display proxy; native photographic pixels are not regenerated',
            'duration': duration, 'start_frame': 0, 'end_frame': n-1,
            'active_frames': active, 'model_frames': recipe['model_frames'], 'fps': 24,
            'width': width, 'height': height, 'content_rect': [x, y, sw, sh],
            'native_width': w, 'native_height': h, 'source_times': times.tolist(),
            'source_pts': copy.deepcopy(profile.get('source_pts')),
            'source_fps': profile.get('fps_rational', str(profile['fps'])),
            'source_frames': source_frames, 'native_to_model': native_to_model,
            'model_sampling_note': '24fps model motion mapped to every native source frame; resampling does not recover finer temporal detail.',
            'selection': 'model_foreground', 'selection_note': 'Raw RGB only. AlphaGen chooses its main subject; no mask or prompt selects a person.'}


def capabilities(p, *, connection=None, catalog=None):
    cfg = connection or connections.current()
    if not cfg.configured:
        return {'ready': False, 'checks': [], 'error': 'Connect Comfy before running People AlphaGen.'}
    try:
        if catalog is None: catalog = connections.node_catalog(cfg)
        graph = _graph(Path('people-check.mp4'), _recipe(p), width=1024, height=576)
        status, details = connections.check_graph(catalog, graph)
        return {'ready': status == 'ready', 'checks': [{'id': 'alpha', 'label': 'People AlphaGen', 'status': status, 'details': details, 'required': True}],
                'note': 'Alpha-only dependencies. Model registration is not proof of matte edge quality or installed weight revision.'}
    except Exception:
        return {'ready': False, 'checks': [], 'error': 'Could not verify People AlphaGen models. No generation started.'}


def _graph(source, recipe, *, width, height, prefix='bang-on/people-alpha/check'):
    graph = graphs.graph('alpha', source, recipe, width=width, height=height, prefix=prefix)[0]
    graph.pop('video')
    graph['save'] = {'class_type': 'SaveImage', 'inputs': {'images': ['decode', 0], 'filename_prefix': prefix}}
    return graph


def _bound(root, value, directory=False):
    root = Path(root).resolve(); path = (root/value).resolve()
    if not path.is_relative_to(root) or path == root or not (path.is_dir() if directory else path.is_file()):
        raise ValueError('A frozen People AlphaGen asset is missing or outside this project.')
    return path


def _row(p, tid):
    row = next((r for r in p.get('people_alpha_takes', []) if r['id'] == tid), None)
    if row is None: raise ValueError('Prepare this People AlphaGen version first.')
    return row


def _read(root, row):
    td = _bound(root, row['take_dir'], True)
    if elements._sha(td/'request.json') != row['request_sha256']:
        raise ValueError('The frozen People AlphaGen request changed; prepare a new version.')
    request, state = elements._load(td/'request.json'), elements._load(td/'state.json')
    if request['plan'] != row['plan'] or request['signature'] != row['signature']:
        raise ValueError('The frozen People AlphaGen timeline changed; prepare a new version.')
    for name, sha in request['inputs'].items():
        if elements._sha(_bound(td, name)) != sha:
            raise ValueError('A frozen People AlphaGen input changed; prepare a new version.')
    return td, request, state


def _verify_source(p, request):
    frame_dir = projects.plate_dir(p)
    hashes = request['provenance']['source_frames_sha256']
    if set(hashes) != {str(frame) for frame in range(p['frames'])}:
        raise ValueError('People AlphaGen source coverage changed; prepare a new version.')
    for frame, sha in hashes.items():
        if elements._sha(media.frame_path(frame_dir, int(frame))) != sha:
            raise ValueError('Source photography changed since People AlphaGen preparation. Prepare a new version.')
    if p.get('native_profile'):
        native = _bound(projects.pdir(p['id']), 'native/frames', True)
        hashes = request['provenance'].get('native_frames_sha256', {})
        if set(hashes) != {str(frame) for frame in range(p['frames'])}:
            raise ValueError('People AlphaGen native source coverage changed; prepare a new version.')
        for frame, sha in hashes.items():
            if elements._sha(media.frame_path(native, int(frame))) != sha:
                raise ValueError('Native source photography changed since People AlphaGen preparation. Prepare a new version.')


def prepare(pid, seed=42, *, connection=None):
    """Freeze a full short RGB plate locally. No model call or upload."""
    with projects._lock(pid):
        p = projects.load(pid)
        if projects.generation_active(p): raise ValueError('Wait for the current generation.')
        spec = plan(p, seed); cfg = connection or connections.current()
        backend = elements._connection_identity(cfg)
        frame_dir = projects.plate_dir(p); source_hashes, native_hashes = {}, {}
        for frame in range(p['frames']):
            source_hashes[str(frame)] = elements._sha(media.frame_path(frame_dir, frame))
        if p.get('native_profile'):
            native = _bound(projects.pdir(pid), 'native/frames', True)
            for frame in range(p['frames']):
                native_hashes[str(frame)] = elements._sha(media.frame_path(native, frame))
        tid = graphs.digest([spec['signature'], source_hashes, native_hashes, backend])
        old = next((r for r in p.get('people_alpha_takes', []) if r['id'] == tid), None)
        if old:
            _read(projects.pdir(pid), old)
            return copy.deepcopy(old)
        root = projects.pdir(pid); td = root/'people_alpha'/'takes'/tid; td.mkdir(parents=True)
        source = td/'inputs'/'source.mp4'; source.parent.mkdir()
        x, y, sw, sh = spec['content_rect']; frames = []
        for frame in spec['source_frames']:
            image = cv2.imread(str(media.frame_path(frame_dir, frame)), cv2.IMREAD_UNCHANGED)
            if image is None or image.shape[:2] != (spec['native_height'], spec['native_width']):
                raise ValueError('Source proxy dimensions differ from its People AlphaGen plan.')
            if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
                raise ValueError('People AlphaGen needs the RGB8 display proxy; native photography is retained separately.')
            if (sw, sh) != (image.shape[1], image.shape[0]): image = cv2.resize(image, (sw, sh), interpolation=cv2.INTER_AREA)
            frames.append(cv2.copyMakeBorder(image, y, spec['height']-sh-y, x, spec['width']-sw-x, cv2.BORDER_CONSTANT, value=0))
        frames += [frames[-1]]*(spec['model_frames']-spec['active_frames'])
        elements._encode(source, frames)
        recipe = _recipe(p, seed, spec['active_frames'])
        graph = _graph(source, recipe, width=spec['width'], height=spec['height'], prefix=f'bang-on/people-alpha/{tid[:16]}')
        elements._write(td/'graph.json', graph)
        request = {'version': 1, 'id': tid, 'signature': spec['signature'], 'plan': spec, 'recipe': recipe,
                   'backend': backend, 'provenance': {'kind': 'people_alpha_source', 'contains_plate': True,
                   'purpose': 'Alpha-only foreground segmentation, never photographic regeneration',
                   'source_frames_sha256': source_hashes, 'native_frames_sha256': native_hashes,
                   'uses_sam2_mask': False, 'requires_review': True},
                   'inputs': {'inputs/source.mp4': elements._sha(source), 'graph.json': elements._sha(td/'graph.json')}}
        elements._write(td/'request.json', request)
        elements._write(td/'state.json', {'status': 'prepared', 'request_sha256': elements._sha(td/'request.json')})
        row = {'id': tid, 'seed': seed, 'signature': spec['signature'], 'plan': spec, 'status': 'prepared',
               'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'take_dir': str(td.relative_to(root)),
               'request_sha256': elements._sha(td/'request.json')}
        p.setdefault('people_alpha_takes', []).insert(0, row); projects.save(p)
        return copy.deepcopy(row)


def run(td, *, connection=None, executor=None, catalog=None, on_status=None):
    """One alpha submission. A receipt/claim forbids automatic paid retries."""
    td = Path(td); request = elements._load(td/'request.json'); state = elements._load(td/'state.json')
    if elements._sha(td/'request.json') != state.get('request_sha256'):
        raise ValueError('The frozen People AlphaGen request changed.')
    for name, sha in request['inputs'].items():
        if elements._sha(_bound(td, name)) != sha: raise ValueError('A frozen People AlphaGen input changed.')
    if state['status'] == 'done':
        _verify_output(td, request, state)
        return state
    if (td/'claim').exists() or state['status'] in ('submitting', 'rendering', 'failed', 'uncertain'):
        raise elements.AlreadySubmitted('This People AlphaGen version already attempted submission. Inspect its receipt; choose a new seed for an explicit new version.')
    cfg = connection or (connections.current() if executor is None else None)
    if cfg is not None and (not cfg.configured or elements._connection_identity(cfg) != request['backend']):
        raise ValueError('The selected Comfy account/target changed or is disconnected. Prepare a new People AlphaGen version.')
    if catalog is None:
        if executor is not None: raise ValueError('An injected People AlphaGen executor needs an explicit catalogue.')
        catalog = connections.node_catalog(cfg)
    graph = elements._load(td/'graph.json'); status, details = connections.check_graph(catalog, graph)
    elements._write(td/'capabilities.json', {'status': status, 'details': details})
    if status != 'ready':
        raise ValueError('People AlphaGen dependencies are not ready. No upload or generation started. '+ '; '.join(details))
    fd = os.open(td/'claim', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); os.close(fd)
    receipt = {'status': 'submitting', 'idempotency_key': uuid.uuid4().hex,
               'backend': request['backend'], 'request_sha256': elements._sha(td/'request.json'),
               'input_sha256': request['inputs']['inputs/source.mp4'], 'graph_sha256': request['inputs']['graph.json']}
    def persist(value):
        if value in ('submitting', 'done'): state.pop('error', None)
        state.update(status=value); elements._write(td/'state.json', state)
        elements._write(td/'submission.json', receipt)
        if on_status:
            try: on_status(copy.deepcopy(state))
            except Exception: pass
    persist('submitting')
    def progress(job, _value):
        receipt.update(job=job, status='rendering'); persist('rendering')
    try:
        call = executor or (lambda **kw: elements._execute_with_client(cfg, **kw))
        files = call(graph=graph, uploads={'control': ('file', td/'inputs/source.mp4')}, out_node='save', out_dir=td/'raw', on_status=progress, idempotency_key=receipt['idempotency_key'])
        spec = request['plan']
        if len(files) != spec['model_frames']:
            raise ValueError('People AlphaGen PNG frame count differs from the frozen full-shot plan.')
        decoded, raw_hashes, actual_hashes = [], {}, {}
        model_dir = td/'model_masks'; model_dir.mkdir()
        raw_dir = td/'raw_model'; raw_dir.mkdir()
        for index, file in enumerate(files):
            path = Path(file); raw = path.read_bytes()
            if raw[:8] != b'\x89PNG\r\n\x1a\n': raise ValueError('People AlphaGen must return lossless PNG matte frames.')
            actual = raw_dir/f'{index:05d}.png'; actual.write_bytes(raw); actual_hashes[actual.name] = elements._sha(actual)
            image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
            if image is None or image.dtype != np.uint8 or image.shape[:2] != (spec['height'], spec['width']):
                raise ValueError('People AlphaGen PNG dimensions/precision differ from the frozen plan.')
            if image.ndim == 3:
                if image.shape[2] != 3 or int(np.ptp(image.astype(np.int16), axis=2).max()) > 4:
                    raise ValueError('People AlphaGen returned coloured RGB rather than a grayscale opacity matte.')
                image = np.uint8(np.floor(image.astype(np.float32).mean(2)+.5))
            if image.ndim != 2: raise ValueError('People AlphaGen returned an invalid opacity image.')
            target = model_dir/f'{index:05d}.png'
            if not cv2.imwrite(str(target), image): raise ValueError('Could not preserve lossless People AlphaGen model frames.')
            raw_hashes[target.name] = elements._sha(target); decoded.append(image)
        elements._write(td/'model_masks.json', raw_hashes)
        elements._write(td/'raw_model.json', actual_hashes)
        x, y, w, h = spec['content_rect']
        if not any(im[y:y+h, x:x+w].max() > 0 for im in decoded[:spec['active_frames']]):
            raise ValueError('People AlphaGen returned an entirely empty foreground matte; inspect its raw output.')
        review = td/'alpha.mp4'; elements._encode(review, [cv2.cvtColor(im, cv2.COLOR_GRAY2BGR) for im in decoded])
        masks = td/'native_masks'; masks.mkdir()
        hashes = {}
        for frame, index in enumerate(spec['native_to_model']):
            alpha = decoded[index][y:y+h, x:x+w]
            if alpha.shape != (spec['native_height'], spec['native_width']):
                alpha = cv2.resize(alpha, (spec['native_width'], spec['native_height']), interpolation=cv2.INTER_LINEAR)
            target = masks/f'{frame:05d}.png'
            if not cv2.imwrite(str(target), alpha): raise ValueError('Could not preserve native people matte frames.')
            hashes[target.name] = elements._sha(target)
        elements._write(td/'native_masks.json', hashes)
        asset = {'directory': 'native_masks', 'frames': spec['frames'], 'width': spec['native_width'], 'height': spec['native_height'],
                 'model_frames': spec['model_frames'], 'model_fps': 24, 'decoded_bits': 8, 'colour_space': 'linear_data',
                 'selection': 'model_foreground', 'alpha_path': 'alpha.mp4', 'alpha_sha256': elements._sha(review),
                 'model_directory': 'model_masks', 'model_sha256': elements._sha(td/'model_masks.json'),
                 'raw_directory': 'raw_model', 'raw_sha256': elements._sha(td/'raw_model.json'),
                 'sha256': elements._sha(td/'native_masks.json'), 'model_profile_hash': spec['model_profile_hash'], 'requires_review': True}
        state['asset'] = asset; receipt.update(status='done'); persist('done')
        return state
    except Exception as exc:
        failure = 'failed' if receipt.get('job') else 'uncertain'
        receipt.update(status=failure, error=type(exc).__name__); state['error'] = 'People AlphaGen '+type(exc).__name__+'. Inspect the saved receipt; no automatic retry was made.'
        persist(failure); raise


def _verify_output(td, request, state):
    asset = state['asset']; manifest = _bound(td, 'native_masks.json')
    spec = request['plan']
    if (asset.get('frames'), asset.get('width'), asset.get('height')) != (spec['frames'], spec['native_width'], spec['native_height']):
        raise ValueError('People AlphaGen native matte dimensions or coverage changed.')
    if elements._sha(manifest) != asset['sha256'] or elements._sha(_bound(td, asset['alpha_path'])) != asset['alpha_sha256']:
        raise ValueError('A completed People AlphaGen asset changed.')
    hashes = elements._load(manifest)
    if set(hashes) != {f'{f:05d}.png' for f in range(spec['frames'])}: raise ValueError('People AlphaGen matte coverage is incomplete.')
    for name, sha in hashes.items():
        if elements._sha(_bound(td, Path(asset['directory'])/name)) != sha: raise ValueError('A completed People AlphaGen matte frame changed.')
    model_manifest = _bound(td, 'model_masks.json')
    if elements._sha(model_manifest) != asset['model_sha256']: raise ValueError('People AlphaGen model PNG manifest changed.')
    models = elements._load(model_manifest)
    if set(models) != {f'{frame:05d}.png' for frame in range(spec['model_frames'])}: raise ValueError('People AlphaGen model frame coverage changed.')
    for name, sha in models.items():
        if elements._sha(_bound(td, Path(asset['model_directory'])/name)) != sha: raise ValueError('A People AlphaGen model PNG changed.')
    raw_manifest = _bound(td, 'raw_model.json')
    if elements._sha(raw_manifest) != asset['raw_sha256']: raise ValueError('People AlphaGen raw PNG manifest changed.')
    raw = elements._load(raw_manifest)
    if set(raw) != set(models): raise ValueError('People AlphaGen raw PNG coverage changed.')
    for name, sha in raw.items():
        if elements._sha(_bound(td, Path(asset['raw_directory'])/name)) != sha: raise ValueError('A raw People AlphaGen PNG changed.')
    return asset


def validated_asset(pid, tid):
    p = projects.load(pid); row = _row(p, tid); root = projects.pdir(pid)
    td, request, state = _read(root, row)
    if state.get('status') != 'done' or row.get('status') != 'done': raise ValueError('Complete and review this People AlphaGen version first.')
    if plan(p, row['seed'])['signature'] != row['signature']: raise ValueError('This People AlphaGen version is outdated.')
    _verify_source(p, request)
    asset = _verify_output(td, request, state)
    return {**copy.deepcopy(asset), 'id': tid, 'directory': str((td/asset['directory']).relative_to(root)),
            'source_people_alpha': tid, 'source_signature': row['signature'], 'plan': request['plan']}


def asset_path(pid, tid, kind, frame=None):
    """Serve frozen review media with per-file checks, not an N-frame rehash."""
    row = _row(projects.load(pid), tid); td = _bound(projects.pdir(pid), row['take_dir'], True)
    if elements._sha(_bound(td, 'request.json')) != row['request_sha256']:
        raise ValueError('The frozen People AlphaGen request changed.')
    request, state = elements._load(td/'request.json'), elements._load(td/'state.json')
    if kind == 'source':
        path = _bound(td, 'inputs/source.mp4'); expected = request['inputs']['inputs/source.mp4']
    else:
        if state.get('status') != 'done' or row.get('status') != 'done': raise ValueError('People AlphaGen output is not complete.')
        asset = state['asset']
        if kind == 'alpha': path, expected = _bound(td, asset['alpha_path']), asset['alpha_sha256']
        elif kind in ('frame', 'mask'):
            if isinstance(frame, bool) or not isinstance(frame, int) or not 0 <= frame < request['plan']['frames']:
                raise ValueError('People AlphaGen frame is outside its source range.')
            manifest = _bound(td, 'native_masks.json')
            if elements._sha(manifest) != asset['sha256']: raise ValueError('People AlphaGen frame manifest changed.')
            name = f'{frame:05d}.png'; expected = elements._load(manifest).get(name)
            path = _bound(td, Path(asset['directory'])/name)
        else: raise ValueError('Choose People AlphaGen source, alpha or native mask frame.')
    if not expected or elements._sha(path) != expected: raise ValueError('A frozen People AlphaGen review asset changed.')
    return path


def records(p):
    out = []
    for row in p.get('people_alpha_takes', []):
        item = {k: copy.deepcopy(row[k]) for k in ('id', 'seed', 'signature', 'plan', 'status', 'created', 'error') if k in row}
        try: item['current'] = plan(p, row['seed'])['signature'] == row['signature']
        except (ValueError, KeyError, OSError): item['current'] = False
        item['backend_current'] = True
        if row['status'] == 'prepared':
            try:
                _, request, _ = _read(projects.pdir(p['id']), row)
                item['backend_current'] = request['backend'] == elements._connection_identity(connections.current())
            except (ValueError, KeyError, OSError): item['backend_current'] = False
        if row.get('asset'): item['asset'] = {k: copy.deepcopy(v) for k, v in row['asset'].items() if k not in ('directory', 'alpha_path', 'model_directory', 'raw_directory')}
        out.append(item)
    return out


def start(pid, tid, *, connection=None, catalog=None, executor=None):
    with projects._lock(pid):
        p = projects.load(pid)
        if projects.generation_active(p): raise ValueError('Wait for the current generation.')
        row = _row(p, tid); td, request, state = _read(projects.pdir(pid), row)
        if plan(p, row['seed'])['signature'] != row['signature']: raise ValueError('This People AlphaGen guide is outdated.')
        if row['status'] == 'done': validated_asset(pid, tid); return copy.deepcopy(row)
        if row['status'] != 'prepared': raise ValueError('This People AlphaGen version already ran; inspect its receipt before preparing a new seed.')
        _verify_source(p, request)
        cfg = connection or connections.current()
        if not cfg.configured or request['backend'] != elements._connection_identity(cfg): raise ValueError('The selected Comfy account/target changed or is disconnected. Prepare a new version.')
        rid = uuid.uuid4().hex[:12]
        render = {'id': rid, 'kind': 'people_alpha', 'status': 'queued', 'people_alpha_take': tid, 'pid': os.getpid(),
                  'files': {}, 'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'seed': row['seed'], 'strength': ''}
        row.pop('error', None); row['status'] = 'checking'; p['renders'].insert(0, render); projects.save(p)
    def worker():
        def update(value):
            def save(q):
                _row(q, tid).update(status=value['status'])
                next(r for r in q['renders'] if r['id'] == rid).update(status=value['status'])
            projects.update(pid, save)
        try:
            result = run(td, connection=cfg, executor=executor, catalog=catalog, on_status=update)
            def keep(q):
                _row(q, tid).pop('error', None)
                _row(q, tid).update(status='done', asset=result['asset'])
                next(r for r in q['renders'] if r['id'] == rid).update(status='done')
            projects.update(pid, keep)
        except Exception as exc:
            latest = elements._load(td/'state.json')
            error = latest.get('error') or (str(exc) if isinstance(exc, ValueError) else 'People AlphaGen '+type(exc).__name__+'; inspect the local inputs and model readiness.')
            def fail(q):
                _row(q, tid).update(status=latest['status'], error=error[:400])
                next(r for r in q['renders'] if r['id'] == rid).update(status='error', error=error[:400])
            projects.update(pid, fail)
    threading.Thread(target=worker, daemon=True).start()
    return render
