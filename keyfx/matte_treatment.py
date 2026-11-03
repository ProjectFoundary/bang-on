"""Finishing-only core treatment of raw FX alpha, before visibility/holdouts."""
from __future__ import annotations
import cv2
import numpy as np

PROFILE = 'raw-alpha-multiply-erode-blur-v1'
DEFAULT = {'matte_enabled': True, 'matte_multiply': 16., 'matte_erode': 12., 'matte_blur': 3.}
FIELDS = tuple(DEFAULT)


def policy(settings=None):
    cfg = {**DEFAULT, **{k:v for k,v in (settings or {}).items() if k in DEFAULT}}
    if not isinstance(cfg['matte_enabled'], bool):
        raise ValueError('Invalid matte treatment switch.')
    for key, lo, hi in [('matte_multiply',1,64),('matte_erode',0,64),('matte_blur',0,24)]:
        value=cfg[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not np.isfinite(value) or not lo<=value<=hi:
            raise ValueError(f'Invalid {key}.')
        if key=='matte_erode' and int(value)!=value:
            raise ValueError('Matte erosion must be a whole pixel count.')
        cfg[key]=float(value)
    return {'profile':PROFILE, **cfg}


def apply(alpha, settings=None):
    cfg=policy(settings)
    alpha=np.asarray(alpha)
    if (alpha.ndim!=2 or not alpha.size or not np.issubdtype(alpha.dtype,np.floating)
            or not np.isfinite(alpha).all() or alpha.min()<0 or alpha.max()>1):
        raise ValueError('Matte treatment requires finite scalar alpha in0–1.')
    original=alpha.astype(np.float32,copy=False)
    if not cfg['matte_enabled']:return original.copy()
    core=np.minimum(original*cfg['matte_multiply'],1)
    radius=int(cfg['matte_erode'])
    if radius:
        kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(radius*2+1,radius*2+1))
        core=cv2.erode(core,kernel,borderType=cv2.BORDER_CONSTANT,borderValue=0)
    if cfg['matte_blur']:
        core=cv2.GaussianBlur(core,(0,0),cfg['matte_blur'],borderType=cv2.BORDER_CONSTANT)
    core=np.clip(core,0,1)
    # Fixed original-fringe guard: the approved experiment protects6px and
    # blends to full core strength at12px, independently of erosion/blur knobs.
    contours,_=cv2.findContours(np.uint8(original>8/255),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    filled=np.zeros(original.shape,np.uint8)
    cv2.drawContours(filled,contours,-1,1,cv2.FILLED)
    distance=cv2.distanceTransform(np.pad(filled,1),cv2.DIST_L2,5)[1:-1,1:-1]
    t=np.clip((distance-6)/6,0,1);weight=t*t*(3-2*t)
    return np.maximum(original,original+weight*(core-original))
