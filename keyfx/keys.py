"""Build the keyframe images: the shot's own frame with every effect's stamp drawn in its state at that frame."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from . import presets
from .config import CH, CW
from .media import pad, read_frame

WARM = np.array([110, 180, 255], np.float32)  # BGR glow tint


def _phase(preset: dict, name: str) -> dict:
    return next(ph for ph in preset['phases'] if ph['phase'] == name)


def _place(img: np.ndarray, rgba: np.ndarray, x: float, y: float, scale: float, rot: float, flip: bool,
           anchor: tuple[float, float], opacity: float, mask: np.ndarray | None = None) -> None:
    CH, CW = img.shape[:2]
    s = max(scale * CH, 4) / rgba.shape[0]
    st = cv2.resize(rgba, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    if flip:
        st = st[:, ::-1].copy()
    h, w = st.shape[:2]
    ax = (1 - anchor[0]) * w if flip else anchor[0] * w
    ay = anchor[1] * h
    M = cv2.getRotationMatrix2D((float(ax), float(ay)), -float(rot), 1.0)
    M[0, 2] += x * CW - ax
    M[1, 2] += y * CH - ay
    wp = cv2.warpAffine(st, M, (CW, CH), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    a = np.clip(wp[..., 3:4] * opacity, 0, 1)
    img *= 1 - a
    img += wp[..., :3] * a
    if mask is not None:
        np.maximum(mask, a[..., 0], out=mask)


def _glow(img: np.ndarray, x: float, y: float, radius: float, strength: float) -> None:
    CH, CW = img.shape[:2]
    _yy, _xx = np.mgrid[0:CH, 0:CW].astype(np.float32)
    r = np.hypot(_xx - x * CW, _yy - y * CH)
    img += (np.exp(-(r / max(radius, 1)) ** 2) * strength)[..., None] * WARM


def _hole(img: np.ndarray, x: float, y: float, size: float, seed: int) -> None:
    """Procedural impact damage: a dark chipped crater with a pale dusty rim and a few cracks."""
    CH, CW = img.shape[:2]
    _yy, _xx = np.mgrid[0:CH, 0:CW].astype(np.float32)
    rng = np.random.default_rng(seed)
    R = max(size * CH * 0.5, 2)
    cx, cy = x * CW, y * CH
    ang = np.arctan2(_yy - cy, _xx - cx)
    wob = 1 + 0.25 * np.sin(ang * 5 + rng.uniform(0, 6)) + 0.15 * np.sin(ang * 11 + rng.uniform(0, 6))
    d = np.hypot(_xx - cx, _yy - cy) / (R * wob)
    crater = np.clip(1 - d, 0, 1) ** 0.6
    rim = np.exp(-((d - 1.25) / 0.35) ** 2) * 0.25
    img *= (1 - 0.75 * crater)[..., None]
    img += (rim * 60)[..., None]
    for _ in range(4):                                   # hairline cracks
        a = rng.uniform(0, 2 * np.pi); L = R * rng.uniform(1.5, 3.0)
        p1 = (int(cx + np.cos(a) * R * 0.8), int(cy + np.sin(a) * R * 0.8)); p2 = (int(cx + np.cos(a) * L), int(cy + np.sin(a) * L))
        layer = np.zeros((CH, CW), np.float32); cv2.line(layer, p1, p2, 1.0, 1, cv2.LINE_AA)
        img *= (1 - 0.5 * layer)[..., None]


def _seed(*parts: str) -> int:
    import zlib
    return zlib.crc32('/'.join(parts).encode()) & 0xffff


def active(effects: list[dict], frame: int) -> list[tuple[dict, dict, dict]]:
    """(effect, key, phase) for every effect that draws something at `frame`."""
    out = []
    for e in effects:
        k = state_at(e, frame)
        if k is not None:
            out.append((e, k, _phase(presets.get(e['preset']), k['phase'])))
    return out


def state_at(effect: dict, frame: int) -> dict | None:
    """The key whose look this effect has at `frame`: the latest key at or before it. None before the effect or after it ends."""
    preset = presets.get(effect['preset'])
    keys = sorted(effect['keys'], key=lambda k: k['frame'])
    past = [k for k in keys if k['frame'] <= frame]
    if not past:
        return None
    k = past[-1]
    ph = _phase(preset, k['phase'])
    if not ph.get('stamp'):
        return None
    if k is keys[-1] and frame > k['frame'] and not ph.get('persist'):
        return None
    return k


def compose(base: np.ndarray, effects: list[dict], frame: int, mask: np.ndarray | None = None) -> np.ndarray:
    """Draw every active effect onto a copy of `base`. If `mask` (CH x CW float) is given, it collects where stamps were drawn."""
    CH, CW = base.shape[:2]
    img = base.copy()
    for e in effects:
        k = state_at(e, frame)
        if k is None:
            continue
        preset = presets.get(e['preset'])
        ph = _phase(preset, k['phase'])
        x, y, sc, rot = k['x'], k['y'], k['scale'], k.get('rot', 0.0)
        if ph['stamp'] == 'hole':
            _hole(img, x, y, sc, _seed(e['id'], k['phase']))
            continue
        if ph.get('glow'):
            _glow(img, x, y, ph.get('glow_radius', 0.8) * sc * CH, ph['glow'])
        _place(img, presets.stamp(e['preset'], ph['stamp']), x, y, sc, rot, e.get('flip', False),
               tuple(ph.get('anchor', (0.5, 0.5))), ph.get('opacity', 1.0), mask)
        if ph.get('with_hole'):
            _hole(img, x, y, sc * ph['with_hole'], _seed(e['id'], 'hole'))
    return img


HOLD = 16  # an effect that persists gets a repeat key this often, so the structure never "resets" it between keys


def hold_frames(effects: list[dict], n_frames: int | None) -> dict[int, int]:
    """Extra keys for effects that persist to the end of the shot: {frame: the real key frame it repeats}."""
    if not n_frames:
        return {}
    real = {f for f, _ in _real_frames(effects, n_frames)}
    out: dict[int, int] = {}
    for e in effects:
        ks = sorted((k for k in e['keys'] if k.get('enabled', True) is not False), key=lambda k: k['frame'])
        if not ks or not _phase(presets.get(e['preset']), ks[-1]['phase']).get('persist'):
            continue
        last = ks[-1]['frame']
        f = last + HOLD
        while f <= n_frames - 1:
            if all(abs(f - r) > HOLD // 3 for r in real | set(out)):
                out[f] = last
            f += HOLD
        end = n_frames - 1                                   # always pin the last frame too
        if end - last > HOLD // 2 and all(abs(end - r) > HOLD // 3 for r in real | set(out)):
            out[end] = last
    return out


def prerolled(e: dict) -> bool:
    """Already going at frame 0 (started before the shot)."""
    return any(k['frame'] < 0 for k in e['keys'] if _phase(presets.get(e['preset']), k['phase']).get('stamp')) and state_at(e, 0) is not None


def _real_frames(effects: list[dict], n_frames: int | None = None) -> list[tuple[int, bool]]:
    out: dict[int, bool] = {}
    for e in effects:
        preset = presets.get(e['preset'])
        for k in e['keys']:
            if k.get('enabled', True) is False or k['frame'] < 0 or (n_frames and k['frame'] > n_frames - 1):
                continue
            hard = bool(_phase(preset, k['phase']).get('hard'))
            out[k['frame']] = out.get(k['frame'], False) or hard
        if prerolled(e):
            out.setdefault(0, False)                      # the anchor frame shows it already going
    return sorted(out.items())


def key_frames(effects: list[dict], n_frames: int | None = None) -> list[tuple[int, bool]]:
    """Every keyed frame across all effects (plus hold keys for persisting effects when the shot length is given), and whether
    it must be a hard key (e.g. a one-frame muzzle flash)."""
    out = dict(_real_frames(effects, n_frames))
    for f in hold_frames(effects, n_frames):
        out.setdefault(f, False)
    return sorted(out.items())


def guide_effects(effects: list[dict], mode: str = 'sparse') -> list[dict]:
    """Select generation phases without changing the full live-preview timeline."""
    if mode not in ('sparse', 'full'):
        raise ValueError('Choose natural motion or all-phase guides.')
    if mode == 'full':
        return effects
    out = []
    for effect in effects:
        phases = presets.get(effect['preset']).get('guide_phases')
        out.append({**effect, 'keys': [k for k in effect['keys'] if k['phase'] in phases]} if phases else effect)
    return out


def guide_frames(effects: list[dict], n_frames: int | None = None, mode: str = 'sparse') -> list[tuple[int, bool]]:
    out = dict(key_frames(guide_effects(effects, mode), n_frames))
    for effect in effects:
        if prerolled(effect):
            out.setdefault(0, False)  # one opening state when the early blast happened before the clip
    return sorted(out.items())


def build(base_dir: Path, effects: list[dict], out_dir: Path, n_frames: int | None = None,
          guide_mode: str = 'full') -> list[tuple[int, Path, bool]]:
    """Write one padded PNG key per keyed frame; returns [(frame, path, hard)]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    res = []
    for f, hard in guide_frames(effects, n_frames, guide_mode):
        p = out_dir / f'key_{f:04d}.png'
        cv2.imwrite(str(p), pad(compose(read_frame(base_dir, f), effects, f)))
        res.append((f, p, hard))
    return res


def preview(base_dir: Path, effects: list[dict], frame: int) -> np.ndarray:
    return np.clip(compose(read_frame(base_dir, frame), effects, frame), 0, 255).astype(np.uint8)
