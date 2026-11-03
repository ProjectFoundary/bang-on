"""Local, immutable compositing packages. Never submits a model job.

EXR RGB is transfer-linear Rec.709 in100nit units; alpha is untransformed
coverage. Element RGB remains premultiplied/emissive to preserve fire radiance
at zero alpha. Exact native PTS and the source interpretation ship in manifest.
"""
from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import zipfile

os.environ.setdefault('OPENCV_IO_ENABLE_OPENEXR', '1')
import cv2
import numpy as np

from . import element_jobs, elements, native_colour, native_media, post_holdouts, projects, matte_treatment, element_finishing

REVISION = 'native-linear-layer-package-v1'
KINDS = {'combined', 'lighting', 'elements'}
_lock = threading.RLock()


def _options(options):
    if options is None:options={}
    if not isinstance(options,dict) or set(options)-{'include','render_id'}:
        raise ValueError('Choose combined, lighting and/or element exports.')
    include=options.get('include',['combined','lighting','elements'])
    if (not isinstance(include,list) or not include or len(include)>3
            or any(not isinstance(v,str) or v not in KINDS for v in include) or len(set(include))!=len(include)):
        raise ValueError('Choose each requested export type once.')
    rid=options.get('render_id')
    if rid is not None and (not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',rid)):
        raise ValueError('Choose a current combined result to export.')
    return {'include':sorted(include),'render_id':rid}


def _bound(root,value,*,directory=False):
    path=(root/Path(value)).resolve()
    if not path.is_relative_to(root) or path==root or not (path.is_dir() if directory else path.is_file()):
        raise ValueError('An export input is missing or outside this shot.')
    return path


def _lighting_module():
    if Path(__file__).with_name('lighting_integration.py').is_file():
        return importlib.import_module('.lighting_integration',__package__)
    return None


def _light_signature(p):
    module=_lighting_module()
    return module.selected_signature(p) if module is not None else None


def _light_frame(p,frame,profile):
    module=_lighting_module()
    return module.signed_layer(p,frame,profile) if module is not None else None


def _context(p,options):
    if not element_jobs.enabled(p) or not p.get('native_profile'):
        raise ValueError('Layer exports need a native original-plate LTX shot.')
    root=projects.pdir(p['id']).resolve();profile=element_jobs.source_profile(p)
    native_colour.colour(profile)
    if profile['frames']!=p['frames'] or len(profile['timestamps'])!=p['frames']:
        raise ValueError('The native export timeline is incomplete.')
    render=None
    if options['render_id'] is not None:
        render=next((r for r in p['renders'] if r['id']==options['render_id']),None)
        if not render or render.get('kind') not in ('effect','final') or render.get('status')!='done' or not projects.render_current(p,render):
            raise ValueError('The selected combined result is missing, unfinished or outdated.')
    review=bool(render and render.get('review_only'))
    if review:
        selected=element_jobs._review_elements(p,render['review_take_ids'])
    else:
        selected=[]
        for effect in p.get('effects',[]):
            row=element_jobs.selected_take(p,effect)
            if not row or p.get('element_approvals',{}).get(effect['id'])!=row['id']:
                raise ValueError('Approve each current element or select an explicit current review composite before exporting layers.')
            element_jobs._validate_optical_assets(p['id'],p,effect)
            selected.append((effect,copy.deepcopy(row),element_jobs._validated_take(root,row)))
    for effect,_,_ in selected:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',effect['id']):raise ValueError('Invalid element identifier.')
    if render is None:
        render=next((r for r in p.get('renders',[]) if r.get('kind') in ('final','effect') and r.get('status')=='done'
                     and not r.get('review_only') and projects.render_current(p,r)),None)
    selection=element_jobs._review_selection(selected)
    lighting_signature=_light_signature(p)
    has_analytic=any(element_jobs.effect_config(p,e)['light'] or element_jobs.effect_config(p,e)['shadow'] or element_jobs.effect_config(p,e)['ltx_glow'] for e,_,_ in selected)
    if options['include']==['lighting'] and lighting_signature is None and not has_analytic:
        raise ValueError('Generate or enable a lighting pass before exporting lighting.')
    descriptor={'revision':REVISION,'project':p['id'],'options':options,'profile':profile,
                'effect_signature':projects.effect_signature(p),'selection':selection,'review_only':review,
                'render_id':render['id'] if render and (options['render_id'] is not None or 'combined' in options['include']) else None,'lighting':lighting_signature}
    if render and 'combined' in options['include']:
        rd=root/'renders'/render['id'];files={}
        for kind in ('master','result'):
            if render.get('files',{}).get(kind):
                path=_bound(root,rd/render['files'][kind]);files[kind]=elements._sha(path)
        descriptor['combined_sha256']=files
    return {'root':root,'profile':profile,'selected':selected,'render':render,'review_only':review,
            'descriptor':descriptor,'signature':element_jobs.digest(descriptor)}


def _write_json(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n');os.replace(tmp,path)


def _write_exr(path,data):
    data=np.asarray(data,dtype=np.float32)
    if data.ndim!=3 or data.shape[2] not in (3,4) or not np.isfinite(data).all():
        raise ValueError('An EXR export layer has invalid linear channels.')
    if data.shape[2]==4 and np.any((data[...,3]<0)|(data[...,3]>1)):
        raise ValueError('EXR alpha must be linear coverage in[0,1].')
    path.parent.mkdir(parents=True,exist_ok=True)
    bgr=data[...,::-1] if data.shape[2]==3 else data[...,[2,1,0,3]]
    try:
        ok=cv2.imwrite(str(path),bgr,[cv2.IMWRITE_EXR_TYPE,cv2.IMWRITE_EXR_TYPE_FLOAT,
                                    cv2.IMWRITE_EXR_COMPRESSION,cv2.IMWRITE_EXR_COMPRESSION_ZIP])
    except cv2.error as exc:
        raise ValueError('Float EXR export is unavailable in this OpenCV build.') from exc
    if not ok:raise ValueError('Could not write the float EXR layer.')


def _manifest(ctx,p,options):
    profile=ctx['profile']
    return {'version':1,'id':ctx['signature'],'project_id':p['id'],'name':p.get('name',p['id']),
            'review_only':ctx['review_only'],'approval_note':'Unapproved review package' if ctx['review_only'] else 'Current approved element package',
            'include':options['include'],'width':profile['width'],'height':profile['height'],'frames':profile['frames'],
            'timing':{k:copy.deepcopy(profile[k]) for k in ('fps','fps_rational','time_base','timestamps','source_pts','frame_durations_ticks','duration') if k in profile},
            'source_colour':native_colour.colour(profile),'working_colour':{'primaries':'bt709','transfer':'linear','one_unit_nits':100.,'aces':False},
            'source':{'sha256':profile.get('source_sha256'),'rgb_range_policy':profile.get('rgb_range_policy'),
                      'working_copy_warning':profile.get('working_copy_warning'),'native_rgb_storage_bit_depth':profile.get('storage_bit_depth')},
            'alpha':{'association':'premultiplied/emissive RGB','channels':'RGBA','data':True,
                     'warning':'Do not unpremultiply zero-alpha emissive RGB. Raw fire radiance can remain nonzero at alpha0.'},
            'elements':[],'lighting':{'selected':ctx['descriptor']['lighting'] is not None,'status':'generated' if ctx['descriptor']['lighting'] is not None else 'not_generated',
                                     'signed':'lighting/signed/%05d.exr' if ctx['descriptor']['lighting'] is not None and 'lighting' in options['include'] else None,
                                     'association':'signed additive RGB; no alpha'},
            'reconstruction':[
                'Decode the original working plate using source_colour and convert its primaries to linear Rec.709.',
                'Add lighting/signed/NNNNN.exr once when included. Preserve negative values in float.',
                'For each element in manifest order: multiply the current image by its analytic shadow if present; add its analytic light if present; then over with E.rgb + image*(1-E.alpha).',
                'App native integer intermediates may differ by rounding from a wholly float reconstruction.',
                'Existing muzzle image edits are included only in a final combined master; separate RGBA exports cover LTX elements.'
            ],'selection':ctx['descriptor']['selection'],'files':{}}


def _build_files(p,options,ctx,directory,progress=None):
    root=ctx['root'];profile=ctx['profile'];selected=ctx['selected'];n=profile['frames'];size=(profile['width'],profile['height'])
    h,w=profile['height'],profile['width'];zeros=np.zeros((h,w,3),np.float32)
    manifest=_manifest(ctx,p,options)
    optical={e['id']:post_holdouts.NativePostHoldouts(p,e,profile,source_depth=row['plan'].get('source_depth')) for e,row,take in selected}
    needs_frames=bool(set(options['include'])&{'lighting','elements'})
    auxiliaries={}
    for effect,row,take in selected:
        cfg=element_jobs.effect_config(p,effect);auxiliaries[effect['id']]={'light':bool(cfg['light'] or cfg['ltx_glow']),'shadow':bool(cfg['shadow'])}
        manifest['elements'].append({'id':effect['id'],'preset':effect['preset'],'label':effect.get('where',effect['preset']),
                                     'take_id':row['id'],'approved':p.get('element_approvals',{}).get(effect['id'])==row['id'],
                                     'holdouts':'geometric and native optical holdouts already applied once',
                                     'tracking':'source-camera coordinates; do not apply a second track',
                                     'active_window':{k:copy.deepcopy(row['plan'].get(k)) for k in ('start_time','end_time','start_frame','active_frames','source_frames')},
                                     'rgba':f'elements/{effect["id"]}/rgba/%05d.exr' if 'elements' in options['include'] else None,
                                     'analytic_light':f'lighting/analytic/{effect["id"]}/light/%05d.exr' if (cfg['light'] or cfg['ltx_glow']) and 'lighting' in options['include'] else None,
                                     'analytic_shadow':f'lighting/analytic/{effect["id"]}/shadow/%05d.exr' if cfg['shadow'] and 'lighting' in options['include'] else None,
                                     'matte_treatment':matte_treatment.policy(cfg),'edge_finishing':element_finishing.policy(cfg)})
    if needs_frames:
        for f in range(n):
            if 'lighting' in options['include'] and ctx['descriptor']['lighting'] is not None:
                delta=_light_frame(p,f,profile)
                # A validated selected pass can cover only the active FX range.
                # Outside that window its contribution is exactly neutral.
                if delta is None:delta=zeros
                if np.asarray(delta).shape!=(h,w,3):raise ValueError('The signed lighting layer does not match the native plate.')
                _write_exr(directory/'lighting'/'signed'/f'{f:05d}.exr',delta)
            for effect,row,take in selected:
                layer=element_jobs.element_frame_layers(p,effect,row,take,profile,optical,f)
                if 'elements' in options['include']:
                    rgba=np.zeros((h,w,4),np.float32)
                    if layer is not None:rgba[...,:3]=layer['rgb'];rgba[...,3]=layer['alpha']
                    _write_exr(directory/'elements'/effect['id']/'rgba'/f'{f:05d}.exr',rgba)
                if 'lighting' in options['include']:
                    for kind in ('light','shadow'):
                        if not auxiliaries[effect['id']][kind]:continue
                        data=layer[kind] if layer is not None else None
                        if kind=='light':data=zeros if data is None else data
                        else:data=np.repeat((np.ones((h,w),np.float32) if data is None else data)[...,None],3,axis=2)
                        _write_exr(directory/'lighting'/'analytic'/effect['id']/kind/f'{f:05d}.exr',data)
            if progress:progress(f+1,n)
    if 'combined' in options['include']:
        out=directory/'combined';out.mkdir()
        render=ctx['render']
        if render and ctx['descriptor'].get('combined_sha256',{}).get('master'):
            rd=root/'renders'/render['id']
            for kind in ('master','result'):
                name=render.get('files',{}).get(kind)
                if name:shutil.copy2(_bound(root,rd/name),out/('master.mov' if kind=='master' else 'preview.mp4'))
            manifest['combined']={'render_id':render['id'],'contains_muzzle_edits':render.get('kind')=='final','copied_existing':True}
        else:
            frames=out/'native_frames';frames.mkdir()
            for f in range(n):native_media.write_native_frame(frames,f,element_jobs._composite_frame(p,selected,profile,optical,f))
            source=root/'native'/profile.get('source_file','source.mp4')
            native_media.export_native(frames,profile,out,source_audio=source if source.is_file() else None)
            shutil.rmtree(frames)
            if (out/'result.mp4').is_file():os.replace(out/'result.mp4',out/'preview.mp4')
            for helper in list(out.iterdir()):
                if helper.is_dir():shutil.rmtree(helper)
                elif helper.name not in ('master.mov','preview.mp4','poster.jpg'):helper.unlink()
            manifest['combined']={'render_id':None,'contains_muzzle_edits':False,'copied_existing':False}
    for _,row,_ in selected:element_jobs._validated_take(root,row)
    if _context(projects.load(p['id']),options)['signature']!=ctx['signature']:
        raise ValueError('The shot changed during export. Build a package from the current versions.')
    for path in sorted(directory.rglob('*')):
        if path.is_file():manifest['files'][path.relative_to(directory).as_posix()]=elements._sha(path)
    _write_json(directory/'manifest.json',manifest)
    (directory/'README.txt').write_text('Bang On layered export\n\nRead manifest.json for exact native timing, colour and layer association.\n'
        'EXRs are float32 linear Rec.709;1.0 represents100cd/m2. Alpha is data.\n'
        'Sequences retain native frame count/order. Use manifest timestamps/source_pts for native or variable-rate timing; do not assume24fps.\n'
        'Element RGB is premultiplied/emissive. Preserve RGB at zero alpha; do not automatically unpremultiply.\n'
        'Element holdouts and camera tracking are already applied once. Do not apply another track or holdout to reconstruct this composite.\n'
        'Lighting/signed is an additive signed RGB delta, not an alpha matte.\n'
        'Analytic light/shadow auxiliary files apply in the manifest element order.\n'
        'Separate elements cover LTX effects; muzzle image edits are baked only into final combined output.\n')
    return manifest


def _public(state):
    return {k:copy.deepcopy(state[k]) for k in ('id','status','progress','frames','review_only','include','created','finished','error','archive_sha256','bytes') if k in state}


def start(pid,options=None):
    options=_options(options)
    with projects._lock(pid):
        p=projects.load(pid)
        if projects.generation_active(p) or any(f.get('status') in ('queued','generating') for f in p.get('flashes',[])):
            raise ValueError('Wait for the current generation before exporting layers.')
        ctx=_context(p,options);job=ctx['root']/'layer_exports'/ctx['signature'];job.mkdir(parents=True,exist_ok=True)
        state_file=job/'state.json'
        with _lock:
            if state_file.is_file():
                state=json.loads(state_file.read_text())
                if state.get('status')=='building' and state.get('pid')==os.getpid():return _public(state)
                if state.get('status')=='done' and (job/'bundle.zip').is_file() and elements._sha(job/'bundle.zip')==state.get('archive_sha256'):return _public(state)
            state={'id':ctx['signature'],'status':'building','progress':0,'frames':p['frames'],'review_only':ctx['review_only'],
                   'include':options['include'],'created':time.time(),'pid':os.getpid()}
            _write_json(job/'request.json',{'options':options,'descriptor':ctx['descriptor']});_write_json(state_file,state)
    def run():
        try:
            def progress(done,total):
                with _lock:
                    state['progress']=done/total;_write_json(state_file,state)
            with tempfile.TemporaryDirectory(prefix='package-',dir=job) as temp:
                directory=Path(temp)/'layers';directory.mkdir()
                _build_files(p,options,ctx,directory,progress)
                archive=Path(temp)/'bundle.zip'
                with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as z:
                    for path in sorted(directory.rglob('*')):
                        if path.is_file():z.write(path,path.relative_to(directory).as_posix())
                sha=elements._sha(archive);count=archive.stat().st_size;os.replace(archive,job/'bundle.zip')
            state.update(status='done',progress=1,finished=time.time(),archive_sha256=sha,bytes=count)
        except Exception as exc:
            state.update(status='error',finished=time.time(),error=(f'{type(exc).__name__}: {exc}').replace(str(ctx['root']),'<shot>')[:500])
        with _lock:_write_json(state_file,state)
    threading.Thread(target=run,daemon=True).start()
    return _public(state)


def status(pid,jid):
    if not isinstance(jid,str) or not re.fullmatch(r'[a-f0-9]{64}',jid):raise ValueError('Export package not found.')
    root=projects.pdir(pid).resolve();path=_bound(root,Path('layer_exports')/jid/'state.json')
    state=json.loads(path.read_text())
    if state.get('status')=='building' and state.get('pid')!=os.getpid():
        state.update(status='error',error='The local export was interrupted. Build the package again; model assets are unchanged.')
    return _public(state)


def archive(pid,jid):
    state=status(pid,jid)
    if state.get('status')!='done':raise ValueError('The export package is not ready.')
    path=_bound(projects.pdir(pid).resolve(),Path('layer_exports')/jid/'bundle.zip')
    if elements._sha(path)!=state.get('archive_sha256'):raise ValueError('The export package changed. Build a new package.')
    return path
