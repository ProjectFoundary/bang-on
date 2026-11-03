"""Conservative local explosion opacity support from simulated data alpha.

This is a lower bound, never an alpha cap or a luminance key. AlphaGen's
original decoded matte stays intact; holdouts and element opacity apply later.
"""
from __future__ import annotations

import cv2
import numpy as np


V1_PROFILE = 'eroded-guide-alpha-core-v1'
V2_PROFILE = 'feathered-guide-alpha-core-v2'
PROFILE = V2_PROFILE
EROSION_AT_540 = 6.0
BLUR_SIGMA_AT_540 = 3.0
ENVELOPE_THRESHOLD = 8 / 255


def _declared_policy(profile: str) -> dict:
    if profile not in (V1_PROFILE, V2_PROFILE):
        raise ValueError('Unsupported frozen explosion alpha-core policy; capture a supported new version.')
    result = {'profile': profile, 'erosion_pixels_at_540': EROSION_AT_540,
            'blur_sigma_at_540': BLUR_SIGMA_AT_540 if profile == V1_PROFILE else 6.0,
            'source': 'simulated_data_alpha', 'combination': 'maximum',
            'support': 'bounded_by_original_guide',
            'generated_envelope': 'small_close_then_fill_external_contours',
            'envelope_threshold': ENVELOPE_THRESHOLD,
            'envelope_close_pixels_at_540': EROSION_AT_540}
    if profile == V2_PROFILE:
        result.update(edge_breakup='guide_centroid_coherent_attenuation',
                      edge_breakup_strength=.45, edge_breakup_period_at_540=22.0,
                      edge_breakup_seed=1983, frame_border='replicate_visible_support')
    return result


def policy() -> dict:
    """Default for new captures; existing frozen v1 declarations still replay."""
    return _declared_policy(PROFILE)


def validate_policy(frozen_policy: dict | None) -> None:
    if frozen_policy is not None and (not isinstance(frozen_policy, dict)
            or frozen_policy != _declared_policy(frozen_policy.get('profile'))):
        raise ValueError('Unsupported frozen explosion alpha-core policy; capture a supported new version.')


def _configuration(frozen_policy: dict | None) -> dict:
    validate_policy(frozen_policy)
    return policy() if frozen_policy is None else frozen_policy


def _coherent_noise(guide: np.ndarray, period: float, seed: int) -> np.ndarray:
    """Deterministic smooth material coordinates, with no per-frame random seed.

    Anchoring to guide mass carries the breakup with the element/camera instead
    of flickering independent screen noise. It is an artistic opacity detail,
    not recovered smoke motion or evidence of unknown alpha.
    """
    h, w = guide.shape
    weights = guide.astype(np.float64); mass = float(weights.sum())
    if mass <= 0:
        return np.zeros(guide.shape, np.float32)
    cx = float(np.dot(weights.sum(axis=0), np.arange(w))/mass)
    cy = float(np.dot(weights.sum(axis=1), np.arange(h))/mass)
    ys, xs = np.nonzero(guide)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max())+1, int(ys.min()), int(ys.max())+1
    scale = 540.0/h
    x, y = np.meshgrid((np.arange(x0,x1,dtype=np.float32)-cx)*scale/period,
                       (np.arange(y0,y1,dtype=np.float32)-cy)*scale/period)
    def hashed(ix, iy, salt):
        with np.errstate(over='ignore'):
            value = ix.astype(np.uint32)*np.uint32(0x9e3779b1) ^ iy.astype(np.uint32)*np.uint32(0x85ebca77) ^ np.uint32(salt)
            value ^= value >> np.uint32(16); value *= np.uint32(0x7feb352d)
            value ^= value >> np.uint32(15); value *= np.uint32(0x846ca68b)
            value ^= value >> np.uint32(16)
        return (value >> np.uint32(8)).astype(np.float32)/16777215.0
    result = np.zeros(x.shape, np.float32)
    for frequency, amplitude in ((1, .6), (2, .3), (4, .1)):
        u, v = x*frequency, y*frequency
        ix, iy = np.floor(u).astype(np.int32), np.floor(v).astype(np.int32)
        u, v = u-ix, v-iy; u, v = u*u*(3-2*u), v*v*(3-2*v)
        salt = seed+frequency*101
        a = hashed(ix, iy, salt)*(1-u)+hashed(ix+1, iy, salt)*u
        b = hashed(ix, iy+1, salt)*(1-u)+hashed(ix+1, iy+1, salt)*u
        result += amplitude*(a*(1-v)+b*v)
    full = np.zeros(guide.shape,np.float32);full[y0:y1,x0:x1]=result
    return full


def core_alpha(guide: np.ndarray, *, frozen_policy: dict | None = None) -> np.ndarray:
    """Erode and soften true simulation alpha, returning float opacity0–1.

    Softening is clamped to the original guide, so blur cannot create opacity
    beyond that guide or fill genuine interior holes. Sparse RGB conditioning
    must not remove these per-frame alpha inputs.
    """
    cfg = _configuration(frozen_policy)
    guide = np.asarray(guide)
    if guide.ndim != 2 or not guide.size or guide.dtype != np.uint8:
        raise ValueError('Explosion core support requires a non-empty8-bit data-alpha plane.')
    scale = guide.shape[0] / 540.0
    radius = max(1, round(cfg['erosion_pixels_at_540'] * scale))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*radius+1, 2*radius+1))
    border = cv2.BORDER_CONSTANT if cfg['profile']==V1_PROFILE else cv2.BORDER_REPLICATE
    eroded = cv2.erode(guide, kernel, borderType=border, borderValue=0)
    # Float processing keeps faint values smooth; no gamma or opaque threshold.
    softened = cv2.GaussianBlur(eroded.astype(np.float32) / 255.0, (0, 0),
                               cfg['blur_sigma_at_540'] * scale, borderType=border)
    core = np.minimum(softened, guide.astype(np.float32) / 255.0)
    if cfg['profile'] == V2_PROFILE:
        distance = cv2.distanceTransform(np.uint8(guide > 8), cv2.DIST_L2, 5)
        sigma = cfg['blur_sigma_at_540']*scale
        edge_start, edge_width = radius+1.5*sigma, max(2*sigma, 1e-6)
        # An all-opaque mask has no zero-distance seed: OpenCV returns FLT_MAX.
        # Saturate the distance before division, retaining the same smoothstep.
        distance = np.minimum(distance, edge_start+edge_width)
        u = np.clip((distance-edge_start)/edge_width, 0, 1)
        band = 1-u*u*(3-2*u)
        noise = _coherent_noise(guide, cfg['edge_breakup_period_at_540'], cfg['edge_breakup_seed'])
        core *= 1-cfg['edge_breakup_strength']*band*noise
    return core


def assisted_alpha(model: np.ndarray, guide: np.ndarray, *, frozen_policy: dict | None = None) -> np.ndarray:
    """Raise weak/enclosed model cores; retain generated outer wisps.

    Model alpha supplies the outer envelope, not a luminance key. A shifted or
    absent model element must not reveal a guide-shaped black patch. This can
    recover an enclosed hole; a completely missing smoke lobe remains unknown.
    """
    cfg = _configuration(frozen_policy)
    model = np.asarray(model)
    if (model.ndim != 2 or model.shape != np.asarray(guide).shape
            or not np.issubdtype(model.dtype, np.floating)
            or not np.isfinite(model).all() or np.any(model < 0) or np.any(model > 1)):
        raise ValueError('Explosion model alpha must be finite0–1 float opacity matching its guide.')
    core = core_alpha(guide, frozen_policy=cfg)
    scale = guide.shape[0] / 540.0
    radius = max(1, round(cfg['envelope_close_pixels_at_540'] * scale))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*radius+1, 2*radius+1))
    border = cv2.BORDER_CONSTANT if cfg['profile']==V1_PROFILE else cv2.BORDER_REPLICATE
    envelope = cv2.morphologyEx(np.uint8(model > cfg['envelope_threshold'])*255, cv2.MORPH_CLOSE,
                                kernel, borderType=border, borderValue=0)
    contours, _ = cv2.findContours(envelope, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(envelope)
    cv2.drawContours(filled, contours, -1, 255, thickness=cv2.FILLED)
    softened = cv2.GaussianBlur(filled.astype(np.float32)/255, (0, 0),
                               cfg['blur_sigma_at_540']*scale, borderType=border)
    gate = np.minimum(softened, filled.astype(np.float32)/255)
    return np.maximum(model.astype(np.float32, copy=False), core*gate)
