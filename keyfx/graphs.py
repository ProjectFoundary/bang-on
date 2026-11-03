"""MiniMax H3 Ref2VA API graphs for the two passes. Files are uploaded via the SDK, so file fields hold placeholders here."""
from __future__ import annotations

from pathlib import Path

from .config import (FPS, KEY_STRENGTH, LENGTH, MODELS, RENDER_H, RENDER_W, SIGMAS, render_length,
                     SCENE_DRAFT_W, SCENE_DRAFT_H, SCENE_LMS_STRENGTH, SCENE_UPSCALE_MODELS,
                     SCENE_UPSCALE_PROMPT, SCENE_UPSCALE_SIGMAS)

Uploads = dict[str, tuple[str, Path]]


def _n(cls: str, **inputs) -> dict:
    return {'class_type': cls, 'inputs': inputs}


def _base(prompt: str, refs: list[Path], structure: Path, seed: int, prefix: str, uploads: Uploads, n_frames: int = LENGTH,
          width: int = RENDER_W, height: int = RENDER_H) -> dict:
    length = render_length(n_frames)
    g = {
        'unet': _n('UNETLoader', unet_name=MODELS['unet'], weight_dtype='default'),
        'shift': _n('MiniMaxH3SigmaShift', model=['unet', 0], shift_video=12.0, shift_audio=3.0),
        'clip': _n('CLIPLoader', clip_name=MODELS['clip'], type='minimax', device='default'),
        'vae': _n('VAELoader', vae_name=MODELS['vae']),
        'proxy_video': _n('LoadVideo', file='structure.mp4'),
        'proxy_frames': _n('GetVideoComponents', video=['proxy_video', 0]),
        'latent': _n('EmptyMiniMaxH3LatentAV', width=width, height=height, length=length),
        'noise': _n('RandomNoise', noise_seed=int(seed)),
        'sampler': _n('KSamplerSelect', sampler_name='euler'),
        'sigmas': _n('ManualSigmas', sigmas=SIGMAS),
        'sample': _n('SamplerCustomAdvanced', noise=['noise', 0], guider=['guider', 0], sampler=['sampler', 0], sigmas=['sigmas', 0],
                     latent_image=['latent', 0]),
        'decode': _n('VAEDecode', samples=['sample', 0], vae=['vae', 0]),
        'video': _n('CreateVideo', images=['decode', 0], fps=FPS),
        'save': _n('SaveVideo', video=['video', 0], filename_prefix=prefix, format='mp4',
                   **{'format.codec': 'h264', 'format.codec.encoding': 're-encode', 'format.codec.encoding.crf': 10.0}),
    }
    uploads['proxy_video'] = ('file', structure)
    # Upload source files unchanged; H3 prepares references for this pass's canvas.
    cond = _n('MiniMaxH3ReferenceToVideo', clip=['clip', 0], vae=['vae', 0], prompt=prompt, width=width, height=height, length=length,
              ref_image_size='match')
    for i, ref in enumerate(refs):
        g[f'ref{i}'] = _n('LoadImage', image=f'ref{i}.png')
        cond['inputs'][f'ref_images.ref_image_{i}'] = [f'ref{i}', 0]
        uploads[f'ref{i}'] = ('image', ref)
    cond['inputs']['ref_videos.ref_video_0'] = ['proxy_frames', 0]
    g['cond'] = cond
    return g


def _guide(g: dict, uploads: Uploads, name: str, positive: list, image: Path, frame: int) -> list:
    g[f'img_{name}'] = _n('LoadImage', image=f'{name}.png')
    uploads[f'img_{name}'] = ('image', image)
    g[f'scale_{name}'] = _n('ImageScale', image=[f'img_{name}', 0], upscale_method='lanczos',
                           width=g['latent']['inputs']['width'], height=g['latent']['inputs']['height'], crop='disabled')
    g[f'guide_{name}'] = _n('MiniMaxH3AddGuide', positive=positive, vae=['vae', 0], latent=['latent', 0], image=[f'scale_{name}', 0],
                            frame_idx=int(frame))
    return [f'guide_{name}', 0]


def effect_graph(prompt: str, looks: list[Path], structure: Path, anchor: Path, keys: list[tuple[int, Path, bool]],
                 strength: str = 'guide', seed: int = 42, prefix: str = 'keyfx/effect', n_frames: int = LENGTH) -> tuple[dict, Uploads]:
    """Effect pass: structure video held for every step, frame-0 anchor always, keys soft (early steps only) unless hard."""
    uploads: Uploads = {}
    g = _base(prompt, looks, structure, seed, prefix, uploads, n_frames)
    anchored = _guide(g, uploads, 'anchor', ['cond', 0], anchor, 0)
    cut = KEY_STRENGTH[strength]
    early, late = anchored, anchored
    for frame, path, hard in sorted(keys):
        if frame == 0:
            continue                                     # frame 0 is the anchor
        early = _guide(g, uploads, f'k{frame}', early, path, frame)
        if hard and cut < 1.0:                           # hard keys (e.g. one-frame flashes) stay on for every step
            late = _guide(g, uploads, f'h{frame}', late, path, frame)
    if cut >= 1.0 or early == anchored:
        g['guider'] = _n('BasicGuider', model=['shift', 0], conditioning=early)
    else:
        g['early'] = _n('ConditioningSetTimestepRange', conditioning=early, start=0.0, end=cut)
        g['late'] = _n('ConditioningSetTimestepRange', conditioning=late, start=cut, end=1.0)
        g['both'] = _n('ConditioningCombine', conditioning_1=['early', 0], conditioning_2=['late', 0])
        g['guider'] = _n('BasicGuider', model=['shift', 0], conditioning=['both', 0])
    return g, uploads


def environment_graph(prompt: str, background: Path, structure: Path, seed: int = 42,
                      prefix: str = 'keyfx/environment', opening: Path | None = None, n_frames: int = LENGTH) -> tuple[dict, Uploads]:
    """Smaller scene pass; full original video and native still files remain the references."""
    uploads: Uploads = {}
    g = _base(prompt, [background], structure, seed, prefix, uploads, n_frames, SCENE_DRAFT_W, SCENE_DRAFT_H)
    conditioning = ['cond', 0]
    if opening is not None:
        conditioning = _guide(g, uploads, 'opening', conditioning, opening, 0)
    g['guider'] = _n('BasicGuider', model=['shift', 0], conditioning=conditioning)
    return g, uploads


def scene_upscale_graph(clean_video: Path, seed: int = 42, prefix: str = 'keyfx/scene_hd',
                        n_frames: int = LENGTH, strength: float = SCENE_LMS_STRENGTH) -> tuple[dict, Uploads]:
    """Tested 2x latent upscale + LMS finish. Only the clean scene supplies pixels or guidance."""
    g = {
        'source': _n('LoadVideo', file='clean_low.mp4'),
        'frames': _n('GetVideoComponents', video=['source', 0]),
        'unet': _n('UNETLoader', unet_name=MODELS['unet'], weight_dtype='default'),
        'speed': _n('LoraLoaderModelOnly', model=['unet', 0], lora_name=SCENE_UPSCALE_MODELS['speed'], strength_model=1.0),
        'lms': _n('LoraLoaderModelOnly', model=['speed', 0], lora_name=SCENE_UPSCALE_MODELS['lms'], strength_model=float(strength)),
        'shift': _n('MiniMaxH3SigmaShift', model=['lms', 0], shift_video=12.0, shift_audio=3.0),
        'clip': _n('CLIPLoader', clip_name=MODELS['clip'], type='minimax', device='default'),
        'vae': _n('VAELoader', vae_name=MODELS['vae']),
        'cond': _n('MiniMaxH3ReferenceToVideo', clip=['clip', 0], vae=['vae', 0], prompt=SCENE_UPSCALE_PROMPT,
                   width=RENDER_W, height=RENDER_H, length=render_length(n_frames), ref_image_size='match'),
        'encode': _n('VAEEncode', pixels=['frames', 0], vae=['vae', 0]),
        'latent_upscale': _n('MinimaxH3LatentUpscaler3D', latent=['encode', 0], model_name=SCENE_UPSCALE_MODELS['upscaler'],
                            mode='target dimensions', **{'mode.width': RENDER_W, 'mode.height': RENDER_H}, align=32,
                            enable_temporal_chunking=False, force_unload=True, device='cuda', precision='fp16'),
        'empty_av': _n('LTXVSeparateAVLatent', av_latent=['cond', 1]),
        'starting_latent': _n('LTXVConcatAVLatent', video_latent=['latent_upscale', 0], audio_latent=['empty_av', 1]),
        'high_guide': _n('ImageScale', image=['frames', 0], upscale_method='lanczos', width=RENDER_W, height=RENDER_H, crop='disabled'),
        'guide': _n('MiniMaxH3AddGuide', positive=['cond', 0], latent=['starting_latent', 0], frame_idx=0,
                    vae=['vae', 0], image=['high_guide', 0]),
        'guider': _n('BasicGuider', model=['shift', 0], conditioning=['guide', 0]),
        'noise': _n('RandomNoise', noise_seed=int(seed)),
        'sampler': _n('KSamplerSelect', sampler_name='euler'),
        'sigmas': _n('ManualSigmas', sigmas=SCENE_UPSCALE_SIGMAS),
        'sample': _n('SamplerCustomAdvanced', noise=['noise', 0], guider=['guider', 0], sampler=['sampler', 0],
                     sigmas=['sigmas', 0], latent_image=['starting_latent', 0]),
        'decode': _n('VAEDecode', samples=['sample', 0], vae=['vae', 0]),
        'video': _n('CreateVideo', images=['decode', 0], fps=FPS),
        'save': _n('SaveVideo', video=['video', 0], filename_prefix=prefix, format='mp4',
                   **{'format.codec': 'h264', 'format.codec.encoding': 're-encode', 'format.codec.encoding.crf': 10.0}),
    }
    return g, {'source': ('file', clean_video)}
