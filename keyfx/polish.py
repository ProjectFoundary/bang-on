"""Polish keys: an image-edit pass that makes the pasted stamps look photographed in the shot.

Each stamp key goes through Qwen-Image-Edit-2511 (Lightning, 4 steps) with a prompt built from the presets' `polish` lines.
Only the area around the stamps is taken from the edit, so everything else in the key stays exactly the plate.
Results are cached by content, so previewing keys and then rendering pays for each edit once."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from . import comfy, keys, prompts, image_models
from .config import CH, CW
from .media import pad, read_frame

MODELS = {
    'unet': 'qwen_image_edit_2511_fp8mixed.safetensors',
    'lora': 'Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors',
    'clip': 'qwen_2.5_vl_7b_fp8_scaled.safetensors',
    'vae': 'qwen_image_vae.safetensors',
}
EDIT_W, EDIT_H = 1920, 1080    # native image-edit content; grid padding is added per model
GROW = 0.04                     # how far around the stamps the edit may change the frame (fraction of width)
FEATHER = 12                    # px


def _n(cls: str, **inputs) -> dict:
    return {'class_type': cls, 'inputs': inputs}


def graph(prompt: str, seed: int, prefix: str = 'keyfx/polish') -> dict:
    return {
        'unet': _n('UNETLoader', unet_name=MODELS['unet'], weight_dtype='default'),
        'lora': _n('LoraLoaderModelOnly', model=['unet', 0], lora_name=MODELS['lora'], strength_model=1.0),
        'ms': _n('ModelSamplingAuraFlow', model=['lora', 0], shift=3.0),
        'cfgn': _n('CFGNorm', model=['ms', 0], strength=1.0),
        'clip': _n('CLIPLoader', clip_name=MODELS['clip'], type='qwen_image', device='default'),
        'vae': _n('VAELoader', vae_name=MODELS['vae']),
        'img': _n('LoadImage', image='key.png'),
        'pos': _n('TextEncodeQwenImageEditPlus', clip=['clip', 0], prompt=prompt, vae=['vae', 0], image1=['img', 0]),
        'neg': _n('TextEncodeQwenImageEditPlus', clip=['clip', 0], prompt='', vae=['vae', 0], image1=['img', 0]),
        'enc': _n('VAEEncode', pixels=['img', 0], vae=['vae', 0]),
        'ks': _n('KSampler', model=['cfgn', 0], seed=int(seed), steps=4, cfg=1.0, sampler_name='euler', scheduler='simple',
                 positive=['pos', 0], negative=['neg', 0], latent_image=['enc', 0], denoise=1.0),
        'dec': _n('VAEDecode', samples=['ks', 0], vae=['vae', 0]),
        'save': _n('SaveImage', images=['dec', 0], filename_prefix=prefix),
    }


def prompt_for(effects: list[dict], frame: int) -> str | None:
    """The edit instruction for one key, or None when nothing at this frame asks to be polished."""
    lines = []
    for e, _k, ph in keys.active(effects, frame):
        if ph.get('polish'):
            d = prompts.direction_phrase(e)
            text = ph.get('polish_wind') if prompts.strong_wind(e) and ph.get('polish_wind') else ph['polish']
            lines.append(text.format(where=e.get('where') or 'the marked spot') + (f', {d}' if d else ''))
    if not lines:
        return None
    many = len(lines) > 1
    what = '; '.join(f'the {t}' for t in lines)
    return (f'Make the pasted {"effects" if many else "effect"} ({what}) look like {"real practical effects" if many else "a real practical effect"} '
            'filmed in this exact shot: real photographic detail, natural scale and perspective, matching focus, lighting and film grain, '
            'with its light and shadow falling naturally on nearby surfaces. '
            'Keep the people, the location, the camera framing and everything else exactly the same.')


def edit_mask(stamp_mask: np.ndarray) -> np.ndarray:
    """Grow and feather the drawn-stamp mask into the area the edit may replace (H x W x 1, 0-1)."""
    m = (stamp_mask > 0.05).astype(np.uint8)
    r = max(int(GROW * stamp_mask.shape[1]), 1)
    m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    return cv2.GaussianBlur(m.astype(np.float32), (0, 0), FEATHER * stamp_mask.shape[1] / CW)[..., None]


def polish_frame(comp: np.ndarray, stamp_mask: np.ndarray, prompt: str, seed: int, cache: Path, model: str = 'qwen') -> np.ndarray:
    """Edit one composited key (CH x CW BGR float/uint8) and blend the edit back inside the grown stamp mask."""
    comp8 = np.clip(comp, 0, 255).astype(np.uint8)
    h = hashlib.sha1(comp8.tobytes() + json.dumps([prompt, seed, MODELS] if model == 'qwen' else [prompt, seed, MODELS, model]).encode()).hexdigest()[:16]
    cache.mkdir(parents=True, exist_ok=True)
    edited = cache / f'{h}.png'
    if not edited.exists():
        inp = cache / f'{h}_in.png'
        work = image_models.prepare(comp8, model)
        cv2.imwrite(str(inp), work)
        files = comfy.run_graph(graph(prompt, seed), {'img': ('image', inp)}, 'save', cache / f'{h}_raw') if model == 'qwen' else [image_models.edit(inp, prompt, seed, cache / f'{h}_raw', model)]
        if not files:
            raise RuntimeError('polish job returned no image')
        ed = cv2.imread(str(files[0]))
        ed = image_models.restore(ed, work, comp8.shape[1::-1])
        cv2.imwrite(str(edited), ed)
        for f in files:
            f.unlink(missing_ok=True)
        # Keep model receipts and auxiliary masks for provenance.
        inp.unlink(missing_ok=True)
    ed = cv2.imread(str(edited)).astype(np.float32)
    m = edit_mask(stamp_mask)
    return comp8.astype(np.float32) * (1 - m) + ed * m


def polish_keys(base_dir: Path, effects: list[dict], out_dir: Path, seed: int, cache: Path, n_frames: int | None = None,
                workers: int = 4, model: str = 'qwen', guide_mode: str = 'full') -> list[tuple[int, Path, bool]]:
    """Like keys.build, but every key with a `polish` line goes through the edit pass. Hold keys (repeats of a persisting effect)
    reuse the polished area of the key they repeat, so the effect stays the same instead of being re-imagined. Returns [(frame, path, hard)]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    guides = keys.guide_effects(effects, guide_mode)
    holds = keys.hold_frames(guides, n_frames)
    frames = keys.guide_frames(effects, n_frames, guide_mode)
    done: dict[int, tuple[np.ndarray, np.ndarray]] = {}          # real key frame -> (polished image, grown mask)

    def write(f, img):
        p = out_dir / f'key_{f:04d}.png'
        cv2.imwrite(str(p), pad(img))
        return p

    def sig(f):  # what is drawn at f: identical stamps -> reuse one edit so a lasting effect keeps one look
        return tuple(sorted((e['id'], k['phase'], k.get('x'), k.get('y'), k.get('scale'), k.get('rot', 0), bool(e.get('flip')))
                            for e, k, _ph in keys.active(effects, f)))

    first: dict[tuple, int] = {}
    for f, _h in frames:
        if f not in holds:
            first.setdefault(sig(f), f)
    repeats = {f: first[sig(f)] for f, _h in frames if f not in holds and first[sig(f)] != f}

    def one(item):
        f, hard = item
        base = read_frame(base_dir, f).astype(np.float32)
        mask = np.zeros(base.shape[:2], np.float32)
        comp = keys.compose(base, effects, f, mask)
        prompt = prompt_for(effects, f)
        if prompt and mask.max() > 0:
            img = polish_frame(comp, mask, prompt, seed + f, cache, model=model)
            done[f] = (img, edit_mask(mask))
        else:
            img = comp
        return f, write(f, img), hard

    with ThreadPoolExecutor(workers) as pool:
        res = list(pool.map(one, [x for x in frames if x[0] not in holds and x[0] not in repeats]))
    holds = {**holds, **repeats}
    hard_of = dict(frames)
    for f, src in holds.items():
        comp = keys.compose(read_frame(base_dir, f).astype(np.float32), effects, f)
        if src in done:
            img, m = done[src]
            comp = comp * (1 - m) + img * m
        res.append((f, write(f, comp), hard_of.get(f, False)))
    return sorted(res)
