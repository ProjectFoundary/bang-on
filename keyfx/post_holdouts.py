"""Native optical people mattes, separate from fluid collision and model RGB.

2D is a pixel holdout. Depth mode uses an approximate source/reference-body
depth gate; generated 2D FX has no recovered per-pixel depth.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import cv2
import numpy as np

from . import geometry


def simulation_geometry(cfg, assigned):
    """Optical-only post versions do not force a new Fluid/FX-alpha render."""
    result = copy.deepcopy(cfg); chosen = []
    for matte in result['mattes']:
        if matte['id'] not in assigned:
            continue
        if matte.get('holdoutPost'):
            if matte.get('role', 'both') == 'holdout':
                continue
            matte['role'] = 'collide'
        for key in list(matte):
            if key.startswith('holdout'):
                matte.pop(key)
        chosen.append(matte)
    result['mattes'] = chosen
    ids = {m['id'] for m in chosen}
    return result, [mid for mid in assigned if mid in ids]


def _bound(root, value):
    root = Path(root).resolve(); path = (root/value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('A people holdout frame is missing or outside this project.')
    return path


def read_mask(root, matte, frame, *, original=False):
    refined = None if original else matte.get('holdout')
    if refined:
        from . import people_alpha
        path = people_alpha.asset_path(Path(root).name, refined['take_id'], 'frame', frame)
    else:
        index = 0 if matte.get('static') else frame
        if not 0 <= index < matte.get('frames', 0):
            raise ValueError('The people holdout does not cover this native frame.')
        path = _bound(root, Path(matte['directory'])/f'{index:05d}.png')
    mask = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if mask is None or mask.dtype not in (np.uint8, np.uint16):
        raise ValueError('The people holdout frame is unreadable.')
    if mask.ndim == 3:
        mask = mask[..., 3] if mask.shape[2] == 4 else cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
    return mask.astype(np.float32)/np.iinfo(mask.dtype).max


def camera_matrices(p, width, height):
    from . import projects
    path = projects.track_file(p)
    track = json.loads(path.read_text()); raw = track.get('H', track.get('camera'))
    if not isinstance(raw, list) or len(raw) != p['frames']:
        raise ValueError('The camera track does not cover the native people holdout.')
    matrices = []
    for value in raw:
        if np.asarray(value).size == 4:
            a, b, x, y = value; matrix = np.array([[a,-b,x],[b,a,y],[0,0,1]],np.float64)
        else:
            matrix = np.asarray(value,np.float64).reshape(3,3)
        if not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-12:
            raise ValueError('Invalid people-holdout camera transform.')
        matrices.append(matrix)
    scale = np.diag([width/track.get('w',960),height/track.get('h',540),1.])
    ref = p.get('geometry',{}).get('reference_frame',0)
    return [scale@matrix@np.linalg.inv(matrices[ref])@np.linalg.inv(scale) for matrix in matrices]


def reference_depth(p, cfg, matte):
    """Stable body depth from the original collider, never the refined fringe."""
    if cfg['collision_mode'] != 'depth3d':
        return 4/1.05
    from . import projects
    data = geometry.depth_data(projects.pdir(p['id']),cfg)
    values = np.asarray(data['values'],np.float32).reshape(data['height'],data['width'])
    mask = read_mask(projects.pdir(p['id']),matte,cfg['reference_frame'],original=True)
    mask = cv2.resize(mask,(data['width'],data['height']),interpolation=cv2.INTER_LINEAR)
    depths = data['scale']/np.maximum(.025,values+data['bias']) if data['kind']=='inverse' else data['scale']*values+data['bias']
    selected = depths[mask>.7]
    return float(np.median(selected if selected.size else depths))


def _sample(values, x, y):
    h,w = values.shape; x=float(np.clip(x,0,w-1));y=float(np.clip(y,0,h-1))
    a,b=int(x),int(y);c,d=min(w-1,a+1),min(h-1,b+1);u,v=x-a,y-b
    return float((values[b,a]*(1-u)+values[b,c]*u)*(1-v)+(values[d,a]*(1-u)+values[d,c]*u)*v)


class NativePostHoldouts:
    def __init__(self,p,e,profile,source_depth=None):
        from . import projects, element_jobs
        self.root=projects.pdir(p['id']);self.cfg=geometry.validate(p.get('geometry',{}),p['frames'])
        self.width,self.height=profile['width'],profile['height'];self.mattes=[];self.transforms=None
        assigned=element_jobs.effect_config(p,e)['colliders']
        self.mattes=[m for m in self.cfg['mattes'] if m['id'] in assigned and m.get('holdoutPost') and m.get('role','both')!='collide']
        if not self.mattes:return
        if self.cfg['collision_mode']=='depth3d' or any(m.get('static') and not m.get('holdout') for m in self.mattes):
            self.transforms=camera_matrices(p,self.width,self.height)
        if self.cfg['collision_mode']=='matte2d':self.depths={};self.source_depth=4/1.05;return
        self.depths={m['id']:reference_depth(p,self.cfg,m)+(m.get('depthOffset') or 0) for m in self.mattes}
        if source_depth is not None:
            if not np.isfinite(source_depth) or source_depth<=0:raise ValueError('Invalid captured effect depth for the people holdout.')
            self.source_depth=float(source_depth);return
        data=geometry.depth_data(self.root,self.cfg);values=np.asarray(data['values'],np.float32).reshape(data['height'],data['width'])
        spec=element_jobs.plan(p,e['id']);onset=spec['start_frame']
        active=[k for k in e['keys'] if k.get('enabled') is not False]
        phase='onset' if e['preset']=='explosion' else 'ignite' if e['preset']=='fire' else 'start' if e['preset'].startswith('smoke_') else 'impact'
        key=next((k for k in active if k['phase']==phase),min(active,key=lambda k:k['frame']))
        fallback=(e.get('anchors') or [{'x':.5,'y':.5}])[0]
        point=np.array([key.get('x',fallback['x'])*self.width,key.get('y',fallback['y'])*self.height,1.])
        point=np.linalg.inv(self.transforms[onset])@point;point/=point[2]
        value=_sample(values,(point[0]+.5)*data['width']/self.width-.5,(point[1]+.5)*data['height']/self.height-.5)
        surface=data['scale']/max(.025,value+data['bias']) if data['kind']=='inverse' else data['scale']*value+data['bias']
        focal=self.cfg['focal_pixels']*self.width/960
        clearance=max(.08,self.height*surface/focal*np.sqrt(max(.1,e.get('size',1)))*1.65/24*1.25)
        self.source_depth=max(.1,surface-clearance-element_jobs.effect_config(p,e)['depth_offset'])

    def visibility(self,frame):
        if not self.mattes:return None
        visible=np.ones((self.height,self.width),np.float32)
        for matte in self.mattes:
            if self.cfg['collision_mode']=='depth3d' and self.depths[matte['id']]>=self.source_depth:continue
            mask=read_mask(self.root,matte,frame)
            mask=cv2.resize(mask,(self.width,self.height),interpolation=cv2.INTER_LINEAR)
            if not matte.get('holdout') and matte.get('static'):
                mask=cv2.warpPerspective(mask,self.transforms[frame],(self.width,self.height),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT,borderValue=0)
            visible*=1-np.clip(mask,0,1)
        return visible
