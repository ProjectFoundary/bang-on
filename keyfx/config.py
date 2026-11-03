"""Fixed settings of the H3 keyframe-directed recipe (see BUILD_PLAN.md for where each number comes from)."""
import os
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent
USER_DATA = (Path.home() / 'Library' / 'Application Support' / 'KeyFX' if sys.platform == 'darwin' else
             Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'KeyFX' if sys.platform == 'win32' else
             Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'keyfx')
DATA = Path(os.environ.get('KEYFX_DATA', ROOT / 'data' if (ROOT / 'pyproject.toml').is_file() else USER_DATA))
PRESETS = PKG / 'presets'
WEB_DIST = PKG / 'web_dist' if (PKG / 'web_dist').is_dir() else ROOT / 'web' / 'dist'

FPS = 24                    # H3 reads reference videos at 24 fps
W, H, PAD = 960, 544, 2     # canvas; 16:9 content is 960x540 with 2 px bars top and bottom
CW, CH = W, H - 2 * PAD     # content size
# Legacy canvas and logical UI coordinates above remain supported for saved takes.
# Sources, still edits, FX and final output retain the native 1080p canvas.
# Scene replacement deliberately starts smaller, then finishes at HD (see SCENE_PROFILE).
RENDER_W, RENDER_H, RENDER_PAD = 1920, 1088, 4
RENDER_CW, RENDER_CH = RENDER_W, RENDER_H - 2 * RENDER_PAD
VIDEO_PROFILE = 'keyfx-native-1080p-regular20-v1'
SCENE_PROFILE = 'keyfx-scene-960-latent2x-lms05-v1'
SCENE_DRAFT_W, SCENE_DRAFT_H = 960, 544
SCENE_LMS_STRENGTH = 0.5
SCENE_UPSCALE_MODELS = {
    'speed': 'drbaph__MiniMax-H3-Turbo-Lora-ComfyUI__minimax_h3_dmd_ref2va_8step_turbo_pruned.safetensors',
    'lms': 'minimax_h3_lms_v1.0_r64.safetensors',
    'upscaler': 'Alissonerdx__Minimax-H3-ComfyUI__h3_upscaler_lms_v0.1.safetensors',
}
SCENE_UPSCALE_PROMPT = 'Enhance this video with sharp, crisp details while preserving a natural photorealistic appearance.'
SCENE_UPSCALE_SIGMAS = '0.9035, 0.6316, 0.3158, 0.0000'
LENGTH = 124                # frames per render (17k+5 grid; trained range starts here)
MAX_FRAMES = 362            # H3's supported single-shot range, about 15 seconds


def render_length(frames: int) -> int:
    """Smallest H3 17k+5 grid containing this clip; never buy an arbitrary five-second tail."""
    if not isinstance(frames, int) or not 1 <= frames <= MAX_FRAMES:
        raise ValueError(f'Choose a clip between one frame and {MAX_FRAMES / FPS:.2f} seconds.')
    return 5 + 17 * ((max(frames, 5) - 5 + 16) // 17)

# Optional early-only schedules; the app defaults to sparse keys held throughout sampling ('exact').
# 'exact' describes the schedule, not guaranteed pixel preservation.
# Percent cut points between steps 8|9 and 12|13 of 20 under shift 12 (measured, see round 2 soft keys).
KEY_STRENGTH = {'guide': 0.377, 'firm': 0.577, 'exact': 1.0}

MODELS = {
    'unet': 'minimax_h3_ref2va_pruned_int8_convrot.safetensors',
    'clip': 'qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',
    'vae': 'minimax_h3_video_vae_fp16.safetensors',
}
SIGMAS = ('1, 0.995633187773, 0.990825688073, 0.985507246377, 0.979591836735, 0.972972972973, 0.965517241379, 0.957055214724, '
          '0.947368421053, 0.936170212766, 0.923076923077, 0.90756302521, 0.888888888889, 0.865979381443, 0.837209302326, 0.8, 0.75, '
          '0.679245283019, 0.571428571429, 0.387096774194, 0')
