"""Effect presets: keyframe phases, stamps, look references and prompt fragments (keyfx/presets/<id>/preset.json)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .config import PRESETS

REPEAT = 16   # frames between repeat keys of a lasting effect
PREROLL_MAX = 240   # how far before frame 0 an effect may start


@lru_cache(maxsize=1)
def load_all() -> dict[str, dict]:
    out = {}
    for f in sorted(PRESETS.glob('*/preset.json')):
        p = json.loads(f.read_text())
        p['dir'] = str(f.parent)
        out[p['id']] = p
    return out


def get(preset_id: str) -> dict:
    presets = load_all()
    if preset_id not in presets:
        raise KeyError(f'unknown preset {preset_id!r}')
    return presets[preset_id]


@lru_cache(maxsize=64)
def stamp(preset_id: str, name: str) -> np.ndarray:
    """RGBA float32 (straight colour, alpha 0-1)."""
    im = cv2.imread(str(Path(get(preset_id)['dir']) / name), cv2.IMREAD_UNCHANGED)
    if im is None:
        raise FileNotFoundError(f'{preset_id}/{name}')
    if im.shape[2] == 3:
        im = np.dstack([im, np.full(im.shape[:2], 255, np.uint8)])
    im = im.astype(np.float32)
    im[..., 3] /= 255
    return im


def public(p: dict) -> dict:
    """What the web UI needs (no server paths)."""
    return {k: v for k, v in p.items() if k != 'dir'}


def new_effect(preset_id: str, onset: int, x: float, y: float, eid: str, n_frames: int) -> dict:
    """A fresh effect instance: one key per preset phase, frames offset from the onset, all at the clicked position."""
    p = get(preset_id)
    keys = []
    for ph in p['phases']:
        f = int(np.clip(onset + ph['offset'], -PREROLL_MAX, n_frames - 1))   # frames before 0: already going when the shot starts
        k = {'phase': ph['phase'], 'frame': f}
        if ph.get('stamp'):
            k.update(x=x, y=y, scale=ph.get('scale', 0.3), rot=0.0)
        keys.append(k)
    last = p['phases'][-1]
    if last.get('persist') and last.get('stamp'):
        # repeat keys keep a lasting effect alive to the end of the shot (the plate would otherwise reset it between keys);
        # they are ordinary keys, so the user can move them when the camera moves
        f, end = max(keys[-1]['frame'] + REPEAT, 0), n_frames - 1
        while f < end - REPEAT // 3:
            keys.append({**keys[-1], 'frame': f}); f += REPEAT
        if end - max(keys[-1]['frame'], 0) > REPEAT // 2 or keys[-1]['frame'] < 0:
            keys.append({**keys[-1], 'frame': end})
    d = p.get('direction', {})
    return {'id': eid, 'preset': preset_id, 'where': p.get('default_where', 'the marked spot'), 'flip': False, 'keys': keys,
            'anchors': [{'frame': int(np.clip(onset, 0, n_frames - 1)), 'x': x, 'y': y}],
            'angle': d.get('angle', 0), 'strength': d.get('strength', 0), 'size': 1.0}
