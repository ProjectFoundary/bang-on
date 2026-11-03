"""Explicit raw-plate alpha-only matting and reviewed optical attachment."""
from __future__ import annotations

import time
import uuid

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from . import element_jobs, geometry, people_alpha, projects
from .element_api import _id, _idle, _project, _public_geometry

router=APIRouter(prefix='/api/projects/{pid}',tags=['people holdouts'])


def _ready_project(pid):
    p=_project(pid)
    if p['mode']!='plate' or not element_jobs.enabled(p):
        raise HTTPException(400,'People AlphaGen holdouts use the original-plate LTX workflow.')
    return p


def _row(pid,tid):
    _id(tid);p=_ready_project(pid)
    row=next((r for r in people_alpha.records(p) if r['id']==tid),None)
    if not row:raise HTTPException(404,'People AlphaGen version not found.')
    row['approved_mattes']=[m['id'] for m in p.get('geometry',{}).get('mattes',[]) if (m.get('holdout') or {}).get('take_id')==tid]
    return row


@router.get('/people-alpha/plan')
def plan(pid:str):
    try:return people_alpha.plan(_ready_project(pid))
    except (ValueError,TypeError,KeyError) as exc:raise HTTPException(400,str(exc)) from None


@router.get('/people-alpha/capabilities')
def capabilities(pid:str):
    return people_alpha.capabilities(_ready_project(pid))


@router.post('/people-alpha/prepare')
def prepare(pid:str,body:dict):
    p=_ready_project(pid);_idle(p)
    if set(body)-{'seed'}:raise HTTPException(400,'Choose an AlphaGen seed for the full short shot.')
    try:
        row=people_alpha.prepare(pid,body.get('seed',42))
        return _row(pid,row['id'])
    except (ValueError,TypeError,KeyError,OSError) as exc:raise HTTPException(400,str(exc)) from None


@router.post('/people-alpha/{tid}/generate')
def generate(pid:str,tid:str,body:dict):
    p=_ready_project(pid);_idle(p);_id(tid)
    if body:raise HTTPException(400,'Generate only the prepared AlphaGen request.')
    try:return people_alpha.start(pid,tid)
    except (ValueError,TypeError,KeyError,OSError) as exc:raise HTTPException(400,str(exc)) from None


@router.post('/people-alpha/{tid}/approve')
def approve(pid:str,tid:str,body:dict):
    _id(tid)
    if set(body)-{'reviewed','matte_id','effect_id'} or body.get('reviewed') is not True:
        raise HTTPException(400,'Review the AlphaGen foreground across the whole shot before using this holdout.')
    with projects._lock(pid):
        p=_ready_project(pid);_idle(p)
        try:asset=people_alpha.validated_asset(pid,tid)
        except (ValueError,TypeError,KeyError,OSError) as exc:raise HTTPException(400,str(exc)) from None
        cfg=geometry.validate(p.get('geometry',{}),p['frames'])
        mid,eid=body.get('matte_id'),body.get('effect_id')
        effect=next((e for e in p['effects'] if e['id']==eid),None) if eid is not None else None
        if eid is not None and (not effect or effect['preset'] not in element_jobs.KINDS):raise HTTPException(400,'Select a simulated element for this people holdout.')
        if mid is not None:
            if not isinstance(mid,str):raise HTTPException(400,'Choose a named matte.')
            matte=next((m for m in cfg['mattes'] if m['id']==mid),None)
            if matte is None:raise HTTPException(400,'The selected original matte no longer exists.')
        else:
            matte=next((m for m in cfg['mattes'] if m.get('source_people_alpha')==tid and m.get('holdoutBaseline')=='none'),None)
            if matte is None:
                matte={'id':'people-alpha-'+uuid.uuid4().hex[:12],'name':'People · AlphaGen','directory':asset['directory'],
                       'frames':asset['frames'],'width':asset['width'],'height':asset['height'],'static':False,
                       'kind':'soft','role':'holdout','thickness':.2,'softRadius':8,'drag':24,'colliderBlur':0,
                       'depthOffset':0,'sha256':asset['sha256'],'source_people_alpha':tid,'holdoutBaseline':'none'}
                cfg['mattes'].append(matte)
        # Enable the requested optical use without changing physical pixels,
        # drag or blur. Post both normalizes to collide for model signatures.
        if matte.get('role')=='collide':matte['role']='both'
        matte.setdefault('holdoutBaseline','original')
        matte['holdoutPost']=True
        matte['holdout']={k:asset[k] for k in ('directory','frames','width','height','sha256','source_signature','model_profile_hash')}
        matte['holdout'].update(take_id=tid,approved_at=time.strftime('%Y-%m-%d %H:%M:%S'),selection='model_foreground',
                                depth_note='Exact 2D screen holdout; 3D uses an approximate source/body depth gate.')
        p['geometry']=cfg
        if effect:
            options=element_jobs.effect_config(p,effect);options['colliders']=list(dict.fromkeys([*options['colliders'],matte['id']]))
            effect['element']=options
        projects.save(p)
    return {'matte_id':matte['id'],'geometry':_public_geometry(p),'take':_row(pid,tid)}


@router.post('/geometry/mattes/{mid}/holdout')
def original_mask(pid:str,mid:str,body:dict):
    _id(mid)
    if body!={'mode':'original'}:raise HTTPException(400,'Choose the original optical mask for this object.')
    with projects._lock(pid):
        p=_ready_project(pid);_idle(p);cfg=geometry.validate(p.get('geometry',{}),p['frames'])
        matte=next((m for m in cfg['mattes'] if m['id']==mid),None)
        if not matte:raise HTTPException(404,'Matte not found.')
        if matte.get('holdoutBaseline')=='none':raise HTTPException(400,'This AlphaGen-only object has no original tracked mask. Uncheck its element assignment to disable it.')
        matte.pop('holdout',None);matte['holdoutPost']=True
        p['geometry']=cfg;projects.save(p)
    return _public_geometry(p)


def _asset(pid,tid,kind,frame=None):
    _ready_project(pid);_id(tid)
    try:return people_alpha.asset_path(pid,tid,kind,frame)
    except (ValueError,KeyError,OSError):raise HTTPException(404,'People AlphaGen review asset is missing or changed.') from None


@router.get('/people-alpha/{tid}/source.mp4')
def source_video(pid:str,tid:str):
    return FileResponse(_asset(pid,tid,'source'),media_type='video/mp4')


@router.get('/people-alpha/{tid}/alpha.mp4')
def alpha_video(pid:str,tid:str):
    return FileResponse(_asset(pid,tid,'alpha'),media_type='video/mp4')


def _mask(pid,tid,frame,max_edge=None):
    image=cv2.imread(str(_asset(pid,tid,'frame',frame)),cv2.IMREAD_GRAYSCALE)
    if image is None:raise HTTPException(404,'People AlphaGen frame is unreadable.')
    if max_edge:
        scale=min(1.,max_edge/max(image.shape))
        if scale<1:image=cv2.resize(image,(max(1,round(image.shape[1]*scale)),max(1,round(image.shape[0]*scale))),interpolation=cv2.INTER_AREA)
    return image


@router.get('/people-alpha/{tid}/frames/{frame}.png')
def alpha_frame(pid:str,tid:str,frame:int,max_edge:int|None=Query(None,ge=16,le=2048)):
    image=_mask(pid,tid,frame,max_edge);ok,data=cv2.imencode('.png',image)
    if not ok:raise HTTPException(404,'Could not render the people matte.')
    return Response(data.tobytes(),media_type='image/png',headers={'Cache-Control':'private, max-age=31536000, immutable'})


@router.get('/people-alpha/{tid}/review/{frame}.png')
def review_frame(pid:str,tid:str,frame:int,view:str=Query('checker',pattern='^(overlay|checker|alpha)$'),max_edge:int=Query(1024,ge=16,le=2048)):
    mask=_mask(pid,tid,frame)
    if view=='alpha':image=mask
    else:
        from . import media
        p=_ready_project(pid)
        original=cv2.imread(str(media.frame_path(projects.plate_dir(p),frame)))
        if original is None or original.shape[:2]!=mask.shape:raise HTTPException(400,'The source and matte sizes differ; review the prepared source video.')
        alpha=mask.astype(np.float32)[...,None]/255
        if view=='overlay':image=original*(1-.4*alpha)+np.array([90,210,65],np.float32)*.4*alpha
        else:
            y,x=np.indices(mask.shape);checker=np.where((x//24+y//24)%2,100,55)[...,None]
            image=original*alpha+checker*(1-alpha)
        image=np.uint8(np.clip(np.floor(image+.5),0,255))
    scale=min(1.,max_edge/max(image.shape[:2]))
    if scale<1:image=cv2.resize(image,(max(1,round(image.shape[1]*scale)),max(1,round(image.shape[0]*scale))),interpolation=cv2.INTER_AREA)
    ok,data=cv2.imencode('.png',image)
    if not ok:raise HTTPException(404,'Could not render the holdout review.')
    return Response(data.tobytes(),media_type='image/png',headers={'Cache-Control':'no-store'})
