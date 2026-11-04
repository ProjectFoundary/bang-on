"""HTTP boundary for isolated elements and immutable geometry assets.

Mounted by server.py, so its same-origin and project-generation guards apply.
This module starts no workers or model jobs on import/startup.
"""
from __future__ import annotations

import copy
import base64
import json
import math
from pathlib import Path, PureWindowsPath
import re
import struct
import subprocess
import io
import zipfile

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from . import depth_selection, element_jobs, elements, geometry, people, projects, post_holdouts

router = APIRouter(prefix='/api/projects/{pid}', tags=['elements'])
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PREPARE_BYTES = 256 * 1024 * 1024
MAX_GEOMETRY_BYTES = 64 * 1024 * 1024
_ID = re.compile(r'[A-Za-z0-9_-]{1,128}')
_MATTE_FIELDS = {'role', 'kind', 'thickness', 'softRadius', 'colliderBlur', 'colliderDisplacement', 'drag', 'depthOffset'}


def _id(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise HTTPException(404, 'Asset or project not found.')
    return value


def _project(pid: str) -> dict:
    _id(pid)
    try:
        return projects.load(pid)
    except FileNotFoundError:
        raise HTTPException(404, 'Project not found.') from None


def _idle(p: dict) -> None:
    if projects.generation_active(p) or any(f.get('status') in ('queued', 'generating') for f in p.get('flashes', [])):
        raise HTTPException(409, 'Wait for the current generation before changing element inputs.')


def _bound(pid: str, value: str | Path) -> Path:
    root = projects.pdir(_id(pid)).resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root) or path == root or not path.is_file():
        raise HTTPException(404, 'Asset not found.')
    return path


def _safe_filename(value: str | None) -> str:
    value = value or 'upload.png'
    if (len(value) > 255 or value in ('.', '..') or Path(value).name != value
            or PureWindowsPath(value).name != value or any(ord(ch) < 32 for ch in value)):
        raise HTTPException(400, 'Use a plain filename without directory components.')
    return value


def _length(request: Request, limit: int) -> None:
    raw = request.headers.get('content-length')
    if raw:
        try:
            if int(raw) < 0 or int(raw) > limit:
                raise HTTPException(413, 'Upload is too large.')
        except ValueError:
            raise HTTPException(400, 'Invalid upload length.') from None


def _number(value: object, name: str, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise HTTPException(400, f'Invalid {name}.')
    return float(value)


def _public_geometry(p: dict) -> dict:
    cfg = geometry.validate(p.get('geometry', {}), p['frames'])
    out = {k: cfg[k] for k in ('reference_frame', 'focal_pixels', 'depth_scale', 'depth_bias', 'collision_mode')}
    entry = cfg.get('depth')
    out['depth'] = ({k: entry[k] for k in ('id', 'kind', 'reference_frame', 'width', 'height', 'sha256', 'units') if k in entry}
                    if entry else None)
    out['depth_current'] = bool(entry and entry.get('reference_frame', cfg['reference_frame']) == cfg['reference_frame'])
    out['mattes'] = [{k: m[k] for k in ('id', 'name', 'frames', 'static', 'width', 'height', 'reference_frame',
                                      'kind', 'role', 'thickness', 'softRadius', 'colliderBlur', 'colliderDisplacement', 'drag', 'depthOffset', 'sha256', 'holdoutPost', 'holdoutBaseline', 'cameraBox') if k in m}
                     for m in cfg.get('mattes', [])]
    for entry,original in zip(out['mattes'],cfg.get('mattes',[])):
        if original.get('holdout'):
            entry['holdout']={k:v for k,v in original['holdout'].items() if k!='directory'}
    return out


def public_take(pid: str, row: dict) -> dict:
    out = {k: copy.deepcopy(row[k]) for k in ('id', 'effect_id', 'generation_signature', 'status', 'created', 'error', 'current', 'backend_current', 'approved', 'approval_outdated', 'needs_alpha_core', 'plan','recoverable_stages','local_recovery') if k in row}
    if 'backend_current' not in out:
        out['backend_current'] = element_jobs.backend_current(pid,row)
    out['assets'] = {}
    if row.get('inputs'):
        out['guide_url'] = f'/api/projects/{pid}/element-takes/{row["id"]}/guide.mp4'
    for stage, asset in row.get('assets', {}).items():
        if stage not in ('rgb', 'alpha'):
            continue
        out['assets'][stage] = {k: copy.deepcopy(asset[k]) for k in ('sha256', 'frames', 'model_frames', 'fps', 'width', 'height',
                                                                   'content_rect', 'decoded_bits', 'colour_space', 'colour_association') if k in asset}
        out['assets'][stage]['url'] = f'/api/projects/{pid}/element-takes/{row["id"]}/{stage}.mp4'
    return out


@router.get('/elements')
def list_elements(pid: str):
    p = _project(pid)
    return {'enabled': element_jobs.enabled(p), 'takes': [public_take(pid, r) for r in element_jobs.records(p)]}


@router.get('/elements/plan')
def element_plan(pid: str, effect_id: str):
    p = _project(pid)
    _id(effect_id)
    try:
        return element_jobs.plan(p, effect_id)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from None


@router.get('/elements/capabilities')
def element_capabilities(pid: str):
    """Explicit, read-only selected-backend check. Never invoked at startup."""
    p = _project(pid)
    recipe = {'models': p.get('settings', {}).get('element_models', {}),
              'model_revisions': p.get('settings', {}).get('element_model_revisions', {})}
    try:
        return elements.fetch_capabilities(recipe)
    except (ValueError, TypeError):
        raise HTTPException(400, 'The selected element model profile is invalid.') from None


@router.post('/elements/{eid}/prepare')
async def prepare_element(pid: str, eid: str, request: Request):
    p = _project(pid); _id(eid); _idle(p)
    _length(request, MAX_PREPARE_BYTES)
    try:
        spec = element_jobs.plan(p, eid)
        form = await request.form(max_files=363, max_fields=2, max_part_size=MAX_IMAGE_BYTES)
        metadata = form.get('metadata')
        if not isinstance(metadata, str) or len(metadata) > 200_000:
            raise ValueError('Supply element metadata and captured guide frames.')
        meta = json.loads(metadata)
        if not isinstance(meta, dict):
            raise ValueError('Element metadata must be an object.')
        images, visibility, guide_alpha, total, seen = {}, {}, {}, 0, set()
        for name, val in form.multi_items():
            if name in seen:
                raise ValueError('Each captured frame must appear only once.')
            seen.add(name)
            if name == 'metadata':
                continue
            m = re.fullmatch(r'(guide_alpha|guide|visibility)_(\d{4})\.png', name)
            if not m or not hasattr(val, 'read'):
                raise ValueError('Unexpected element upload field.')
            f = int(m.group(2))
            if not 0 <= f < spec['active_frames']:
                raise ValueError('Captured frame is outside this element window.')
            _safe_filename(val.filename)
            data = await val.read(MAX_IMAGE_BYTES + 1)
            total += len(data)
            if len(data) > MAX_IMAGE_BYTES or total > MAX_PREPARE_BYTES:
                raise HTTPException(413, 'Captured guide upload is too large.')
            # Read PNG geometry before allocating pixels in the decoder.
            if len(data) < 24 or data[:8] != b'\x89PNG\r\n\x1a\n' or data[12:16] != b'IHDR':
                raise ValueError('Captured frames must be PNG images.')
            width, height = struct.unpack('>II', data[16:24])
            if (width, height) != (spec['width'], spec['height']):
                raise ValueError('Captured canvas differs from the current element plan.')
            {'guide':images,'visibility':visibility,'guide_alpha':guide_alpha}[m.group(1)][f] = data
        if len(images) != spec['active_frames'] or len(visibility) != spec['active_frames']:
            raise ValueError('Capture every guide and geometric visibility frame.')
        extras={'guide_alpha':guide_alpha} if guide_alpha else {}
        return public_take(pid, element_jobs.prepare_upload(pid, eid, meta, images, visibility, **extras))
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from None


@router.post('/element-takes/{tid}/generate')
def generate_element(pid: str, tid: str):
    p = _project(pid); _id(tid); _idle(p)
    try:
        row = element_jobs.start(pid, tid)
        return public_take(pid, row) if 'effect_id' in row else row
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from None


@router.post('/element-takes/{tid}/approve')
def approve_element(pid: str, tid: str):
    p = _project(pid); _id(tid); _idle(p)
    try:
        return public_take(pid, element_jobs.approve(pid, tid))
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from None


@router.post('/element-takes/{tid}/generate-batch')
def generate_batch_element(pid: str, tid: str):
    p=_project(pid);_id(tid);_idle(p)
    try:
        row=element_jobs.start(pid,tid,skip_if_approved=True)
        if row.get('skipped_approved'):return row
        return public_take(pid,row) if 'effect_id' in row else row
    except (ValueError,TypeError) as exc:
        raise HTTPException(400,str(exc)) from None


@router.post('/elements/{eid}/new-version')
def new_element_version(pid: str, eid: str):
    p = _project(pid); _id(eid); _idle(p)
    try:
        return element_jobs.new_version(pid, eid)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from None


@router.post('/elements/composite')
def composite_elements(pid: str):
    p = _project(pid); _idle(p)
    try:
        return element_jobs.composite(pid)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from None


@router.post('/elements/review-composite')
def review_composite_elements(pid: str, body: dict):
    p=_project(pid);_idle(p)
    if set(body)!={'take_ids'}:
        raise HTTPException(400,'Choose the completed element take IDs for this review composite.')
    try:
        return element_jobs.review_composite(pid,body['take_ids'])
    except (ValueError,TypeError,KeyError) as exc:
        raise HTTPException(400,str(exc)) from None


@router.post('/element-takes/{tid}/review')
async def review_element(pid: str, tid: str):
    p=_project(pid);_id(tid);_idle(p)
    try:
        return await run_in_threadpool(element_jobs.review_over_plate,pid,tid)
    except (ValueError,TypeError,KeyError,OSError,subprocess.TimeoutExpired) as exc:
        raise HTTPException(400,str(exc)) from None


@router.post('/element-takes/{tid}/effective-alpha-review')
async def review_effective_alpha(pid: str, tid: str):
    p=_project(pid);_id(tid);_idle(p)
    from . import alpha_core_review
    def build():
        effect,row,take=element_jobs._review_state(p,tid)
        result=alpha_core_review.build(projects.pdir(pid),row,take,treatment=element_jobs.effect_config(p,effect),context=(p,effect))
        return {k:v for k,v in result.items() if k!='path'}
    try:
        result=await run_in_threadpool(build)
        result['url']=f'/api/projects/{pid}/element-takes/{tid}/alpha-reviews/{result["signature"]}/effective-alpha.mp4'
        return result
    except (ValueError,TypeError,KeyError,OSError,subprocess.TimeoutExpired) as exc:
        raise HTTPException(400,str(exc)) from None


@router.get('/element-takes/{tid}/alpha-reviews/{signature}/effective-alpha.mp4')
def effective_alpha_video(pid: str, tid: str, signature: str):
    p=_project(pid);_id(tid)
    from . import alpha_core_review
    try:
        effect,row,take=element_jobs._review_state(p,tid)
        path=alpha_core_review.validate_movie(projects.pdir(pid),row,take,signature,treatment=element_jobs.effect_config(p,effect),context=(p,effect))
        _bound(pid,path)
        return FileResponse(path,media_type='video/mp4',headers={'Cache-Control':'private, no-cache'})
    except (ValueError,TypeError,KeyError,OSError):
        raise HTTPException(404,'This effective-alpha review is missing or no longer matches the element.') from None


@router.post('/element-takes/{tid}/recover-downloaded')
async def recover_downloaded_element(pid: str, tid: str, body: dict):
    p=_project(pid);_id(tid);_idle(p)
    if set(body)!={'stage','expected_raw_sha256'}:raise HTTPException(400,'Choose the downloaded stage and its reviewed SHA256.')
    try:
        row=await run_in_threadpool(element_jobs.recover_downloaded,pid,tid,body['stage'],body['expected_raw_sha256'])
        return public_take(pid,row)
    except (ValueError,TypeError,KeyError,OSError,elements.ElementError) as exc:
        raise HTTPException(400,str(exc)) from None


@router.get('/element-takes/{tid}/reviews/{version}/review.mp4')
def element_review_video(pid: str, tid: str, version: str):
    p=_project(pid);_id(tid)
    if not re.fullmatch(r'[a-f0-9]{64}',version) or not any(r['id']==tid for r in p.get('element_takes',[])):
        raise HTTPException(404,'Local review not found.')
    folder=Path('element_reviews')/tid/version
    video=_bound(pid,folder/'review.mp4');manifest=_bound(pid,folder/'review.json')
    try:record=json.loads(manifest.read_text())
    except (OSError,ValueError):raise HTTPException(409,'The local review changed. Build a new review over the plate.') from None
    if record.get('signature')!=version or elements._sha(video)!=record.get('video_sha256'):
        raise HTTPException(409,'The local review changed. Build a new review over the plate.')
    return FileResponse(video,media_type='video/mp4')


@router.get('/element-takes/{tid}/{stage}.mp4')
def element_video(pid: str, tid: str, stage: str):
    p = _project(pid); _id(tid)
    if stage not in ('rgb', 'alpha', 'guide'):
        raise HTTPException(404, 'Element stage not found.')
    row = next((r for r in p.get('element_takes', []) if r['id'] == tid), None)
    if stage == 'guide':
        if not row or not row.get('inputs'):
            raise HTTPException(404, 'Prepared guide not found.')
        return FileResponse(_bound(pid, Path(row['inputs']) / 'guide.mp4'), media_type='video/mp4')
    if not row or not row.get('take_dir'):
        raise HTTPException(404, 'Element stage not found.')
    try:
        saved=elements.inspect(element_jobs._asset_path(projects.pdir(pid),row['take_dir'],directory=True))
        if stage not in saved.get('assets',{}):raise HTTPException(404,'No completed asset is saved for this element stage.')
    except (OSError,KeyError,ValueError):raise HTTPException(409,'The saved stage is unavailable; inspect the cached take before another attempt.') from None
    try:entry=element_jobs.validated_stage(projects.pdir(pid),row,stage)
    except ValueError as exc:raise HTTPException(409,str(exc)) from None
    return FileResponse(_bound(pid, entry['path']), media_type='video/mp4')


@router.get('/geometry')
def get_geometry(pid: str):
    return _public_geometry(_project(pid))


@router.post('/geometry/depth')
async def import_depth(pid: str, request: Request, file: UploadFile = File(...), kind: str = Form('inverse'),
                       reference_frame: int | None = Form(None)):
    _project(pid); _length(request, MAX_GEOMETRY_BYTES + 100_000); _safe_filename(file.filename)
    data = await file.read(MAX_GEOMETRY_BYTES + 1)
    if len(data) > MAX_GEOMETRY_BYTES:
        raise HTTPException(413, 'Depth image is too large.')
    try:
        with projects._lock(pid):
            p = _project(pid); _idle(p)
            cfg = geometry.validate(p.get('geometry', {}), p['frames'])
            ref = cfg['reference_frame'] if reference_frame is None else reference_frame
            if isinstance(ref, bool) or not 0 <= ref < p['frames']:
                raise ValueError('Choose a depth reference frame inside the shot.')
            entry = geometry.import_depth(projects.pdir(pid), data, kind=kind, reference_frame=ref)
            cfg.update(depth=entry, reference_frame=ref, collision_mode='depth3d')
            p['geometry'] = cfg; projects.save(p)
        return {'geometry': _public_geometry(p), 'asset': _public_geometry(p)['depth']}
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from None


@router.post('/geometry/mattes')
async def import_matte(pid: str, request: Request, file: UploadFile = File(...), name: str = Form('Object'), kind: str = Form('hard')):
    _project(pid); _length(request, MAX_GEOMETRY_BYTES + 100_000)
    filename = _safe_filename(file.filename)
    data = await file.read(MAX_GEOMETRY_BYTES + 1)
    if len(data) > MAX_GEOMETRY_BYTES:
        raise HTTPException(413, 'Matte upload is too large.')
    if kind not in ('hard', 'soft') or len(name) > 80:
        raise HTTPException(400, 'Choose a named hard or soft matte.')
    try:
        with projects._lock(pid):
            p = _project(pid); _idle(p)
            cfg = geometry.validate(p.get('geometry', {}), p['frames'])
            entry = geometry.import_matte(projects.pdir(pid), data, filename, p['frames'], name=name, soft=kind == 'soft')
            if entry['static']:
                entry['reference_frame'] = cfg['reference_frame']
            cfg['mattes'].append(entry); p['geometry'] = cfg; projects.save(p)
        return {'geometry': _public_geometry(p), 'asset': next(m for m in _public_geometry(p)['mattes'] if m['id'] == entry['id'])}
    except (ValueError, TypeError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise HTTPException(400, 'Could not import matte: ' + str(exc)) from None


@router.post('/geometry/use-people-matte')
def reuse_people_matte(pid: str, body: dict):
    """Explicitly reuse already tracked pixels; never run another matte model."""
    if set(body) - {'effect_id'}:
        raise HTTPException(400, 'Choose an element for the existing people matte.')
    with projects._lock(pid):
        p = _project(pid); _idle(p)
        current = people.current(p)
        if not current:
            raise HTTPException(400, 'No current tracked people matte is available.')
        eid = body.get('effect_id')
        effect = next((e for e in p['effects'] if e['id'] == eid), None) if eid is not None else None
        if eid is not None and (effect is None or effect['preset'] not in element_jobs.KINDS):
            raise HTTPException(400, 'Choose an existing simulated element.')
        cfg = geometry.validate(p.get('geometry', {}), p['frames'])
        asset = next((m for m in cfg['mattes'] if m.get('source_people_matte') == current['id']), None)
        if asset is None:
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, 'w', compression=zipfile.ZIP_STORED) as archive:
                for frame in range(p['frames']):
                    source = _bound(pid, Path('renders') / current['id'] / 'masks' / f'{frame:05d}.png')
                    archive.writestr(f'{frame:05d}.png', source.read_bytes())
                    if raw.tell() > 512 * 1024 * 1024:
                        raise HTTPException(413, 'The existing matte exceeds the geometry import size limit.')
            try:
                asset = geometry.import_matte(projects.pdir(pid), raw.getvalue(), 'people.zip', p['frames'], name='Tracked people', soft=True)
            except (ValueError, OSError) as exc:
                raise HTTPException(400, str(exc)) from None
            asset.update(source_people_matte=current['id'], colliderBlur=8.)
            cfg['mattes'].append(asset)
        p['geometry'] = cfg
        if effect is not None:
            options = element_jobs.effect_config(p, effect)
            options['colliders'] = list(dict.fromkeys([*options['colliders'], asset['id']]))
            effect['element'] = options
        projects.save(p)
    return {'asset': {k: v for k, v in asset.items() if k != 'directory'}, 'geometry': _public_geometry(p)}


@router.patch('/geometry')
def patch_geometry(pid: str, body: dict):
    _project(pid)
    allowed = {'reference_frame', 'focal_pixels', 'depth_scale', 'depth_bias', 'mattes', 'collision_mode', 'depth_interpretation'}
    if set(body) - allowed:
        raise HTTPException(400, 'Geometry patches may change controls, not immutable asset paths or IDs.')
    with projects._lock(pid):
        p = _project(pid); _idle(p)
        cfg = copy.deepcopy(geometry.validate(p.get('geometry', {}), p['frames']))
        if 'depth_interpretation' in body:
            interpretation = body['depth_interpretation']
            if (not isinstance(interpretation, dict) or set(interpretation) != {'id', 'kind'}
                    or interpretation['kind'] not in ('inverse', 'distance')):
                raise HTTPException(400, 'Choose a valid depth convention for the imported pass.')
            depth = cfg.get('depth')
            if not depth or depth['id'] != interpretation['id']:
                raise HTTPException(409, 'The depth pass changed. Reload the shot and choose its convention again.')
            depth['kind'] = interpretation['kind']
        if 'collision_mode' in body:
            if body['collision_mode'] not in ('matte2d', 'depth3d'):
                raise HTTPException(400, 'Choose 2D matte or depth-based collision.')
            cfg['collision_mode'] = body['collision_mode']
        for name, lo, hi in [('focal_pixels', 100, 10000), ('depth_scale', .01, 1000), ('depth_bias', 0, 10)]:
            if name in body:
                cfg[name] = _number(body[name], name, lo, hi)
        if 'reference_frame' in body:
            ref = body['reference_frame']
            if isinstance(ref, bool) or not isinstance(ref, int) or not 0 <= ref < p['frames']:
                raise HTTPException(400, 'Choose a reference frame inside the shot.')
            cfg['reference_frame'] = ref
        patches = body.get('mattes', [])
        if not isinstance(patches, list) or len(patches) > len(cfg['mattes']):
            raise HTTPException(400, 'Patch existing matte IDs only.')
        seen = set()
        for patch in patches:
            if not isinstance(patch, dict) or set(patch) - (_MATTE_FIELDS | {'id'}) or 'id' not in patch:
                raise HTTPException(400, 'Patch matte controls using its immutable ID.')
            mid = patch['id']
            if not isinstance(mid, str) or mid in seen:
                raise HTTPException(400, 'Each existing matte may be patched once.')
            seen.add(mid)
            entry = next((m for m in cfg['mattes'] if m['id'] == mid), None)
            if not entry:
                raise HTTPException(400, 'Matte ID does not exist.')
            for name, choices in [('role', ('collide', 'holdout', 'both')), ('kind', ('hard', 'soft'))]:
                if name in patch:
                    if patch[name] not in choices:
                        raise HTTPException(400, f'Invalid matte {name}.')
                    entry[name] = patch[name]
            for name, lo, hi in [('thickness', .001, 20), ('softRadius', 0, 256), ('colliderBlur', 0, 128), ('colliderDisplacement', -128, 128), ('drag', 0, 1000), ('depthOffset', -1000, 1000)]:
                if name in patch:
                    number = _number(patch[name], name, lo, hi)
                    # A no-op full-form save must not change int/float spelling
                    # in a frozen legacy geometry signature.
                    if entry.get(name) != number: entry[name] = number
        p['geometry'] = cfg; projects.save(p)
    return _public_geometry(p)


def _depth_pick(p: dict, body: dict):
    try:
        cfg = geometry.validate(p.get('geometry', {}), p['frames'])
        return depth_selection.select(projects.pdir(p['id']), cfg, body.get('points'), body.get('tolerance', .04), body.get('bounds'))
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from None


def _png_url(image):
    ok, encoded = cv2.imencode('.png', image)
    if not ok:
        raise HTTPException(400, 'Could not render the depth selection.')
    return 'data:image/png;base64,' + base64.b64encode(encoded.tobytes()).decode('ascii')


@router.post('/geometry/depth-selection/preview')
def preview_depth_selection(pid: str, body: dict):
    if set(body) - {'points', 'tolerance', 'bounds'}:
        raise HTTPException(400, 'Use depth points, tolerance and optional region bounds.')
    mask, depth, recipe = _depth_pick(_project(pid), body)
    overlay = np.zeros((*mask.shape, 4), np.uint8)
    overlay[..., :3] = [95, 220, 65]
    overlay[..., 3] = np.uint8(mask.astype(np.float32) * .55)
    return {**recipe, 'mask_url': _png_url(mask), 'overlay_url': _png_url(overlay), 'depth_url': _png_url(depth)}


@router.post('/geometry/depth-selection')
def save_depth_selection(pid: str, body: dict):
    if set(body) - {'points', 'tolerance', 'bounds', 'selection_key', 'name', 'kind', 'role', 'effect_id'}:
        raise HTTPException(400, 'Unexpected depth selection setting.')
    name, kind, role = body.get('name', 'Depth object'), body.get('kind', 'hard'), body.get('role', 'both')
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or kind not in ('hard', 'soft') or role not in ('both', 'collide', 'holdout'):
        raise HTTPException(400, 'Choose an object name, surface and collision/holdout role.')
    with projects._lock(pid):
        p = _project(pid); _idle(p)
        mask, _, recipe = _depth_pick(p, body)
        if body.get('selection_key') != recipe['selection_key']:
            raise HTTPException(409, 'The depth selection changed. Preview it again before adding the object.')
        eid = body.get('effect_id')
        effect = next((e for e in p['effects'] if e['id'] == eid), None) if eid is not None else None
        if eid is not None and (effect is None or effect['preset'] not in element_jobs.KINDS):
            raise HTTPException(400, 'Select an existing simulated element for this object.')
        ok, data = cv2.imencode('.png', mask)
        if not ok:
            raise HTTPException(400, 'Could not preserve the depth selection.')
        asset = geometry.import_matte(projects.pdir(pid), data.tobytes(), 'depth_selection.png', p['frames'], name=name.strip(), soft=kind == 'soft')
        asset.update(role=role, colliderBlur=0., reference_frame=recipe['reference_frame'], selection=recipe)
        p.setdefault('geometry', geometry.defaults()).setdefault('mattes', []).append(asset)
        if effect is not None:
            cfg = element_jobs.effect_config(p, effect)
            cfg['colliders'] = [*cfg['colliders'], asset['id']]
            effect['element'] = cfg
        projects.save(p)
    return {'asset': {k: v for k, v in asset.items() if k != 'directory'}, 'geometry': _public_geometry(p)}


@router.post('/geometry/generate')
def generate_geometry(pid: str, body: dict):
    p = _project(pid); _idle(p)
    if set(body) - {'kind', 'points', 'name', 'soft', 'reference_frame'} or body.get('kind') not in ('depth', 'matte'):
        raise HTTPException(400, 'Choose depth or matte generation.')
    name, soft, points = body.get('name', 'Object'), body.get('soft', False), body.get('points')
    if not isinstance(name, str) or len(name) > 80 or not isinstance(soft, bool):
        raise HTTPException(400, 'Choose a valid object name and collider type.')
    if points is not None and (not isinstance(points, list) or len(points) > 128):
        raise HTTPException(400, 'Use up to128 object selection points.')
    extra = {}
    if 'reference_frame' in body:
        ref = body['reference_frame']
        if body['kind'] != 'depth' or isinstance(ref, bool) or not isinstance(ref, int) or not 0 <= ref < p['frames']:
            raise HTTPException(400, 'Choose a depth reference frame inside the shot.')
        extra['reference_frame'] = ref
    try:
        return geometry.start_job(pid, body['kind'], points=points, name=name, soft=soft, **extra)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from None


@router.get('/geometry/mattes/{mid}/frames/{frame}.png')
def matte_frame(pid: str, mid: str, frame: int, max_edge: int = Query(256, ge=16, le=2048)):
    p = _project(pid); _id(mid)
    entry = next((m for m in p.get('geometry', {}).get('mattes', []) if m['id'] == mid), None)
    if not entry or not 0 <= frame < p['frames']:
        raise HTTPException(404, 'Matte frame not found.')
    index = 0 if entry.get('static') else frame
    if not 0 <= index < entry.get('frames', 0):
        raise HTTPException(404, 'Matte frame not found.')
    path = _bound(pid, Path(entry['directory']) / f'{index:05d}.png')
    im = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if im is None or im.dtype not in (np.uint8, np.uint16):
        raise HTTPException(404, 'Matte image is missing or invalid.')
    if im.ndim == 3:
        im = im[..., 3] if im.shape[2] == 4 else cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    if im.dtype == np.uint16:
        im = np.uint8(np.floor(im.astype(np.float64) / 257 + .5))
    scale = min(1., max_edge / max(im.shape))
    if scale < 1:
        im = cv2.resize(im, (max(1, round(im.shape[1] * scale)), max(1, round(im.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode('.png', im)
    if not ok:
        raise HTTPException(404, 'Could not decode the matte preview.')
    return Response(encoded.tobytes(), media_type='image/png', headers={'Cache-Control': 'private, max-age=31536000, immutable'})


@router.get('/geometry/scene')
def geometry_scene(pid: str):
    p = _project(pid)
    try:
        cfg = geometry.validate(p.get('geometry', {}), p['frames'])
        ref = cfg['reference_frame']
        if cfg['collision_mode'] == 'depth3d':
            if not cfg.get('depth'):
                raise HTTPException(400, 'Import or generate a depth pass before using depth-based collision. 2D matte mode works without depth.')
            if cfg['depth'].get('reference_frame', ref) != ref:
                raise HTTPException(400, 'Import or generate depth for the selected reference frame.')
            depth = geometry.depth_data(projects.pdir(pid), cfg)
        else:
            depth = {'width': 1, 'height': 1, 'values': [1.], 'kind': 'inverse', 'scale': 4., 'bias': .05}
        track_path = projects.track_file(p)
        _bound(pid, track_path)
        track = json.loads(track_path.read_text())
        native = track.get('H', track.get('camera'))
        if not isinstance(native, list) or len(native) != p['frames']:
            raise ValueError('The camera track does not cover this source shot.')
        matrices = []
        for value in native:
            if isinstance(value, list) and len(value) == 4:
                a, b, tx, ty = value
                matrix = np.array([[a, -b, tx], [b, a, ty], [0, 0, 1]], dtype=np.float64)
            else:
                matrix = np.asarray(value, dtype=np.float64).reshape(3, 3)
            if not np.all(np.isfinite(matrix)) or abs(np.linalg.det(matrix)) < 1e-12:
                raise ValueError('The camera track contains an invalid transform.')
            matrices.append(matrix)
        scale = np.diag([960 / track.get('w', 960), 540 / track.get('h', 540), 1.])
        inverse_ref = np.linalg.inv(matrices[ref])
        homographies = [(scale @ m @ inverse_ref @ np.linalg.inv(scale)).ravel().tolist() for m in matrices]
        mattes = []
        for entry in cfg['mattes']:
            if entry.get('static') and entry.get('reference_frame', ref) != ref:
                raise ValueError('A static matte belongs to another reference frame; import it again for this frame.')
            matte = {k: entry[k] for k in ('id', 'width', 'height', 'static', 'role', 'kind', 'thickness', 'depthOffset', 'softRadius', 'colliderBlur', 'colliderDisplacement', 'drag', 'holdoutPost', 'cameraBox') if k in entry}
            matte.update(coordinates='reference' if entry.get('static') else 'source', frameCount=entry['frames'],
                         maskUrl=f'/api/projects/{pid}/geometry/mattes/{entry["id"]}/frames/{{frame}}.png')
            if entry.get('holdoutPost'):
                matte['holdoutDepth']=post_holdouts.reference_depth(p,cfg,entry)
                if entry.get('holdout'):
                    h=entry['holdout']
                    matte.update(holdoutWidth=h['width'],holdoutHeight=h['height'],holdoutCoordinates='source',
                                 holdoutMaskUrl=f'/api/projects/{pid}/people-alpha/{h["take_id"]}/frames/{{frame}}.png')
            mattes.append(matte)
        profile = element_jobs.source_profile(p)
        return {'key': element_jobs.digest([cfg, homographies, profile['timestamps']]), 'width': 960, 'height': 540,
                'referenceFrame': ref, 'fps': profile['fps'], 'sourceTimes': profile['timestamps'], 'depth': depth, 'collisionMode': cfg['collision_mode'],
                'camera': {'focalPixels': cfg['focal_pixels'], 'homographies': homographies}, 'mattes': mattes}
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, OSError, np.linalg.LinAlgError):
        # Keep absolute disk paths out of HTTP errors.
        raise HTTPException(400, 'Depth, matte or camera data is missing or invalid for this reference frame. Import/generate matching geometry and retry.') from None
