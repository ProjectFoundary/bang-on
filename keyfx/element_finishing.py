"""Cached original-body / generated-edge finishing in linear light."""
from __future__ import annotations
import cv2
import numpy as np
from . import geometry, post_holdouts

PROFILE = 'original-body-generated-fringe-v1'
DEFAULT = {'body_matte': False, 'edge_blend': 1., 'holdout_blur': 0., 'ltx_glow': 0.}
FIELDS = tuple(DEFAULT)


def policy(settings=None):
    cfg = {**DEFAULT, **{k: v for k, v in (settings or {}).items() if k in DEFAULT}}
    if not isinstance(cfg['body_matte'], bool):
        raise ValueError('Original body must be on or off.')
    for key, maximum in [('edge_blend', 1), ('holdout_blur', 12), ('ltx_glow', .5)]:
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError(f'Invalid element {key}.')
        cfg[key] = float(value)
    return {'profile': PROFILE, **cfg}


def edge_weight(guide, amount=1., protected=None):
    # Inner contours do not expose AlphaGen holes in the accepted body.
    contours, _ = cv2.findContours(np.uint8(guide > 8/255), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros(guide.shape, np.uint8)
    cv2.drawContours(filled, contours, -1, 1, cv2.FILLED)
    distance = cv2.distanceTransform(np.pad(filled, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    scale = guide.shape[0]/576
    t = np.clip((distance-12*scale)/(20*scale), 0, 1)
    weight = amount*(1-t*t*(3-2*t))
    if protected is not None: weight[protected] = 0
    return weight


def effective_alpha(model, guide, settings=None, protected=None):
    cfg = policy(settings)
    if model.shape != guide.shape: raise ValueError('Original body and AlphaGen canvas differ.')
    weight = edge_weight(guide, cfg['edge_blend'], protected)
    return guide*(1-weight)+model*weight


def soften_visibility(visibility, radius, scale=1.):
    if not radius: return visibility
    return np.clip(cv2.GaussianBlur(visibility, (0, 0), radius*scale,
                                   borderType=cv2.BORDER_REPLICATE), 0, 1)


def blend_layers(energy, model, guide, visibility, settings, protected=None):
    cfg = policy(settings)
    weight = edge_weight(guide, cfg['edge_blend'], protected)
    original_alpha = guide*visibility
    original_rgb = np.minimum(energy, guide[..., None])*visibility[..., None]
    # The captured ratio has no meaning outside simulation coverage. No
    # simulation support cap is applied to new exterior AlphaGen wisps.
    edge_visibility = np.where(guide > 0, visibility, 1)
    alpha = original_alpha*(1-weight)+model*edge_visibility*weight
    rgb = original_rgb*(1-weight[..., None])+energy*edge_visibility[..., None]*weight[..., None]
    return rgb, alpha


def glow(rgb, strength):
    """Small lens bloom from already occluded radiance; does not fill alpha."""
    if not strength: return None
    luminance = rgb @ np.array([.2126, .7152, .0722], np.float32)
    bright = rgb*np.clip((luminance-.18)/np.maximum(luminance, 1e-6), 0, 1)[..., None]
    return cv2.GaussianBlur(bright, (0, 0), max(.5, rgb.shape[1]*8/1024))*strength


class BodyMasks:
    """Select original interaction pixels, never replace them with a new cutout.

    CG slabs and supports in front of the source retain original placement;
    back walls stay behind it. Non-CG assigned optical objects are protected.
    """
    def __init__(self, p, e, take, width, height):
        from . import projects
        self.root = projects.pdir(p['id']); self.size = (width, height)
        cfg = geometry.validate(p.get('geometry', {}), p['frames'])
        assigned = set(e.get('element', {}).get('colliders', []))
        depth = take.get('provenance', {}).get('plan', {}).get('source_depth', float('inf'))
        self.mattes = []
        for matte in cfg['mattes']:
            if matte['id'] not in assigned or matte.get('role', 'both') == 'collide': continue
            box = matte.get('cameraBox')
            if box:
                slab = box['halfSize'][2] < .3
                if not slab and -box['center'][2]+matte.get('depthOffset', 0) > depth: continue
            self.mattes.append(matte)
        self.transforms = post_holdouts.camera_matrices(p, width, height) if any(m.get('static') for m in self.mattes) else None
        self.cached = None
        self.static_camera = self.transforms is None or all(np.allclose(self.transforms[0], t, atol=1e-7) for t in self.transforms)

    def pixels(self, frame):
        if self.cached is not None: return self.cached
        protected = np.zeros((self.size[1], self.size[0]), bool)
        for matte in self.mattes:
            mask = post_holdouts.read_mask(self.root, matte, frame)
            mask = cv2.resize(mask, self.size, interpolation=cv2.INTER_LINEAR)
            if matte.get('static') and self.transforms is not None and not matte.get('holdout'):
                mask = cv2.warpPerspective(mask, self.transforms[frame], self.size, flags=cv2.INTER_LINEAR)
            protected |= mask > 0
        if self.static_camera and all(m.get('static') and not m.get('holdout') for m in self.mattes): self.cached = protected
        return protected
