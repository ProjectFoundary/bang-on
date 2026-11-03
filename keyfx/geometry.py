"""Versioned depth/matte assets for finite 3D collision; no photograph replacement."""
from __future__ import annotations

from pathlib import Path
import hashlib
import io
import json
import shutil
import threading
import time
import uuid
import zipfile

import cv2
import numpy as np

from . import connections, elements, media, people


def _id():
    return uuid.uuid4().hex[:12]


def defaults():
    return {'reference_frame': 0, 'focal_pixels': 900.0, 'depth_scale': 4.0,
            'depth_bias': .05, 'depth': None, 'mattes': [], 'collision_mode': 'matte2d'}


def validate(value: dict, n: int) -> dict:
    out = {**defaults(), **value}
    # Preserve the finite-depth behavior of older saved shots with depth.
    out['collision_mode'] = value.get('collision_mode', 'depth3d' if value.get('depth') else 'matte2d')
    if out['collision_mode'] not in ('matte2d', 'depth3d'):
        raise ValueError('Choose 2D matte or depth-based collision.')
    f = out['reference_frame']
    if isinstance(f, bool) or not isinstance(f, int) or not 0 <= f < n:
        raise ValueError('Choose a reference frame inside the shot.')
    for k, lo, hi in [('focal_pixels', 100, 10000), ('depth_scale', .01, 1000), ('depth_bias', 0, 10)]:
        if isinstance(out[k], bool): raise ValueError(f'Invalid geometry {k}.')
        v = float(out[k])
        if not np.isfinite(v) or not lo <= v <= hi:
            raise ValueError(f'Invalid geometry {k}.')
        out[k] = v
    # Optional exact static CG boxes, expressed in the source camera's XYZ
    # coordinates. Ordinary photographed mattes keep their finite-depth shell.
    for matte in out.get('mattes', []):
        box = matte.get('cameraBox')
        if box is None: continue
        if not matte.get('static') or not isinstance(box, dict) or set(box) != {'center', 'axes', 'halfSize'}:
            raise ValueError('Exact CG boxes require a static matte and center/axes/halfSize.')
        center, axes, half = (np.asarray(box[k], dtype=float) for k in ('center', 'axes', 'halfSize'))
        if (center.shape != (3,) or axes.shape != (3, 3) or half.shape != (3,)
                or not all(np.isfinite(a).all() for a in (center, axes, half))
                or np.any(half <= 0) or np.any(half > 1000) or np.any(np.abs(center) > 1e6)
                or not np.allclose(axes @ axes.T, np.eye(3), atol=1e-5)):
            raise ValueError('Invalid camera-space CG box.')
    return out


def _store_image(root: Path, data: bytes, name: str) -> tuple[Path, np.ndarray]:
    if len(data) > 64 * 1024 * 1024:
        raise ValueError('Use an image smaller than64MB.')
    im = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if im is None or im.dtype not in (np.uint8, np.uint16) or max(im.shape[:2]) > 8192:
        raise ValueError('Use an8/16-bit image up to8192pixels per side.')
    if im.ndim == 3:
        im = im[..., 3] if im.shape[2] == 4 else cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    cv2.imwrite(str(path), im)
    return path, im


def import_depth(project_dir: Path, data: bytes, *, kind='inverse', reference_frame=0) -> dict:
    if kind not in ('inverse', 'distance'):
        raise ValueError('Depth must be inverse or distance data.')
    rid = _id()
    path, im = _store_image(project_dir / 'geometry' / rid, data, 'depth.png')
    if float(im.max()) == float(im.min()):
        raise ValueError('The depth image is constant; provide a varying depth pass.')
    return {'id': rid, 'file': str(path.relative_to(project_dir)), 'kind': kind,
            'reference_frame': reference_frame, 'width': im.shape[1], 'height': im.shape[0],
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'units': 'relative monocular depth'}


def import_matte(project_dir: Path, data: bytes, filename: str, n: int, *, name='Object', soft=False) -> dict:
    rid = _id()
    root = project_dir / 'geometry' / rid / 'masks'
    images = []
    if filename.lower().endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = sorted((x for x in archive.infolist() if not x.is_dir() and Path(x.filename).suffix.lower() in ('.png', '.jpg', '.jpeg')), key=lambda x: x.filename)
            if len(members) not in (1, n) or sum(x.file_size for x in members) > 512 * 1024 * 1024:
                raise ValueError('Matte ZIP must contain one static image or one image per source frame, up to512MB decoded.')
            images = [archive.read(x) for x in members]
    else:
        images = [data]
    shapes = set()
    for i, raw in enumerate(images):
        path, im = _store_image(root, raw, f'{i:05d}.png')
        shapes.add(im.shape)
    if len(shapes) != 1:
        shutil.rmtree(root.parent)
        raise ValueError('All matte frames must have the same resolution.')
    h, w = next(iter(shapes))
    return {'id': rid, 'name': str(name)[:80], 'frames': len(images), 'static': len(images) == 1,
            'directory': str(root.relative_to(project_dir)), 'width': w, 'height': h,
            'kind': 'soft' if soft else 'hard', 'thickness': .2, 'softRadius': 8, 'drag': 24,
            'role': 'both', 'depthOffset': 0, 'sha256': hashlib.sha256(b''.join(hashlib.sha256(x).digest() for x in images)).hexdigest()}


def depth_data(project_dir: Path, cfg: dict) -> dict:
    entry = cfg.get('depth')
    if not entry:
        raise ValueError('Import or generate a depth pass before using3D collision.')
    path = (project_dir / entry['file']).resolve()
    if project_dir.resolve() not in path.parents:
        raise ValueError('Invalid depth asset.')
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry.get('sha256'):
        raise ValueError('The immutable depth asset changed or is missing. Import the depth pass again.')
    im = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if im is None:
        raise ValueError('The depth asset is missing.')
    h, w = im.shape[:2]
    scale = min(1., 512 / max(h, w))
    im = cv2.resize(im, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    values = im.astype(np.float32) / np.iinfo(im.dtype).max
    return {'width': im.shape[1], 'height': im.shape[0], 'values': values.ravel().tolist(),
            'kind': entry['kind'], 'scale': cfg.get('depth_scale', 4.), 'bias': cfg.get('depth_bias', .05)}


def depth_graph(prefix):
    def node(t, **kw): return {'class_type': t, 'inputs': kw}
    return {'input': node('LoadImage', image='reference.png'),
            'model': node('LoadDA3Model', model_name='depth_anything_3_mono_large.safetensors', weight_dtype='default'),
            'infer': node('DA3Inference', da3_model=['model', 0], image=['input', 0], resolution=1036, resize_method='upper_bound_resize', mode='mono'),
            'render': node('DA3Render', da3_geometry=['infer', 0], output='depth', **{'output.normalization': 'v2_style', 'output.apply_sky_clip': False}),
            'save': node('SaveImage', images=['render', 0], filename_prefix=prefix)}


def start_job(pid: str, kind: str, points=None, name='Object', soft=False, reference_frame=None,
              *, connection=None, catalog=None, executor=None) -> dict:
    """Explicit one-shot geometry generation; readiness precedes every upload/submit.

    Injection points support offline integration tests. Production captures the
    selected connection once and never switches accounts/backends in its worker.
    """
    from . import projects
    with projects._lock(pid):
        p = projects.load(pid)
        if projects.generation_active(p):
            raise ValueError('Wait for the current generation.')
        cfg = validate(p.get('geometry', {}), p['frames'])
        if reference_frame is not None:
            if kind != 'depth' or isinstance(reference_frame, bool) or not isinstance(reference_frame, int) or not 0 <= reference_frame < p['frames']:
                raise ValueError('Choose a depth reference frame inside the shot.')
            cfg['reference_frame'] = reference_frame
            p['geometry'] = cfg
        rid = _id()
        r = {'id': rid, 'kind': 'geometry', 'geometry_kind': kind, 'status': 'queued', 'files': {},
             'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'seed': 0, 'strength': ''}
        rd = projects.pdir(pid) / 'renders' / rid
        rd.mkdir(parents=True)
        selected = connection or connections.current()
        if not selected.configured:
            raise ValueError('Connect Comfy before generating geometry.')
        request = {'project': p, 'geometry': cfg, 'kind': kind, 'points': points, 'name': name, 'soft': soft,
                   'backend': elements._connection_identity(selected), 'idempotency_key': rid}
        if kind == 'matte':
            selection = people.validate({'selection': 'manual', 'points': points or []}, p['frames'])
            shape = media.read_frame(projects.plate_dir(p), 0).shape[:2]
            graph = people.graph(selection, f'keyfx/{pid}/object_{rid}', shape[::-1])
        elif kind != 'depth':
            raise ValueError('Choose depth or matte generation.')
        else:
            graph = depth_graph(f'keyfx/{pid}/depth_{rid}')
        (rd / 'graph.json').write_text(json.dumps(graph, indent=2))
        (rd / 'request.json').write_text(json.dumps(request, indent=2))
        p['renders'].insert(0, r)
        projects.save(p)
    def run():
        try:
            projects._set(pid, rid, status='checking')
            node_catalog = catalog
            if node_catalog is None:
                node_catalog = connections.node_catalog(selected)
            readiness, details = connections.check_graph(node_catalog, graph)
            (rd / 'capabilities.json').write_text(json.dumps({'status': readiness, 'details': details, 'backend': request['backend']}))
            if readiness != 'ready':
                raise ValueError('Geometry dependencies are not ready; no upload or model generation started. ' + '; '.join(details[:8]))
            def status(job, state):
                projects._set(pid, rid, job=job, status='rendering' if state == 'running' else 'validating')
            def execute(uploads):
                submission = {'status': 'submitting', 'idempotency_key': rid, 'graph_sha256': elements._sha(rd / 'graph.json'),
                              'uploads': {key: {'field': value[0], 'sha256': elements._sha(value[1])} for key, value in uploads.items()}}
                elements._write(rd / 'submission.json', submission)
                kwargs = dict(graph=graph, uploads=uploads, out_node='save', out_dir=rd/'raw', on_status=status, idempotency_key=rid)
                return executor(**kwargs) if executor else elements._execute_with_client(selected, **kwargs)
            if kind == 'depth':
                src = media.frame_path(projects.plate_dir(p), cfg['reference_frame'])
                files = execute({'input': ('image', src)})
                if len(files) != 1:
                    raise ValueError('Depth generation must return one reference depth image.')
                result = import_depth(projects.pdir(pid), files[0].read_bytes(), reference_frame=cfg['reference_frame'])
                source_shape = cv2.imread(str(src)).shape[:2]
                if abs(result['width']/result['height'] - source_shape[1]/source_shape[0]) > 2/min(result['height'],source_shape[0]):
                    raise ValueError('Depth generation changed the reference aspect ratio; it was not applied.')
                projects.update(pid, lambda q: q.setdefault('geometry', defaults()).update(depth=result,reference_frame=cfg['reference_frame'],collision_mode='depth3d'))
            else:
                source = media.content_video(projects.plate_dir(p), p['frames'], rd/'source.mp4')
                files = execute({'input': ('file', source)})
                masks = media.read_video(files[0], limit=p['frames']+1) if len(files) == 1 else []
                if len(masks) != p['frames'] or any(im.shape[:2] != shape for im in masks):
                    raise ValueError('Tracked matte changed source frame count or dimensions; it was not applied.')
                if any(int(np.ptp(im.astype(np.int16), axis=2).max()) > 4 for im in masks):
                    raise ValueError('Tracked matte returned coloured image content instead of grayscale opacity; it was not applied.')
                md = rd/'masks'; md.mkdir()
                hashes = {}
                for i, im in enumerate(masks):
                    path = md/f'{i:05d}.png'
                    if not cv2.imwrite(str(path), cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)):
                        raise ValueError('Could not preserve tracked matte frames.')
                    hashes[path.name] = elements._sha(path)
                elements._write(rd/'mask_frames.json', hashes)
                result = {'id': rid, 'name': str(name)[:80], 'directory': str((rd / 'masks').relative_to(projects.pdir(pid))),
                          'frames': p['frames'], 'static': False, 'width':shape[1], 'height':shape[0],
                          'sha256':elements._sha(rd/'mask_frames.json'), 'kind': 'soft' if soft else 'hard',
                          'role': 'both', 'thickness': .2, 'depthOffset': 0, 'softRadius': 8, 'drag': 24}
                projects.update(pid, lambda q: q.setdefault('geometry', defaults()).setdefault('mattes', []).append(result))
            projects._set(pid, rid, status='done', geometry_asset=result)
        except Exception as exc:
            projects._set(pid, rid, status='error', error=f'{type(exc).__name__}: {exc}'[:500])
    threading.Thread(target=run, daemon=True).start()
    return r
