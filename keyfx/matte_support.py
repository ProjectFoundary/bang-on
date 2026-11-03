"""Explicit, local/free opacity-guide attachments for existing immutable RGB takes.

The user chooses a finishing revision. Raw model outputs, their request, and the
approval pin stay intact. Every source-camera alpha sample is independently
frozen so no sparse RGB guide slot becomes an empty matte.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import shutil
import struct
import uuid

import cv2
import numpy as np

from . import alpha_core, element_jobs, elements, projects

_ID=re.compile(r'[a-f0-9]{12}')


def _selection(p,eid,tid,*,verify=False):
    e=next((e for e in p.get('effects',[]) if e['id']==eid),None)
    row=next((r for r in p.get('element_takes',[]) if r['id']==tid and r['effect_id']==eid),None)
    if not e or e.get('preset')!='explosion' or not row or row.get('status')!='done':
        raise ValueError('Choose a completed current explosion before refreshing its matte.')
    if row.get('generation_signature')!=element_jobs.generation_signature(p,e):
        raise ValueError('The explosion changed. Refresh a current version instead.')
    take=element_jobs._validated_take(projects.pdir(p['id']),row) if verify else None
    return e,row,take


def _descriptor(e,row):
    spec=row['plan']
    n=spec['active_frames']
    if isinstance(n,bool) or not isinstance(n,int) or not 1<=n<=121:
        raise ValueError('The frozen explosion window has an invalid alpha frame count.')
    if (not isinstance(spec.get('source_frames'),list) or len(spec['source_frames'])!=n
            or any(isinstance(f,bool) or not isinstance(f,int) or f<0 for f in spec['source_frames'])):
        raise ValueError('The frozen explosion source-frame map is invalid.')
    return {'effect_id':e['id'],'take_id':row['id'],'generation_signature':row['generation_signature'],
            'request_sha256':row.get('request_sha256'),'post_manifest_sha256':row.get('post_manifest_sha256'),
            'assets':{stage:row['assets'][stage]['sha256'] for stage in ('rgb','alpha')},
            'width':spec['width'],'height':spec['height'],'active_frames':n,
            'start_time':spec['start_time'],'end_time':spec.get('end_time'),
            'source_frames':spec['source_frames'],'source_depth':spec.get('source_depth')}


def plan(p,eid,tid):
    e,row,_=_selection(p,eid,tid)
    descriptor=_descriptor(e,row)
    return {**copy.deepcopy(row['plan']),'take_id':tid,'signature':row['generation_signature'],
            'plan_signature':element_jobs.digest(descriptor),'alpha_core':alpha_core.policy()}


def _decode(data,width,height):
    if (not isinstance(data,(bytes,bytearray)) or len(data)<24 or data[:8]!=b'\x89PNG\r\n\x1a\n'
            or data[12:16]!=b'IHDR' or struct.unpack('>II',data[16:24])!=(width,height)):
        raise ValueError('Opacity guides must be PNG data matching the frozen explosion canvas.')
    value=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_UNCHANGED)
    if value is None or value.dtype!=np.uint8 or value.shape[:2]!=(height,width):
        raise ValueError('Opacity-guide geometry differs from the frozen explosion plan.')
    if value.ndim==3:
        if (value.shape[2] not in (3,4) or not np.array_equal(value[...,0],value[...,1])
                or not np.array_equal(value[...,0],value[...,2])):
            raise ValueError('Opacity guides must contain grayscale data alpha, not coloured RGB.')
        value=value[...,0]
    if value.ndim!=2:raise ValueError('Opacity guides must contain one scalar alpha plane.')
    return value


def prepare(pid,eid,tid,meta,frames):
    """Attach freshly captured data alpha without any provider or RGB stage run."""
    with projects._lock(pid):
        p=projects.load(pid)
        if projects.generation_active(p):raise ValueError('Wait for the current generation before refreshing matte support.')
        e,row,_=_selection(p,eid,tid,verify=True)
        spec=plan(p,eid,tid);descriptor=_descriptor(e,row)
        if (not isinstance(meta,dict) or meta.get('signature')!=spec['signature']
                or meta.get('plan_signature')!=spec['plan_signature']):
            raise ValueError('The captured opacity guide belongs to an earlier explosion setup.')
        if set(frames)!=set(range(spec['active_frames'])):
            raise ValueError('Capture every opacity-guide frame, including sparse RGB-guide slots.')
        # Validate all client data before creating or selecting any attachment.
        decoded=[_decode(frames[k],spec['width'],spec['height']) for k in range(spec['active_frames'])]
        if not any(value.any() for value in decoded):raise ValueError('The simulated opacity guide is empty. Check this explosion in Preview first.')
        sid=uuid.uuid4().hex[:12];parent=projects.pdir(pid)/'matte_support';parent.mkdir(exist_ok=True)
        if not parent.resolve().is_relative_to(projects.pdir(pid).resolve()):raise ValueError('The opacity-guide directory is outside this project.')
        temporary=parent/('.preparing-'+sid);directory=parent/sid
        temporary.mkdir();(temporary/'frames').mkdir()
        try:
            hashes={}
            for k,value in enumerate(decoded):
                path=temporary/'frames'/f'{k:05d}.png'
                if not cv2.imwrite(str(path),value):raise ValueError('Could not preserve the opacity guide.')
                hashes[path.name]=elements._sha(path)
            manifest={'version':1,'id':sid,'source':descriptor,'plan_signature':spec['plan_signature'],
                      'policy':alpha_core.policy(),'frames':hashes}
            path=temporary/'manifest.json'
            path.write_text(json.dumps(manifest,sort_keys=True,indent=2,allow_nan=False)+'\n')
            digest=elements._sha(path)
            os.rename(temporary,directory)
        except BaseException:
            shutil.rmtree(temporary,ignore_errors=True)
            raise
        p.setdefault('matte_supports',{})[sid]={'id':sid,'effect_id':eid,'take_id':tid,
            'generation_signature':row['generation_signature'],'plan_signature':spec['plan_signature'],
            'manifest_sha256':digest,'policy':alpha_core.policy()}
        e.setdefault('element',{})['alpha_support']=sid
        projects.save(p)
        return projects.public(p)


def apply(p,e,row,take,k,model_alpha):
    """Return assisted opacity, or None when the attachment is not for this take.

    A same-take attachment is fail-closed: stale generation, changed declaration
    or mutated frame data must not silently fall back to the old matte.
    """
    sid=e.get('element',{}).get('alpha_support')
    if not sid:return None
    if not isinstance(sid,str) or not _ID.fullmatch(sid):raise ValueError('Invalid opacity-guide attachment.')
    record=p.get('matte_supports',{}).get(sid)
    if not record:raise ValueError('The selected opacity guide is missing. Refresh it again.')
    if record.get('take_id')!=row.get('id'):return None
    if record.get('effect_id')!=e.get('id'):raise ValueError('The opacity guide belongs to another effect.')
    if row.get('generation_signature')!=element_jobs.generation_signature(p,e):
        raise ValueError('The selected opacity guide uses an earlier explosion setup.')
    root=projects.pdir(p['id']).resolve();directory=root/'matte_support'/sid
    if not directory.resolve().is_relative_to(root):raise ValueError('The opacity guide is outside this project.')
    path=directory/'manifest.json'
    try:
        if not path.resolve().is_relative_to(directory.resolve()):raise ValueError('The opacity-guide manifest is outside its attachment.')
        if elements._sha(path)!=record.get('manifest_sha256'):raise ValueError('The opacity-guide manifest changed.')
        manifest=json.loads(path.read_text());descriptor=_descriptor(e,row)
        if (manifest.get('version')!=1 or manifest.get('id')!=sid or manifest.get('source')!=descriptor
                or record.get('plan_signature')!=element_jobs.digest(descriptor)
                or manifest.get('plan_signature')!=record.get('plan_signature')
                or manifest.get('policy')!=record.get('policy')):
            raise ValueError('The opacity guide no longer matches its frozen explosion.')
        if (take.get('request_sha256')!=row.get('request_sha256')
                or any(take.get('assets',{}).get(stage,{}).get('sha256')!=descriptor['assets'][stage] for stage in ('rgb','alpha'))):
            raise ValueError('The opacity guide no longer matches its immutable model output.')
        n=descriptor['active_frames']
        if isinstance(k,bool) or not isinstance(k,(int,np.integer)) or not 0<=k<n:
            raise ValueError('Opacity-guide frame is outside this explosion window.')
        expected={f'{i:05d}.png' for i in range(n)}
        if set(manifest.get('frames',{}))!=expected:raise ValueError('The opacity-guide frame manifest is incomplete.')
        filename=f'{k:05d}.png';frame=directory/'frames'/filename
        if not frame.resolve().is_relative_to(directory.resolve()):raise ValueError('The opacity-guide frame is outside its attachment.')
        if elements._sha(frame)!=manifest['frames'][filename]:raise ValueError('An opacity-guide frame changed.')
        value=cv2.imread(str(frame),cv2.IMREAD_UNCHANGED)
        if value is None or value.dtype!=np.uint8 or value.ndim!=2 or value.shape!=(descriptor['height'],descriptor['width']):
            raise ValueError('The opacity-guide frame is invalid.')
        return alpha_core.assisted_alpha(model_alpha,value,frozen_policy=manifest['policy'])
    except (OSError,KeyError,TypeError,json.JSONDecodeError) as exc:
        raise ValueError('The selected opacity guide is missing or invalid. Refresh it again.') from exc
