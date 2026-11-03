"""Local viewing copies of the same assisted explosion matte used in composite.

The caller validates a completed current take first. These independent caches
never replace model assets, change approval, submit a provider job or touch RGB.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading

import cv2
import numpy as np

from . import alpha_core, elements, element_graphs, media, matte_treatment, element_finishing


REVISION = 'explosion-effective-alpha-review-v1'
_lock = threading.RLock()


def _bound(root: Path, path: str | Path) -> Path:
    path = (root / path).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('An effective-alpha input/cache is outside this project.')
    return path


def _context(root: Path, row: dict, take: dict, treatment=None, context=None):
    root = Path(root).resolve()
    tid = row.get('id', '')
    if not isinstance(tid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', tid):
        raise ValueError('Invalid effective-alpha take id.')
    spec = row['plan']; frozen = matte_treatment.policy(treatment) if treatment is not None else spec.get('alpha_core')
    if frozen is None:
        raise ValueError('This take has no frozen explosion-core guide. Prepare a new explosion version; raw AlphaGen is retained.')
    if treatment is None:alpha_core.validate_policy(frozen)
    body = treatment is not None and element_finishing.policy(treatment)['body_matte']
    if body:
        if not spec.get('alpha_core'):raise ValueError('Original body needs captured simulation alpha. Prepare a new element guide.')
        frozen = {**frozen, 'body_finish': element_finishing.policy(treatment)}
    needs_guide = treatment is None or body
    n = spec['active_frames']; geometry = take['geometry']
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 121:
        raise ValueError('Invalid effective-alpha frame count.')
    x, y, w, h = geometry['content_rect']
    if ((w, h) != (spec['width'], spec['height']) or min(x, y) < 0
            or x+w > geometry['width'] or y+h > geometry['height']):
        raise ValueError('Effective-alpha geometry does not match its frozen guide.')
    rd = _bound(root, row['inputs'])
    manifest_file = _bound(root, rd/'manifest.json')
    expected = row.get('post_manifest_sha256')
    if (not expected or take['provenance'].get('post_manifest_sha256') != expected
            or elements._sha(manifest_file) != expected):
        raise ValueError('Frozen guide-alpha manifest changed; capture a new version.')
    manifest = json.loads(manifest_file.read_text())
    guide_hashes = {f'{i:05d}.png': manifest.get('files', {}).get(f'guide_alpha/{i:05d}.png') for i in range(n)} if needs_guide else {}
    if any(not isinstance(v, str) or not re.fullmatch(r'[a-f0-9]{64}', v) for v in guide_hashes.values()):
        raise ValueError('The frozen take does not contain every simulation-alpha frame; capture a new version.')
    asset = take['assets']['alpha']
    for name, digest in [('path', 'sha256'), ('frame_manifest', 'frame_manifest_sha256')]:
        if elements._sha(_bound(root, asset[name])) != asset[digest]:
            raise ValueError('The original AlphaGen asset changed; no assisted review was generated.')
    fingerprint = {'revision': REVISION, 'take_id': tid, 'request_sha256': row['request_sha256'],
                   'post_manifest_sha256': expected, 'guide_alpha_frames': guide_hashes,
                   'raw_alpha_sha256': asset['sha256'],
                   'decoded_alpha_manifest_sha256': asset['frame_manifest_sha256'],
                   'geometry': geometry, 'active_frames': n, 'fps': 24, 'policy': frozen}
    if body and context is not None:
        fingerprint['body_geometry'] = context[0].get('geometry', {})
        fingerprint['body_colliders'] = context[1].get('element', {}).get('colliders', [])
    signature = element_graphs.digest(fingerprint)
    directory = _bound(root, Path('element_alpha_reviews')/tid/signature)
    metadata = {'signature': signature, 'frames': n, 'fps': 24,
                'width': geometry['width'], 'height': geometry['height'],
                'policy': frozen, 'view_only': True, 'model_alpha_unchanged': True}
    return root, rd, asset, geometry, fingerprint, directory, metadata


def _validate_cached(directory: Path, fingerprint: dict, metadata: dict) -> Path:
    video = directory/'effective-alpha.mp4'; record = json.loads((directory/'review.json').read_text())
    if (record.get('inputs') != fingerprint or record.get('signature') != metadata['signature']
            or any(record.get(key) != value for key, value in metadata.items())
            or elements._sha(video) != record.get('video_sha256')):
        raise ValueError('The local assisted-alpha review changed; inspect the retained cache.')
    return video


def validate_movie(root: Path, row: dict, take: dict, signature: str, treatment=None, context=None) -> Path:
    """Verify a current cached viewing copy without rescanning decoded PNGs."""
    _, _, _, _, fingerprint, directory, metadata = _context(root, row, take,treatment,context)
    if signature != metadata['signature']:
        raise ValueError('This assisted-alpha review belongs to different inputs.')
    return _validate_cached(directory, fingerprint, metadata)


def build(root: Path, row: dict, take: dict, treatment=None, context=None) -> dict:
    """Encode active24fps assisted alpha locally, retaining model padding size."""
    with _lock:
        root, rd, asset, geometry, fingerprint, directory, metadata = _context(root, row, take,treatment,context)
        video = directory/'effective-alpha.mp4'
        if video.exists() or (directory/'review.json').exists():
            _validate_cached(directory, fingerprint, metadata)
            return {**metadata, 'path': str(video), 'video_sha256': elements._sha(video), 'cached': True}
        directory.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.assisted-alpha-', dir=directory.parent) as folder:
            temporary = Path(folder); frames = temporary/'frames'; frames.mkdir()
            x, y, w, h = geometry['content_rect']
            model_hashes = json.loads(_bound(root, asset['frame_manifest']).read_text())
            body = treatment is not None and element_finishing.policy(treatment)['body_matte']
            needs_guide = treatment is None or body
            masks = element_finishing.BodyMasks(*context, take, w, h) if body and context is not None else None
            for i in range(metadata['frames']):
                name = f'{i:05d}.png'; gp = _bound(root, rd/'guide_alpha'/name)
                if needs_guide and elements._sha(gp) != fingerprint['guide_alpha_frames'][name]:
                    raise ValueError('A frozen simulation-alpha frame changed; capture a new version.')
                model_path = _bound(root, Path(asset['frames_dir'])/name)
                if elements._sha(model_path) != model_hashes.get(str(model_path)):
                    raise ValueError('A decoded AlphaGen frame changed; no assisted review was generated.')
                raw = cv2.imread(str(model_path), cv2.IMREAD_UNCHANGED)
                guide = cv2.imread(str(gp), cv2.IMREAD_UNCHANGED) if needs_guide else None
                if (raw is None or raw.dtype != np.uint8 or raw.shape != (geometry['height'], geometry['width'])
                        or (needs_guide and (guide is None or guide.shape != (h, w)))):
                    raise ValueError('Effective-alpha model/guide frames do not match their frozen geometry.')
                effective = raw.copy()
                source=raw[y:y+h, x:x+w].astype(np.float32)/255
                if body:
                    frame = row['plan'].get('source_frames', list(range(metadata['frames'])))[i]
                    protected = masks.pixels(frame) if masks else None
                    opacity = element_finishing.effective_alpha(source, guide.astype(np.float32)/255, treatment, protected)
                else:
                    opacity = (matte_treatment.apply(source,treatment) if treatment is not None else
                               alpha_core.assisted_alpha(source,guide,frozen_policy=metadata['policy']))
                effective[y:y+h, x:x+w] = np.uint8(np.floor(opacity*255+.5))
                if not cv2.imwrite(str(frames/name), effective):
                    raise ValueError('Could not preserve a local assisted-alpha review frame.')
            args = ['ffmpeg', '-v', 'error', '-y', '-framerate', '24', '-i', str(frames/'%05d.png'),
                    '-an', '-frames:v', str(metadata['frames']), '-c:v', 'libx264', '-qp', '0',
                    '-pix_fmt', 'yuv444p', '-movflags', '+faststart', str(temporary/'effective-alpha.mp4')]
            result = subprocess.run(args, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise ValueError('Could not encode local assisted alpha: '+result.stderr[-700:])
            try:
                actual = media.probe(temporary/'effective-alpha.mp4')
            except subprocess.CalledProcessError as exc:
                raise ValueError('Could not inspect the local assisted-alpha viewing copy.') from exc
            if ((actual['frames'], actual['width'], actual['height'])
                    != (metadata['frames'], metadata['width'], metadata['height'])
                    or abs(actual['fps']-24) > .01):
                raise ValueError('The assisted-alpha viewing copy failed timing/canvas validation.')
            # Recheck frozen declarations before publishing a potentially slow encode.
            current = _context(root, row, take,treatment,context)
            if current[4] != fingerprint:
                raise ValueError('The assisted-alpha inputs changed during encoding.')
            for i in range(metadata['frames']):
                name = f'{i:05d}.png'
                if ((needs_guide and elements._sha(_bound(root, rd/'guide_alpha'/name)) != fingerprint['guide_alpha_frames'][name])
                        or elements._sha(_bound(root, Path(asset['frames_dir'])/name))
                        != model_hashes.get(str(_bound(root, Path(asset['frames_dir'])/name)))):
                    raise ValueError('The frozen alpha frames changed during local review encoding.')
            video_sha = elements._sha(temporary/'effective-alpha.mp4')
            record = {**metadata, 'inputs': fingerprint, 'video_sha256': video_sha}
            (temporary/'review.json').write_text(json.dumps(record, sort_keys=True, indent=2)+'\n')
            directory.mkdir(exist_ok=True)
            os.replace(temporary/'effective-alpha.mp4', video)
            os.replace(temporary/'review.json', directory/'review.json')
        return {**metadata, 'path': str(video), 'video_sha256': video_sha, 'cached': False}
