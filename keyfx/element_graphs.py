"""Plate-free LTX 2.5 element beauty and Alpha Gen recipes.

Both passes use the selected Comfy installation.  Alpha Gen is an ordinary
IC-LoRA matte pass, never a public-demo fallback or a second beauty render.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path


PROFILE = 'bang-on-ltx25-elements-v1'
GEN_FPS = 24
MAX_FRAMES = 121
SIGMAS = '1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0'
DEFAULT_MODELS = {
    'transformer': 'ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors',
    'text_encoder': 'gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors',
    'video_vae': 'ltx-2.5-video-vae-conv-bf16.safetensors',
    'audio_vae': 'ltx-2.5-audio-vae-bf16.safetensors',
    'fluid': 'AInVFX__ainvfx-fluid__ainvfx-fluid.safetensors',
    'alpha': 'Lightricks__LTX-25-22b-IC-LoRA-Alpha-Gen__ltx-2.5-22b-ic-lora-alpha-gen-0.9.safetensors',
}
DEFAULT_PROMPT = ('ainvfxfluid, a single isolated smoke or dust element following the guided '
                  'volume and motion, with shaded billowing detail, on a uniformly pure black '
                  'background, fixed camera, no room or photographed scenery')


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def model_frames(active_frames: int) -> int:
    if isinstance(active_frames, bool) or not isinstance(active_frames, int) or not 1 <= active_frames <= MAX_FRAMES:
        raise ValueError('An element must contain1–121 active frames at24fps (about5seconds).')
    return 1 + 8 * math.ceil((active_frames - 1) / 8)


def normalize_recipe(value: dict | None = None) -> dict:
    r = copy.deepcopy(value or {})
    if not isinstance(r, dict):
        raise ValueError('Enter an element recipe.')
    n = r.get('active_frames', 49)
    length = model_frames(n)
    fps = r.get('gen_fps', r.get('fps', GEN_FPS))
    if fps != GEN_FPS:
        raise ValueError('Element generation currently uses24fps; source timing is mapped separately.')
    seed = r.get('seed', 42)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**63:
        raise ValueError('Enter a non-negative integer seed below2^63.')
    alpha_seed = r.get('alpha_seed', seed)
    if isinstance(alpha_seed, bool) or not isinstance(alpha_seed, int) or not 0 <= alpha_seed < 2**63:
        raise ValueError('Enter a non-negative AlphaGen seed below2^63.')
    if 'alpha_seed' in r:
        r['alpha_seed'] = alpha_seed
    overrides = r.get('models', {})
    if not isinstance(overrides, dict):
        raise ValueError('Choose model names by element model role.')
    models = {**DEFAULT_MODELS, **overrides}
    if (set(models) - (set(DEFAULT_MODELS) | {'alpha_transformer'})
            or any(not isinstance(v, str) or not v.strip() for v in models.values())):
        raise ValueError('Select the transformer, text/audio/video VAE, Fluid and Alpha Gen model files.')
    prompt = r.get('prompt', DEFAULT_PROMPT)
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
        raise ValueError('Enter a concise Fluid prompt.')
    if not prompt.lstrip().startswith('ainvfxfluid'):
        prompt = 'ainvfxfluid, ' + prompt
    association = r.get('colour_association', 'black_premultiplied')
    if association not in ('black_premultiplied', 'straight'):
        raise ValueError('Choose black_premultiplied or straight colour association.')
    start = r.get('start_frame', 0)
    if isinstance(start, bool) or not isinstance(start, int) or start < 0:
        raise ValueError('The source start frame must be a non-negative integer.')
    r.update(profile=PROFILE, active_frames=n, model_frames=length, gen_fps=GEN_FPS,
             seed=seed, models=models, prompt=prompt, negative_prompt=r.get('negative_prompt', 'blurry, low quality, distorted, watermark, text, scenery'),
             steps=8, cfg=1.0, sampler='euler_ancestral', sigmas=SIGMAS, start_frame=start,
             colour_association=association, colour_space='srgb', alpha_interpretation='linear_data',
             model_revisions=copy.deepcopy(r.get('model_revisions', {})))
    if not isinstance(r['negative_prompt'], str) or not isinstance(r['model_revisions'], dict):
        raise ValueError('Invalid element prompt or model revision metadata.')
    attempts = r.get('stage_attempts', {})
    if (not isinstance(attempts, dict) or set(attempts) - {'rgb', 'alpha'}
            or any(not isinstance(v, str) or not v or len(v) > 100 for v in attempts.values())):
        raise ValueError('Explicit retry attempts must identify the RGB or alpha stage.')
    r['model_profile_hash'] = digest({'models': models, 'revisions': r['model_revisions'], 'profile': PROFILE})
    # Refuse non-JSON/NaN metadata before it can become a persisted request.
    digest(r)
    return r


def _n(cls: str, **inputs) -> dict:
    return {'class_type': cls, 'inputs': inputs}


def graph(stage: str, video: Path, recipe: dict, *, width: int, height: int,
          prefix: str = 'bang-on/element') -> tuple[dict, dict, str]:
    r = normalize_recipe(recipe)
    if stage not in ('rgb', 'alpha'):
        raise ValueError('Choose the RGB or alpha stage.')
    if (not isinstance(width, int) or not isinstance(height, int) or width < 64 or height < 64
            or width % 64 or height % 64 or max(width, height) > 1920 or min(width, height) > 1088):
        raise ValueError('Element canvas must be /64 and fit1920×1088 (portrait supported).')
    m = r['models']
    alpha = stage == 'alpha'
    # The passes have separate model chains: do not stack Fluid and Alpha Gen.
    g = {
        'control': _n('LoadVideo', file='element.mp4'),
        'control_frames': _n('GetVideoComponents', video=['control', 0]),
        'unet': _n('UNETLoader', unet_name=m.get('alpha_transformer', m['transformer']) if alpha else m['transformer'], weight_dtype='default'),
        'lora': _n('LTXICLoRALoaderModelOnly', model=['unet', 0], lora_name=m['alpha' if alpha else 'fluid'], strength_model=1.0),
        'clip': _n('CLIPLoader', clip_name=m['text_encoder'], type='ltxv', device='default'),
        'vae': _n('VAELoader', vae_name=m['video_vae']),
        'avae': _n('VAELoader', vae_name=m['audio_vae']),
        'pos': _n('CLIPTextEncode', text='' if alpha else r['prompt'], clip=['clip', 0]),
        'neg': _n('CLIPTextEncode', text='' if alpha else r['negative_prompt'], clip=['clip', 0]),
        'cond': _n('LTXVConditioning', positive=['pos', 0], negative=['neg', 0], frame_rate=float(GEN_FPS)),
        'latent': _n('EmptyLTXVLatentVideo', width=width, height=height, length=r['model_frames'], batch_size=1),
        'guide': _n('LTXAddVideoICLoRAGuide', positive=['cond', 0], negative=['cond', 1], vae=['vae', 0],
                    latent=['latent', 0], image=['control_frames', 0], frame_idx=0, strength=1.0,
                    # Explicit1 for Alpha Gen. Fluid reads factor2 from the adapter metadata.
                    latent_downscale_factor=1.0 if alpha else ['lora', 1], crop='disabled',
                    use_tiled_encode=False, tile_size=256, tile_overlap=64),
        'audio': _n('LTXVEmptyLatentAudio', frames_number=r['model_frames'], frame_rate=GEN_FPS, batch_size=1, audio_vae=['avae', 0]),
        'noise': _n('RandomNoise', noise_seed=r.get('alpha_seed', r['seed']) if alpha else r['seed']),
        'guider': _n('CFGGuider', model=['lora', 0], positive=['guide', 0], negative=['guide', 1], cfg=1.0),
        'sampler': _n('KSamplerSelect', sampler_name='euler_ancestral'),
        'sigmas': _n('ManualSigmas', sigmas=SIGMAS),
        'av': _n('LTXVConcatAVLatent', video_latent=['guide', 2], audio_latent=['audio', 0]),
        'sample': _n('SamplerCustomAdvanced', noise=['noise', 0], guider=['guider', 0], sampler=['sampler', 0], sigmas=['sigmas', 0], latent_image=['av', 0]),
        'split': _n('LTXVSeparateAVLatent', av_latent=['sample', 0]),
        'crop': _n('LTXVCropGuides', positive=['guide', 0], negative=['guide', 1], latent=['split', 0]),
        'decode': _n('VAEDecode', vae=['vae', 0], samples=['crop', 2]),
        'video': _n('CreateVideo', images=['decode', 0], fps=GEN_FPS),
        'save': _n('SaveVideo', video=['video', 0], filename_prefix=prefix, format='mp4',
                   **{'format.codec': 'h264', 'format.codec.encoding': 're-encode', 'format.codec.encoding.crf': 8.0}),
    }
    return g, {'control': ('file', Path(video))}, 'save'


def requirements(recipe: dict | None = None) -> list[tuple[str, str, dict, bool]]:
    """Only the element dependencies, suitable for the existing Connect Comfy checks."""
    return [(stage, label, graph(stage, Path('element-check.mp4'), recipe or {}, width=1024, height=576)[0], True)
            for stage, label in [('rgb', 'LTX element · Fluid'), ('alpha', 'LTX Alpha Gen')]]
