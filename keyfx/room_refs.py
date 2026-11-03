"""Optional room photographs: canonical uploads and immutable render snapshots."""
from __future__ import annotations

import hashlib
import io
import shutil
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGES = 6
MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
LIMITS = {'qwen': 2, 'flux3': MAX_IMAGES, 'nano': MAX_IMAGES}


def prepare(data: bytes, name: str) -> tuple[dict, bytes]:
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Use room photos smaller than 20 MB each.')
    try:
        with Image.open(io.BytesIO(data)) as src:
            if src.format not in ('JPEG', 'PNG', 'WEBP'):
                raise ValueError('Use JPG, PNG or WebP room photos.')
            if src.width * src.height > MAX_PIXELS:
                raise ValueError('Use room photos below 40 megapixels.')
            src.load()
            im = ImageOps.exif_transpose(src).convert('RGB')
            # Keep native uploaded pixels; model adapters make separate working
            # copies without replacing this full-resolution room master.
            out = io.BytesIO(); im.save(out, format='PNG')
            encoded = out.getvalue()
            rid = uuid.uuid4().hex[:16]
            label = ' '.join(name.replace('\\', '/').split('/')[-1].split())[:160] or 'Room photo'
            return ({'id': rid, 'file': f'room_references/{rid}.png', 'name': label,
                     'width': im.width, 'height': im.height,
                     'sha256': hashlib.sha256(encoded).hexdigest()}, encoded)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('That room photo could not be read. Use JPG, PNG or WebP.') from exc


def check_limit(model: str, count: int) -> None:
    limit = LIMITS[model]
    if count > limit:
        if model == 'qwen':
            raise ValueError('Qwen accepts two room photos alongside the plate. Choose Nano Banana or FLUX 3 to use more, or remove extra photos.')
        raise ValueError(f'Use up to {limit} room reference photos.')


def paths(root: Path, references: list[dict]) -> list[Path]:
    if len(references) > MAX_IMAGES:
        raise ValueError(f'Use up to {MAX_IMAGES} room reference photos.')
    folder = (root / 'room_references').resolve()
    if root.resolve() not in folder.parents:
        raise ValueError('Room references must belong to this project.')
    result = []
    for ref in references:
        path = (root / ref['file']).resolve()
        if path.parent != folder or not path.is_file():
            raise ValueError('A room reference is missing. Upload it again before generating.')
        if hashlib.sha256(path.read_bytes()).hexdigest() != ref['sha256']:
            raise ValueError('A room reference changed on disk. Upload it again before generating.')
        result.append(path)
    return result


def snapshot(root: Path, references: list[dict], output: Path) -> list[dict]:
    sources = paths(root, references)
    if not sources:return []
    output.mkdir(parents=True, exist_ok=True)
    result = []
    for ref, source in zip(references, sources):
        dest = output / source.name
        shutil.copyfile(source, dest)
        result.append({**ref, 'source_file': ref['file'], 'file': str(dest.relative_to(root))})
    return result
