"""Local SDR simulation movies. Never a model input or a photographic master."""
from __future__ import annotations

import bisect
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from PIL import Image
from starlette.concurrency import run_in_threadpool

from . import projects, media
from .element_api import _project, _idle, _length

router = APIRouter(prefix='/api/projects/{pid}/simulation-preview', tags=['preview'])
MAX_BYTES = 256 * 1024 * 1024
MAX_FRAME_BYTES = 2 * 1024 * 1024
REVISION = 'simulation-preview-fast-volume-v3'


def plan(p: dict, quality: str = 'high') -> dict:
    if p.get('mode') != 'plate' or p.get('settings', {}).get('pipeline') != 'ltx_elements':
        raise ValueError('Simulation download is available for original-plate LTX shots.')
    if quality not in ('draft', 'high'):
        raise ValueError('Choose draft or high preview quality.')
    profile = p.get('native_profile') or {}
    duration = float(profile.get('duration', p['frames'] / 24))
    if not math.isfinite(duration) or not 0 < duration <= 60.000001:
        raise ValueError('Simulation previews support shots up to60seconds.')
    times = profile.get('timestamps', [i / 24 for i in range(p['frames'])])
    if len(times) != p['frames'] or not times or any(not math.isfinite(t) for t in times) or any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError('The source timeline is incomplete.')
    count = max(1, math.ceil(duration * 24 - 1e-8))
    indices = []
    for i in range(count):
        t = i / 24
        k = min(bisect.bisect_left(times, t), len(times) - 1)
        if k and t - times[k - 1] <= times[k] - t: k -= 1
        indices.append(k)
    width, height = profile.get('width', 1920), profile.get('height', 1080)
    factor = 960 / max(width, height)
    width, height = max(2, round(width * factor / 2) * 2), max(2, round(height * factor / 2) * 2)
    frozen = {k: p.get(k) for k in ('id', 'frames', 'native_profile', 'source', 'start_s', 'effects', 'geometry', 'flashes', 'settings')}
    track = projects.track_file(p)
    frozen['camera_track_sha256'] = hashlib.sha256(track.read_bytes()).hexdigest()
    frozen['camera_mode'] = 'locked' if projects.camera_locked(p) else 'auto'
    signature = hashlib.sha256(json.dumps([REVISION, quality, frozen], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'signature': signature, 'quality': quality, 'frames': count, 'fps': 24, 'duration': duration,
            'source_frames': indices, 'capture_width': 960, 'capture_height': 540, 'width': width, 'height': height,
            'description': 'SDR simulation preview, all placed effects and blocking muzzle sprites; no handles or model generation.'}


def _encode(p: dict, spec: dict, frames: Path, dest: Path) -> dict:
    if plan(projects.load(p['id']), spec['quality'])['signature'] != spec['signature']:
        raise ValueError('The shot changed. Download a new simulation preview.')
    root = projects.pdir(p['id'])
    profile = p.get('native_profile') or {}
    audio = root / 'native' / profile['source_file'] if profile.get('source_file') else root / p['source']['file']
    args = ['ffmpeg', '-v', 'error', '-y', '-framerate', '24', '-i', str(frames / '%04d.jpg')]
    if audio.is_file():
        args += ['-seek_timestamp', '1', '-ss', str(profile.get('trim_actual_start_s', p.get('start_s', 0))), '-i', str(audio)]
    args += ['-map', '0:v:0']
    args += ['-map', '1:a:0?', '-c:a', 'aac', '-b:a', '192k'] if audio.is_file() else ['-an']
    args += ['-frames:v', str(spec['frames']), '-vf',
             f'scale={spec["width"]}:{spec["height"]}:in_range=pc:out_range=tv:out_color_matrix=bt709:flags=accurate_rnd+full_chroma_int,setsar=1',
             '-c:v', 'libx264', '-crf', '16', '-pix_fmt', 'yuv420p',
             '-color_primaries', 'bt709', '-color_trc', 'iec61966-2-1', '-colorspace', 'bt709', '-color_range', 'tv',
             '-t', str(spec['duration']), '-movflags', '+faststart', str(frames / 'preview.mp4')]
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError('Could not encode the simulation preview: ' + result.stderr[-1200:])
    actual = media.probe(frames / 'preview.mp4')
    if (actual['width'], actual['height'], actual['frames']) != (spec['width'], spec['height'], spec['frames']):
        raise ValueError('Simulation preview failed geometry/frame-count verification.')
    if plan(projects.load(p['id']), spec['quality'])['signature'] != spec['signature']:
        raise ValueError('The shot changed during encoding. Download a new preview.')
    dest.mkdir(parents=True, exist_ok=True)
    os.replace(frames / 'preview.mp4', dest / 'preview.mp4')
    record = {**spec, 'video_sha256': hashlib.sha256((dest / 'preview.mp4').read_bytes()).hexdigest(),
              'kind': 'simulation_preview', 'photographic_master': False}
    (dest / 'preview.json').write_text(json.dumps(record, indent=2) + '\n')
    return {'id': spec['signature'], 'url': f'/api/projects/{p["id"]}/simulation-preview/{spec["signature"]}/preview.mp4',
            'filename': 'bang-on-simulation-preview.mp4', **spec}


@router.get('/plan')
def preview_plan(pid: str, quality: str = 'high'):
    try: return plan(_project(pid), quality)
    except ValueError as exc: raise HTTPException(400, str(exc)) from None


@router.post('')
async def prepare_preview(pid: str, request: Request):
    p = _project(pid); _idle(p); _length(request, MAX_BYTES)
    root = projects.pdir(pid) / 'simulation_previews'
    root.mkdir(exist_ok=True)
    try:
        async with request.form(max_files=1440, max_fields=2, max_part_size=MAX_FRAME_BYTES) as form:
            raw = form.get('metadata')
            if not isinstance(raw, str) or len(raw) > 2000: raise ValueError('Supply preview metadata.')
            meta = json.loads(raw)
            if not isinstance(meta, dict): raise ValueError('Preview metadata must be an object.')
            spec = plan(p, meta.get('quality', 'high'))
            if meta.get('signature') != spec['signature']: raise ValueError('The shot changed. Capture a new preview.')
            with tempfile.TemporaryDirectory(prefix='.capture-', dir=root) as td:
                directory = Path(td); seen = set(); total = 0
                for name, value in form.multi_items():
                    if name in seen: raise ValueError('Duplicate preview frame or metadata.')
                    seen.add(name)
                    if name == 'metadata': continue
                    match = re.fullmatch(r'frame_(\d{4})\.jpg', name)
                    if not match or not hasattr(value, 'read'): raise ValueError('Unexpected preview field.')
                    frame = int(match[1])
                    if not 0 <= frame < spec['frames']: raise ValueError('Preview frame is outside the shot.')
                    data = await value.read(MAX_FRAME_BYTES + 1); total += len(data)
                    if len(data) > MAX_FRAME_BYTES or total > MAX_BYTES: raise HTTPException(413, 'Preview capture exceeds256MB.')
                    with Image.open(io.BytesIO(data)) as image:
                        if image.format != 'JPEG' or image.size != (spec['capture_width'], spec['capture_height']):
                            raise ValueError('Preview frame geometry or format differs from its plan.')
                        image.verify()
                    (directory / f'{frame:04d}.jpg').write_bytes(data)
                if len(seen) != spec['frames'] + 1: raise ValueError('Capture every preview frame.')
                return await run_in_threadpool(_encode, p, spec, directory, root / spec['signature'])
    except HTTPException: raise
    except (ValueError, TypeError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(400, str(exc)) from None


@router.get('/{version}/preview.mp4')
def download_preview(pid: str, version: str):
    _project(pid)
    if not re.fullmatch(r'[a-f0-9]{64}', version): raise HTTPException(404, 'Preview not found.')
    path = projects.pdir(pid) / 'simulation_previews' / version / 'preview.mp4'
    if not path.is_file(): raise HTTPException(404, 'Preview not found.')
    return FileResponse(path, media_type='video/mp4', filename='bang-on-simulation-preview.mp4')
