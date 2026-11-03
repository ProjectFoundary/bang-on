"""Step 3: muzzle flashes, one frame at a time, with an image-editing model (Nano Banana Pro by default).

Ported from Tyson's muzzle_hub project (MIT): a native 1920x1080 working canvas padded to 2048x1152 for the model, the
"flame size" text prompt (placement given as pixel coordinates, never drawn on the image) and the clean-only
Gemini 3 Pro Image workflow. KeyFX change: only a soft region around the flash and its light is taken from the
edit; the rest of the frame stays exactly the base, so flash frames don't flicker against their neighbours.
Each flash costs real credits (about 35 per flash on Comfy Cloud), so generation is capped by a budget.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path

import cv2
import numpy as np

from . import comfy, image_models
from .config import CH, CW, PKG, DATA

WORK = (1920, 1080)                       # working canvas
PAD = {'left': 64, 'right': 64, 'top': 36, 'bottom': 36}
MODEL_CANVAS = (2048, 1152)
CREDITS_PER_FLASH = 35                   # observed in muzzle_hub (34-36); an estimate, not a provider price
WORKFLOW = json.loads((PKG / 'workflows' / 'nano_flash.json').read_text())
BIND = {'clean': '11', 'model': '35', 'output': '30'}
SIZES = {'small': 32, 'medium': 96, 'large': 240}      # flame-body length presets (working pixels)


def budget_credits() -> int | None:
    """Local muzzle allowance: None = explicitly unlimited; 0 = disabled.

    An explicit environment value wins over this installation's saved preference.
    Fresh installations remain disabled until the user selects an allowance.
    """
    value = os.environ.get('KEYFX_FLASH_CREDITS')
    if value is None:
        try:
            value = json.loads((DATA / 'local-settings.json').read_text()).get('flash_credit_allowance', 0)
        except (OSError, ValueError, AttributeError):
            value = 0
    if isinstance(value, str) and value.strip().lower() == 'unlimited':
        return None
    try:
        return max(0, int(value))
    except (ValueError, TypeError, OverflowError):
        return 0


def geometry(flash: dict) -> dict:
    w, h = WORK
    x, y = flash['origin']['x'] * (w - 1), flash['origin']['y'] * (h - 1)
    tx, ty = flash['tip']['x'] * (w - 1), flash['tip']['y'] * (h - 1)
    length = math.hypot(tx - x, ty - y)
    if length < 4:
        raise ValueError('flash too short: drag from the muzzle to where the flame should end')
    return {'origin_px': (x, y), 'tip_px': (tx, ty), 'length_px': length, 'angle_degrees': math.degrees(math.atan2(ty - y, tx - x))}


def prompt(flash: dict) -> str:
    """muzzle_hub's flame_size_v2 prompt."""
    g = geometry(flash)
    (x, y), (tx, ty), length = g['origin_px'], g['tip_px'], g['length_px']
    x += PAD['left']; tx += PAD['left']; y += PAD['top']; ty += PAD['top']
    angle = math.degrees(math.atan2(ty - y, tx - x))
    size = ('a tiny, compact pistol-scale flash' if length <= 72 else
            'a large, elongated machine-gun-scale flash' if length >= 270 else 'a medium-sized muzzle flash')
    width = max(4, length * .32)
    return f'''Edit the supplied clean photograph. Add exactly one photographic muzzle flash
attached to the existing visible barrel. Preserve the existing weapon type and geometry.

FLAME SIZE AND PLACEMENT are the primary edit instructions.
Create {size}. The visible luminous flame body, excluding bloom and scene illumination,
should extend {length:.0f} pixels ({100*length/WORK[0]:.1f}% of the working picture width) from muzzle to flame tip.
Its maximum transverse width should be about {width:.0f} pixels.
The supplied padded image is {MODEL_CANVAS[0]} by {MODEL_CANVAS[1]} pixels. The muzzle attachment is at
({x:.0f}, {y:.0f}); the flame tip should reach ({tx:.0f}, {ty:.0f}).
The screen-space axis is {angle:.1f} degrees clockwise from screen-right.
Use the visible barrel as the attachment authority and the specified endpoint as the size target.
These coordinates are instructions only; return the photograph without measurement graphics.

Render a white-yellow core and irregular warm flame lobes with photographic softness
matching the plate. Keep the luminous flame body within the requested length;
soft bloom can extend slightly beyond it. Add physically plausible brief illumination
and reflections on nearby visible skin, hands, clothing, weapon and surfaces,
scaled to this flash size and respecting occlusion. Preserve ambient exposure and
grade away from the flash. The requested flame length is not the light-spill radius.

Keep the actor's identity, expression, pose, hand anatomy, weapon, background geometry,
framing, focus, grain and motion blur unchanged. Add only the flash and its light response.
Keep the original composition and black padding border. Return one edited photograph.
'''


def graph(text: str, seed: int, prefix: str = 'keyfx/flash') -> dict:
    g = copy.deepcopy(WORKFLOW)
    g[BIND['model']]['inputs'].update(prompt=text, seed=int(seed), resolution='2K')
    g[BIND['output']]['inputs']['filename_prefix'] = prefix
    return g


def to_model_canvas(frame: np.ndarray) -> np.ndarray:
    """Native KeyFX content -> 2048x1152 padded model input."""
    a = cv2.resize(np.clip(frame, 0, 255).astype(np.uint8), WORK, interpolation=cv2.INTER_LANCZOS4)
    return cv2.copyMakeBorder(a, PAD['top'], PAD['bottom'], PAD['left'], PAD['right'], cv2.BORDER_CONSTANT, value=0)


def from_model_canvas(raw: np.ndarray, size: tuple[int, int] = (CW, CH)) -> np.ndarray:
    """Model output -> base-frame resolution. Exact model aspect is required."""
    h, w = raw.shape[:2]
    if (w, h) != MODEL_CANVAS:
        if abs((w / h) / (MODEL_CANVAS[0] / MODEL_CANVAS[1]) - 1) > .03:
            raise ValueError(f'unexpected model canvas {w}x{h}')
        if w < MODEL_CANVAS[0] or h < MODEL_CANVAS[1]:
            raise ValueError('Flash editor returned a lower-resolution canvas; no upscaled substitute was accepted.')
        raw = cv2.resize(raw, MODEL_CANVAS, interpolation=cv2.INTER_AREA)
    a = raw[PAD['top']:PAD['top'] + WORK[1], PAD['left']:PAD['left'] + WORK[0]]
    return cv2.resize(a, size, interpolation=cv2.INTER_LANCZOS4 if size[0] > WORK[0] else cv2.INTER_AREA)


def _flash_px(flash: dict, size: tuple[int, int] = (CW, CH)):
    g = geometry(flash); k = size[0] / WORK[0]
    (x, y), (tx, ty) = [(p[0] * k, p[1] * k) for p in (g['origin_px'], g['tip_px'])]
    return x, y, tx, ty, g['length_px'] * k


def core(flash: dict, size: tuple[int, int] = (CW, CH)) -> np.ndarray:
    """Tight soft capsule around the flame body (taken whole from the edit). CH x CW x 1."""
    x, y, tx, ty, L = _flash_px(flash, size)
    yy, xx = np.mgrid[0:size[1], 0:size[0]].astype(np.float32)
    dx, dy = tx - x, ty - y
    u = np.clip(((xx - x) * dx + (yy - y) * dy) / max(L * L, 1), -0.05, 1.15)
    d = np.hypot(xx - (x + u * dx), yy - (y + u * dy))
    r = 0.30 * L + 6 * size[0] / CW
    m = np.clip((r * 1.6 - d) / (r * 0.8), 0, 1)
    return (m * m * (3 - 2 * m))[..., None]


def spill(flash: dict, size: tuple[int, int] = (CW, CH)) -> np.ndarray:
    """Where the flash may light the scene (only brightening is taken there). CH x CW x 1."""
    x, y, tx, ty, L = _flash_px(flash, size)
    yy, xx = np.mgrid[0:size[1], 0:size[0]].astype(np.float32)
    cx, cy = x + (tx - x) * 0.35, y + (ty - y) * 0.35
    r = np.hypot(xx - cx, yy - cy) / (1.6 * L + 60 * size[0] / CW)
    return np.exp(-(r ** 2) * 2.5)[..., None]


region = core   # kept for callers/tests: the area replaced outright


def match_colour(edited: np.ndarray, base: np.ndarray, flash: dict) -> np.ndarray:
    """Undo the editor's global regrade: per-channel mean/std transfer, measured away from the flash."""
    away = spill(flash, base.shape[1::-1])[..., 0] < 0.05
    e, b = edited.astype(np.float32), base.astype(np.float32)
    out = np.empty_like(e)
    for c in range(3):
        em, es = e[..., c][away].mean(), e[..., c][away].std() + 1e-3
        bm, bs = b[..., c][away].mean(), b[..., c][away].std() + 1e-3
        out[..., c] = (e[..., c] - em) * (bs / es) + bm
    return out


def composite(base: np.ndarray, edited: np.ndarray, flash: dict) -> np.ndarray:
    """Flame from the edit; elsewhere only the light it adds (never its redrawing of the scene)."""
    b = base.astype(np.float32)
    e = match_colour(edited, base, flash)
    light = np.clip(e - b, 0, None)
    size = base.shape[1::-1]
    light = cv2.GaussianBlur(light, (0, 0), 3 * size[0] / CW)       # light, not detail
    lit = b + light * spill(flash, size)
    m = core(flash, size)
    return np.clip(lit * (1 - m) + e * m, 0, 255).astype(np.uint8)


def generate(base: np.ndarray, flash: dict, seed: int, out_dir: Path, model: str = 'nano') -> np.ndarray:
    """One paid edit: composite at the base frame's native resolution."""
    out_dir.mkdir(parents=True, exist_ok=True)
    inp = out_dir / 'clean.png'
    cv2.imwrite(str(inp), to_model_canvas(base))
    text = prompt(flash)
    (out_dir / 'prompt.txt').write_text(text)
    files = comfy.run_partner_graph(graph(text, seed), BIND['clean'], inp, BIND['output'], out_dir / 'raw', 'Bang On muzzle flash') if model == 'nano' else [image_models.edit(inp, text, seed, out_dir / 'raw', model)]
    if not files:
        raise RuntimeError('flash job returned no image')
    edited = from_model_canvas(cv2.imread(str(files[0])), base.shape[1::-1])
    cv2.imwrite(str(out_dir / 'edited_full.png'), edited)
    return composite(base, edited, flash)


def generate_native_display(rgb: np.ndarray, flash: dict, seed: int, out_dir: Path, model='nano') -> np.ndarray:
    """Keep the established muzzle recipe on a fitted display proxy, not the HDR master.

    Letterboxing preserves portrait/non16:9 source geometry. The caller lifts only
    the localized finished display edit onto its native-colour master.
    """
    h,w=rgb.shape[:2];scale=min(WORK[0]/w,WORK[1]/h)
    cw,ch=max(1,round(w*scale)),max(1,round(h*scale));left,top=(WORK[0]-cw)//2,(WORK[1]-ch)//2
    fitted=np.zeros((WORK[1],WORK[0],3),np.uint8)
    fitted[top:top+ch,left:left+cw]=cv2.resize(cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR),(cw,ch))
    f=copy.deepcopy(flash)
    for k in ('origin','tip'):
        f[k]={'x':(left+flash[k]['x']*(cw-1))/(WORK[0]-1),'y':(top+flash[k]['y']*(ch-1))/(WORK[1]-1)}
    out=generate(fitted,f,seed,out_dir,model=model)
    # Lift the local change, not a down/upscaled replacement photograph.
    delta=(out.astype(np.float32)-fitted.astype(np.float32))[top:top+ch,left:left+cw]
    delta=cv2.cvtColor(cv2.resize(delta,(w,h),interpolation=cv2.INTER_LINEAR),cv2.COLOR_BGR2RGB)
    return np.clip(np.rint(rgb.astype(np.float32)+delta),0,255).astype(np.uint8)
