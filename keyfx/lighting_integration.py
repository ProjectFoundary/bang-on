"""MiniMax receiver-light difference, retained separately from fixed LTX FX.

Model photography is reference-only. A signed linear709 difference is added
to the original plate first; unchanged generated elements are then overlaid.
"""
from __future__ import annotations
import copy,json,math,os,threading,time,uuid
from pathlib import Path
os.environ.setdefault('OPENCV_IO_ENABLE_OPENEXR','1')
import cv2
import numpy as np
from . import projects,element_jobs,elements,graphs,connections,native_media,native_colour,post_holdouts,development_gate
from .config import render_length,MAX_FRAMES

PROFILE='minimax-signed-light-v2-canvas32'
PROMPT='''[video editing + reference generation] Video1 is the sole authority for the original camera, room geometry, people, identity, action, timing and materials. Video2 is the synchronized original plate with fixed high quality visual effects, defining their position, scale, motion and emitted light. Produce Video1 with the same effects and realistic interactive illumination: moving warm bounce, reflections, occluded light and soft cast shadows on nearby real surfaces and clothing. Follow the brightness and timing of Video2. Preserve the actor and all permanent scene geometry. No new effects, debris, damage, scorch, objects, cuts, extra motion or uniform orange wash. Keep the original room exposure away from the effects. This output is used only to derive a separate light/shadow difference; preserve source alignment and the effect footprint.'''

def _rows(p):
    rows=[]
    for e in p['effects']:
        tid=p.get('element_approvals',{}).get(e['id'])
        row=next((r for r in p.get('element_takes',[]) if r['id']==tid and r['effect_id']==e['id']),None)
        if not row or row.get('status')!='done' or row['generation_signature']!=element_jobs.generation_signature(p,e):
            raise ValueError('Approve a current version of every effect before the lighting pass.')
        rows.append((e,row))
    if not rows:raise ValueError('Add an effect before generating interaction lighting.')
    return rows

def input_signature(p):
    from . import matte_treatment,element_finishing
    items=[]
    for e,r in _rows(p):
        opts=element_jobs.effect_config(p,e)
        items.append([e['id'],r['id'],r['generation_signature'],r.get('request_sha256'),r.get('post_manifest_sha256'),
                      {k:v.get('sha256') for k,v in r.get('assets',{}).items()},opts['opacity'],opts['gain'],matte_treatment.policy(opts)])
        if element_finishing.policy(opts)!=element_finishing.policy():items[-1].append(element_finishing.policy(opts))
    return element_jobs.digest([PROFILE,p.get('native_profile'),p.get('source'),p.get('start_s'),items])

def selected_signature(p):
    cfg=p.get('light_integration',{})
    if not cfg.get('enabled') or not cfg.get('take_id'):return None
    row=next((r for r in p.get('lighting_takes',[]) if r['id']==cfg['take_id']),{})
    return [cfg.get('take_id'),cfg.get('gain',1),row.get('manifest_sha256')]

def records(p):
    try:sig=input_signature(p)
    except ValueError:sig=None
    return [{k:copy.deepcopy(v) for k,v in r.items() if k in ('id','status','created','error','seed','job','input_signature','active_frames','start_time','end_time','model_frames')}
            |{'current':r.get('input_signature')==sig} for r in p.get('lighting_takes',[])]

def _directory(pid,tid):return projects.pdir(pid)/'lighting'/tid

def validate_cached(p,row):
    rd=_directory(p['id'],row['id']);path=rd/'manifest.json'
    if row.get('input_signature')!=input_signature(p) or elements._sha(path)!=row.get('manifest_sha256'):raise ValueError('The cached lighting pass is stale or changed.')
    manifest=json.loads(path.read_text())
    if set(manifest.get('frames',{}))!={f'{i:05d}.exr' for i in range(row['active_frames'])}:raise ValueError('The lighting frame manifest is incomplete.')
    for name,sha in manifest['frames'].items():
        if elements._sha(rd/'delta'/name)!=sha:raise ValueError('A cached lighting frame changed.')
    return manifest

def _write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2,allow_nan=False));os.replace(tmp,path)

def _update(pid,tid,**changes):
    def update(p):
        next(r for r in p['lighting_takes'] if r['id']==tid).update(changes)
        next(r for r in p['renders'] if r['id']==tid).update(changes)
    projects.update(pid,update)

def plan(p):
    rows=_rows(p);profile=element_jobs.source_profile(p)
    start=min(r['plan']['start_time'] for _,r in rows);end=max(r['plan'].get('end_time',r['plan']['start_time']+r['plan']['active_frames']/24) for _,r in rows)
    n=max(1,math.ceil((end-start)*24-1e-9))
    if n>MAX_FRAMES:raise ValueError('The lighting reference range exceeds15seconds. Use a shorter shot or effect range.')
    scale=min(1024/profile['width'],576/profile['height'],1)
    w=max(16,round(profile['width']*scale));h=max(16,round(profile['height']*scale))
    cw=max(64,32*math.ceil(w/32));ch=max(64,32*math.ceil(h/32))
    return {'input_signature':input_signature(p),'start_time':start,'end_time':end,'active_frames':n,'model_frames':render_length(n),
            'width':w,'height':h,'canvas_width':cw,'canvas_height':ch,'source_frames':[int(np.argmin(np.abs(np.asarray(profile['timestamps'])-(start+i/24)))) for i in range(n)]}

def graph(spec,clean,comp,seed,prefix):
    uploads={};g=graphs._base(PROMPT,[],clean,seed,prefix,uploads,spec['active_frames'],spec['canvas_width'],spec['canvas_height'])
    g['effect_video']=graphs._n('LoadVideo',file='effects.mp4');g['effect_frames']=graphs._n('GetVideoComponents',video=['effect_video',0])
    g['cond']['inputs']['ref_videos.ref_video_1']=['effect_frames',0]
    g['guider']=graphs._n('BasicGuider',model=['shift',0],conditioning=['cond',0]);uploads['effect_video']=('file',comp)
    return g,uploads,'save'

def validate_catalog_canvas(spec,catalog):
    """Generic graph checks omit numeric node steps; verify the live H3 grid."""
    required=catalog.get('EmptyMiniMaxH3LatentAV',{}).get('input',{}).get('required',{})
    for name,value in [('width',spec['canvas_width']),('height',spec['canvas_height']),('length',spec['model_frames'])]:
        declaration=required.get(name)
        if not isinstance(declaration,(list,tuple)) or len(declaration)<2 or not isinstance(declaration[1],dict):continue
        limits=declaration[1];minimum=limits.get('min',0);maximum=limits.get('max',float('inf'));step=limits.get('step',1)
        if not minimum<=value<=maximum or step and (value-minimum)%step:
            raise ValueError(f'MiniMax {name} {value} does not match the selected backend numeric grid.')

def cleanup_difference(model,unlit,alpha,rgb,protected=None):
    raw=model-unlit;w=model.shape[1];s=w/1024
    fx=(alpha>.01)|(rgb.max(2)>.015)
    def kernel(radius):return cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*max(1,round(radius*s))+1,)*2)
    near=cv2.dilate(fx.astype(np.uint8),kernel(40))>0
    hot=(model@np.array([.2126,.7152,.0722],np.float32)>.55)&(model[...,0]>model[...,2]*1.1)
    exclusion=cv2.dilate((fx|(near&hot)).astype(np.uint8),kernel(7))>0
    if protected is not None:exclusion[protected]=False
    valid=(~exclusion).astype(np.float32);sigma=max(.5,32*s)
    weight=cv2.GaussianBlur(valid,(0,0),sigma)
    surrounding=cv2.GaussianBlur(raw*valid[...,None],(0,0),sigma)/np.maximum(weight[...,None],1e-6)
    blend=cv2.GaussianBlur(exclusion.astype(np.float32),(0,0),max(.25,3*s))
    if protected is not None:blend[protected]=0
    return raw*(1-blend[...,None])+surrounding*blend[...,None]

def _align(model,unlit,support):
    if np.array_equal(model,unlit):return model,{'registered':True,'identity':True}
    a=cv2.cvtColor(unlit,cv2.COLOR_RGB2GRAY);b=cv2.cvtColor(model,cv2.COLOR_RGB2GRAY)
    pts=cv2.goodFeaturesToTrack(a,400,.02,7,mask=np.uint8(~support)*255)
    if pts is None or len(pts)<12:return model,{'registered':False}
    dst,status,_=cv2.calcOpticalFlowPyrLK(a,b,pts,None)
    if dst is None:return model,{'registered':False}
    good=(status.ravel()==1)
    if good.sum()<12:return model,{'registered':False}
    transform,inliers=cv2.estimateAffinePartial2D(dst[good],pts[good],method=cv2.RANSAC,ransacReprojThreshold=2)
    if transform is None or inliers.sum()<12 or abs(transform[0,0]-1)>.035 or abs(transform[1,0])>.035 or np.linalg.norm(transform[:,2])>model.shape[1]*.035:
        return model,{'registered':False}
    return cv2.warpAffine(model,transform,(model.shape[1],model.shape[0]),borderMode=cv2.BORDER_REFLECT),{'registered':True,'matrix':transform.tolist()}

def _prepare(p,tid,spec,selected):
    root=projects.pdir(p['id']);rd=_directory(p['id'],tid);profile=element_jobs.source_profile(p)
    snapshot=copy.deepcopy(p);snapshot.pop('light_integration',None)
    for e in snapshot['effects']:e.setdefault('element',{}).update(light=0,shadow=0)
    effects={e['id']:e for e in snapshot['effects']}
    optical={e['id']:post_holdouts.NativePostHoldouts(p,e,profile,source_depth=r['plan'].get('source_depth')) for e,r,t in selected}
    left=(spec['canvas_width']-spec['width'])//2;top=(spec['canvas_height']-spec['height'])//2
    for i,f in enumerate(spec['source_frames']):
        if p.get('native_profile'):base=native_media.read_native_frame(root/'native',f)
        else:
            from . import media
            base=cv2.cvtColor(cv2.imread(str(media.frame_path(projects.plate_dir(p),f))),cv2.COLOR_BGR2RGB)
        clean=native_colour.display_frame(base,profile)
        comp=base.copy();alpha=np.zeros(base.shape[:2],np.float32);radiance=np.zeros(base.shape,np.float32);protected=np.zeros(base.shape[:2],bool)
        for e,r,t in selected:
            layer=element_jobs.element_frame_layers(snapshot,effects[e['id']],r,t,profile,optical,f)
            if layer is None:continue
            comp=native_colour.composite_native(comp,profile,native_colour.encode(layer['rgb'],{'colour':{'transfer':'srgb','primaries':'bt709'}}),layer['alpha'],premultiplied=True)
            alpha=1-(1-alpha)*(1-layer['alpha']);radiance+=layer['rgb'];protected|=layer['visibility']<.02
        for name,image in [('clean',clean),('unlit',native_colour.display_frame(comp,profile))]:
            image=cv2.resize(image,(spec['width'],spec['height']))
            image=cv2.copyMakeBorder(image,top,spec['canvas_height']-spec['height']-top,left,spec['canvas_width']-spec['width']-left,cv2.BORDER_CONSTANT)
            folder=rd/name;folder.mkdir(parents=True,exist_ok=True);cv2.imwrite(str(folder/f'{i:05d}.png'),image[...,::-1])
        folder=rd/'support';folder.mkdir(exist_ok=True)
        np.savez_compressed(folder/f'{i:05d}.npz',alpha=cv2.resize(alpha,(spec['width'],spec['height'])),rgb=cv2.resize(radiance,(spec['width'],spec['height'])),protected=cv2.resize(protected.astype(np.uint8),(spec['width'],spec['height']),interpolation=cv2.INTER_NEAREST))
    for name in ('clean','unlit'):
        # elements._encode consumes BGR24, matching OpenCV's PNG/video arrays.
        frames=[cv2.imread(str(rd/name/f'{i:05d}.png')) for i in range(spec['active_frames'])]
        elements._encode(rd/f'{name}.mp4',frames+[frames[-1]]*(spec['model_frames']-len(frames)))
    return left,top

def start(pid,*,new_version=False,executor=None,catalog=None):
    with projects._lock(pid):
        p=projects.load(pid);spec=plan(p)
        prior=next((r for r in p.get('lighting_takes',[]) if r.get('input_signature')==spec['input_signature']),None)
        if prior and not new_version:
            if prior['status']=='done':
                validate_cached(p,prior)
                p['light_integration']={'enabled':True,'take_id':prior['id'],'gain':1};projects.save(p);return prior
            if prior['status']!='error':return prior
            raise ValueError('This lighting attempt failed or is uncertain. Inspect it, then explicitly choose a new version.')
        if projects.generation_active(p):raise ValueError('Wait for the current job before starting lighting.')
        if development_gate._config() is not None:raise ValueError('The scoped element submission allowance does not authorize a MiniMax lighting job.')
        cfg=connections.current()
        if executor is None and not cfg.configured:raise ValueError('Connect Comfy before generating MiniMax lighting.')
        selected=[(e,r,element_jobs._validated_take(projects.pdir(pid),r)) for e,r in _rows(p)]
        tid=uuid.uuid4().hex[:12];seed=uuid.uuid4().int&0xffffffff if new_version else p.get('settings',{}).get('seed',42)
        rd=_directory(pid,tid);rd.mkdir(parents=True)
        row={'id':tid,'kind':'lighting','status':'preparing','created':time.strftime('%Y-%m-%d %H:%M:%S'),'files':{},'seed':seed,'pid':os.getpid(),**spec}
        p.setdefault('lighting_takes',[]).insert(0,copy.deepcopy(row));p['renders'].insert(0,row);projects.save(p)
    def run():
        try:
            left,top=_prepare(p,tid,spec,selected)
            g,uploads,output=graph(spec,rd/'clean.mp4',rd/'unlit.mp4',seed,'bang-on/lighting/'+tid)
            nodes=catalog if catalog is not None else connections.node_catalog(cfg)
            if executor is not None and catalog is None:raise ValueError('An injected lighting executor needs an explicit node catalogue.')
            status,details=connections.check_graph(nodes,g)
            if status!='ready':raise ValueError('MiniMax lighting models are not ready: '+'; '.join(details))
            validate_catalog_canvas(spec,nodes)
            frozen={'profile':PROFILE,'plan':spec,'graph':g,'backend':elements._connection_identity(cfg),'uploads':{k:{'field':v[0],'path':str(v[1]),'sha256':elements._sha(v[1])} for k,v in uploads.items()}}
            _write(rd/'request.json',frozen)
            if input_signature(projects.load(pid))!=spec['input_signature']:raise ValueError('Effects changed before lighting submission.')
            if elements._connection_identity(connections.current())!=frozen['backend']:raise ValueError('The Comfy account changed before lighting submission.')
            for _,row,_ in selected:element_jobs._validated_take(projects.pdir(pid),row)
            if any(elements._sha(Path(item['path']))!=item['sha256'] for item in frozen['uploads'].values()):raise ValueError('A lighting reference changed before submission.')
            claim=os.open(rd/'claim',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(claim)
            identity=str(uuid.uuid4());_write(rd/'submission.json',{'idempotency_key':identity,'status':'submitting'});_update(pid,tid,status='rendering')
            def progress(job,state):_update(pid,tid,job=job,status='rendering')
            paths=(executor or (lambda **kw:elements._execute_with_client(cfg,**kw)))(graph=g,uploads=uploads,out_node=output,out_dir=rd/'download',on_status=progress,idempotency_key=identity)
            if len(paths)!=1:raise ValueError('MiniMax lighting returned an unexpected output count.')
            cap=cv2.VideoCapture(str(paths[0]));reference=cv2.VideoCapture(str(rd/'unlit.mp4'));fps=cap.get(cv2.CAP_PROP_FPS);count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if abs(fps-24)>.01 or count!=spec['model_frames']:raise ValueError('Lighting output timing differs from the frozen reference.')
            folder=rd/'delta';folder.mkdir();hashes={};alignment=[]
            _update(pid,tid,status='extracting')
            for i in range(spec['active_frames']):
                ok,im=cap.read()
                if not ok or im.shape[:2]!=(spec['canvas_height'],spec['canvas_width']):raise ValueError('Lighting output dimensions differ from the reference.')
                model=im[top:top+spec['height'],left:left+spec['width'],::-1]
                ref_ok,ref=reference.read()
                if not ref_ok:raise ValueError('The frozen unlit reference could not be decoded.')
                unlit=ref[top:top+spec['height'],left:left+spec['width'],::-1]
                support=np.load(rd/'support'/f'{i:05d}.npz');model,registration=_align(model,unlit,support['alpha']>.01);alignment.append(registration)
                delta=cleanup_difference(native_colour.srgb_to_linear(model.astype(np.float32)/255),native_colour.srgb_to_linear(unlit.astype(np.float32)/255),support['alpha'],support['rgb'],support['protected']>0)
                if not np.isfinite(delta).all():raise ValueError('Lighting difference contains nonfinite values.')
                path=folder/f'{i:05d}.exr'
                if not cv2.imwrite(str(path),delta[...,::-1].astype(np.float32),[cv2.IMWRITE_EXR_TYPE,cv2.IMWRITE_EXR_TYPE_FLOAT]):raise ValueError('Could not preserve signed lighting data.')
                hashes[path.name]=elements._sha(path)
            cap.release();reference.release();manifest={'plan':spec,'frames':hashes,'source_raw_sha256':elements._sha(paths[0]),'alignment':alignment,'space':'linear_bt709_100nit','signed':True}
            _write(rd/'manifest.json',manifest);_update(pid,tid,status='done',manifest_sha256=elements._sha(rd/'manifest.json'))
            def activate(q):
                if input_signature(q)==spec['input_signature']:q['light_integration']={'enabled':True,'take_id':tid,'gain':1}
            projects.update(pid,activate)
        except Exception as exc:_update(pid,tid,status='error',error=f'{type(exc).__name__}: {exc}'[:700])
    threading.Thread(target=run,daemon=True).start();return row

def signed_layer(p,frame,profile):
    cfg=p.get('light_integration',{})
    if not cfg.get('enabled') or not cfg.get('take_id'):return None
    row=next((r for r in p.get('lighting_takes',[]) if r['id']==cfg['take_id']),None)
    if not row or row.get('status')!='done' or row['input_signature']!=input_signature(p):raise ValueError('The lighting pass uses earlier effects. Regenerate it or disable it before compositing.')
    t=profile['timestamps'][frame]
    if t<row['start_time']-1e-9 or t>=row['end_time']-1e-9:return None
    rd=_directory(p['id'],row['id']);mf=rd/'manifest.json'
    if elements._sha(mf)!=row.get('manifest_sha256'):raise ValueError('The signed lighting manifest changed.')
    manifest=json.loads(mf.read_text());k=min(row['active_frames']-1,max(0,int(math.floor((t-row['start_time'])*24+.5))))
    path=rd/'delta'/f'{k:05d}.exr'
    if elements._sha(path)!=manifest['frames'].get(path.name):raise ValueError('A signed lighting frame changed.')
    layer=cv2.imread(str(path),cv2.IMREAD_UNCHANGED)
    if layer is None or layer.ndim!=3 or layer.shape[2]!=3 or not np.isfinite(layer).all():raise ValueError('The signed lighting frame is invalid.')
    return cv2.resize(layer[...,::-1],(profile['width'],profile['height']))*float(cfg.get('gain',1))

def configure(pid,body):
    if set(body)-{'enabled','gain','take_id'}:raise ValueError('Unknown lighting setting.')
    with projects._lock(pid):
        p=projects.load(pid)
        if projects.generation_active(p):raise ValueError('Wait for the current job before changing lighting.')
        cfg={**p.get('light_integration',{}),**body}
        if not isinstance(cfg.get('enabled',False),bool):raise ValueError('Choose whether the lighting pass is enabled.')
        gain=cfg.get('gain',1)
        if isinstance(gain,bool) or not isinstance(gain,(int,float)) or not math.isfinite(gain) or not 0<=gain<=3:raise ValueError('Lighting strength must be0–3.')
        if cfg.get('enabled'):
            row=next((r for r in p.get('lighting_takes',[]) if r['id']==cfg.get('take_id')),None)
            if not row or row.get('status')!='done' or row['input_signature']!=input_signature(p):raise ValueError('Choose a completed current lighting pass.')
            validate_cached(p,row)
        p['light_integration']=cfg;projects.save(p);return projects.public(p)
