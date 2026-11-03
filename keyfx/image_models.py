"""Task defaults and real image-edit dispatch shared by the scene and effects tools."""
from pathlib import Path
import json

import cv2
import numpy as np

from . import comfy

DEFAULTS = {'location': 'qwen', 'dressing': 'flux3', 'polish': 'nano', 'flash': 'nano'}
CATALOG = [
    {'id': 'qwen', 'label': 'Qwen Image Edit', 'open_weights': True},
    {'id': 'flux3', 'label': 'FLUX 3', 'open_weights': True},
    {'id': 'nano', 'label': 'Nano Banana Pro', 'open_weights': False},
]


def validate(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('Choose an image model for a supported editing task.')
    if any(not isinstance(model, str) or model not in {m['id'] for m in CATALOG} for model in value.values()):
        raise ValueError('Unknown image model.')
    from . import connections
    cfg = connections.current()
    defaults = {**DEFAULTS, 'polish': 'qwen', 'flash': 'qwen'} if cfg.mode == 'local' and not cfg.nano_available else DEFAULTS
    return {**defaults, **value}


def selected(project: dict, task: str) -> str:
    return validate(project.get('image_models', {}))[task]


def label(model: str) -> str:
    return next(m['label'] for m in CATALOG if m['id'] == model)


def graph(model: str, text: str, seed: int) -> dict:
    from . import flashes, polish, setdressing
    validate({'location': model})
    if model == 'nano': return flashes.graph(text, seed)
    if model == 'qwen': return polish.graph(text, seed)
    return setdressing.graph(text, seed)


def prepare(frame: np.ndarray, model: str) -> np.ndarray:
    """Pad to the editor's grid while retaining every source pixel."""
    h, w = frame.shape[:2]
    grid = 32 if model == 'flux3' else 16 if model == 'qwen' else 1
    return cv2.copyMakeBorder(frame, 0, (-h) % grid, 0, (-w) % grid, cv2.BORDER_REPLICATE)


def restore(raw: np.ndarray, canvas: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Normalize an editor's canvas, then remove padding; reject lower-res outputs."""
    if raw is None: raise RuntimeError('The image editor returned an unreadable image.')
    h, w = canvas.shape[:2]
    rh, rw = raw.shape[:2]
    if rw < w or rh < h:
        raise RuntimeError(f'The image editor returned {rw}x{rh}, below the requested {w}x{h}; no upscaled substitute was accepted.')
    if abs((rw / rh) / (w / h) - 1) > .03:
        raise RuntimeError('The image editor returned an unexpected aspect ratio.')
    if (rw, rh) != (w, h): raw = cv2.resize(raw, (w, h), interpolation=cv2.INTER_AREA)
    return raw[:size[1], :size[0]]


def edit(source: Path, text: str, seed: int, out: Path, model: str, mask: Path | None = None) -> Path:
    """Use the existing tested graph definitions; masks/compositing stay with each task."""
    from . import flashes, polish, setdressing
    validate({'location': model})
    out.mkdir(parents=True, exist_ok=True)
    recipe = graph(model, text, seed)
    (out / 'graph.json').write_text(json.dumps(recipe, indent=2))
    (out / 'model.json').write_text(json.dumps({'id': model, 'label': label(model), 'seed': seed}))
    if model == 'nano':
        files = comfy.run_partner_graph(recipe, flashes.BIND['clean'], source,
                                        flashes.BIND['output'], out, 'Bang On image edit')
    elif model == 'qwen':
        files = comfy.run_graph(recipe, {'img': ('image', source)}, 'save', out)
    else:
        im = cv2.imread(str(source))
        if im is None: raise ValueError('The edit source could not be read.')
        if im.shape[0] % 32 or im.shape[1] % 32:
            raise ValueError('FLUX inputs must be padded to multiples of 32.')
        if mask is None:
            mask = out / 'full_mask.png'
            cv2.imwrite(str(mask), np.full(im.shape[:2], 255, np.uint8))
        files = comfy.run_graph(recipe,
                                {'img': ('image', source), 'mask': ('image', mask)}, 'save', out)
    if not files: raise RuntimeError(f'{label(model)} returned no image.')
    return Path(files[0])
