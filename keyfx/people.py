"""Point-selected video mattes, identity guidance and source-pixel composites."""
from pathlib import Path
import hashlib,json,subprocess
import cv2
import numpy as np
from . import comfy,media
from .config import CH,CW,FPS,H,W,VIDEO_PROFILE,render_length

DEFAULTS={'selection':'auto','mode':'guide','points':[],'feather':1.0,'trim':0.0,'extra_reference':False}


def validate(value: dict, n: int) -> dict:
    if not isinstance(value,dict): raise ValueError('Invalid people selection.')
    # Matte use requires an explicit opt-in. Saved points alone may come from
    # the old mandatory workflow and must not force that workflow back on.
    selection=value.get('selection','auto')
    if selection not in ('auto','manual'):raise ValueError('Choose Automatic or Manual people selection.')
    mode=value.get('mode','guide')
    if mode not in ('guide','cutout'): raise ValueError('Choose Relight or Original cutout.')
    extra=value.get('extra_reference',False)
    if not isinstance(extra,bool):raise ValueError('Choose whether to add the extra people reference.')
    points=[]
    for p in value.get('points',[]):
        if not isinstance(p,dict): raise ValueError('Invalid matte point.')
        f=p.get('frame');person=p.get('person',0);label=p.get('label',1)
        if not isinstance(f,int) or not 0<=f<n or not isinstance(person,int) or not 0<=person<8 or label not in (0,1):
            raise ValueError('Select a valid person and frame.')
        x,y=float(p['x']),float(p['y'])
        if not np.isfinite([x,y]).all() or not 0<=x<=1 or not 0<=y<=1: raise ValueError('Click inside the source image.')
        points.append({'frame':f,'person':person,'label':label,'x':x,'y':y})
    if len(points)>120: raise ValueError('Use at most 120 matte points.')
    feather=float(value.get('feather',1));trim=float(value.get('trim',0))
    if not np.isfinite([feather,trim]).all() or not 0<=feather<=8 or not -5<=trim<=5: raise ValueError('Invalid matte edge setting.')
    return {'selection':selection,'mode':mode,'points':points,'feather':feather,'trim':trim,'extra_reference':extra}


def mask_signature(p: dict) -> str:
    cfg=validate(p.get('people',{}),p['frames'])
    return hashlib.sha256(json.dumps([p['frames'],cfg['points'],VIDEO_PROFILE],sort_keys=True).encode()).hexdigest()


def current(p: dict) -> dict | None:
    if validate(p.get('people',{}),p['frames'])['selection']=='auto':return None
    return next((r for r in p.get('renders',[]) if r['kind']=='matte' and r['status']=='done' and
                 r.get('mask_signature')==mask_signature(p)),None)


def signature(p: dict) -> str | None:
    cfg=validate(p.get('people',{}),p['frames'])
    if cfg['selection']=='auto' or not cfg['points']: return None
    parts=[mask_signature(p),cfg['mode'],cfg['feather'],cfg['trim']]
    if cfg['mode']=='guide' and cfg['extra_reference']:parts.append('extra_people_reference')
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def opening_signature(p: dict) -> str | None:
    cfg=validate(p.get('people',{}),p['frames'])
    if cfg['selection']=='auto':
        return hashlib.sha256(json.dumps(['direct-plate-v1',p['frames'],VIDEO_PROFILE]).encode()).hexdigest()
    if not cfg['points']:return None
    return hashlib.sha256(json.dumps([mask_signature(p),cfg['feather'],cfg['trim']]).encode()).hexdigest()


def graph(cfg: dict, prefix: str, size: tuple[int,int] = (CW,CH)) -> dict:
    CW, CH = size
    def node(kind,title,**kw):return {'class_type':kind,'_meta':{'title':title},'inputs':kw}
    g={'input':node('LoadVideo','Selected source clip',file='source.mp4'),
       'frames':node('GetVideoComponents','All source frames',video=['input',0]),
       'model':node('DownloadAndLoadSAM2Model','SAM 2.1 person tracker',model='sam2.1_hiera_small.safetensors',segmentor='video',device='cuda',precision='fp16')}
    groups={}
    for p in cfg['points']:groups.setdefault((p['frame'],p['person']),[]).append(p)
    if not groups:raise ValueError('Click at least one person on the first frame.')
    for person in {p['person'] for p in cfg['points']}:
        if not any(p['person']==person and p['frame']==0 and p['label']==1 for p in cfg['points']):
            raise ValueError('Start each person with an Add click on the first frame, then refine later frames.')
    prev=None
    for i,((frame,person),points) in enumerate(sorted(groups.items())):
        def coords(label):return json.dumps([{'x':min(CW-1,round(p['x']*CW)),'y':min(CH-1,round(p['y']*CH))} for p in points if p['label']==label])
        # Require a positive point on correction frames: empty coordinate arrays are not
        # supported consistently by the SAM2 node versions deployed on Cloud.
        if not any(p['label']==1 for p in points):raise ValueError('Add a keep point on each correction frame as well as remove points.')
        kw={'sam2_model':[prev,0] if prev else ['model',0],'coordinates_positive':coords(1),'frame_index':frame,'object_index':person}
        if any(p['label']==0 for p in points):kw['coordinates_negative']=coords(0)
        if prev:kw['prev_inference_state']=[prev,1]
        else:kw['image']=['frames',0]
        name=f'points{i}';g[name]=node('Sam2VideoSegmentationAddPoints',f'Person {person+1} · frame {frame}',**kw);prev=name
    g['track']=node('Sam2VideoSegmentation','Track selected people through the clip',sam2_model=[prev,0],inference_state=[prev,1],keep_model_loaded=False)
    g['maskimage']=node('MaskToImage','Matte frames',mask=['track',0])
    g['video']=node('CreateVideo','Exact source timing',images=['maskimage',0],fps=FPS)
    g['save']=node('SaveVideo','Tracked matte',video=['video',0],filename_prefix=prefix,format='mp4',**{'format.codec':'h264','format.codec.encoding':'re-encode','format.codec.encoding.crf':0.0})
    return g


def alpha(path: Path,cfg: dict) -> np.ndarray:
    m=cv2.imread(str(path),cv2.IMREAD_GRAYSCALE)
    if m is None:raise ValueError('The selected matte frame is missing. Rebuild the matte.')
    m=(m.astype(np.float32)/255)
    trim=round(abs(cfg['trim']))
    if trim:
        kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*trim+1,2*trim+1))
        m=cv2.erode(m,kernel) if cfg['trim']>0 else cv2.dilate(m,kernel)
    if cfg['feather']>0:m=cv2.GaussianBlur(m,(0,0),cfg['feather'])
    return np.clip(m,0,1)[...,None]


def build(frames_dir: Path,n: int,cfg: dict,out: Path,on_status=None,on_progress=None) -> None:
    source=media.content_video(frames_dir,n,out/'source.mp4')
    files=comfy.run_graph(graph(cfg,'keyfx/people_matte',media.read_frame(frames_dir,0).shape[1::-1]),{'input':('file',source)},'save',out/'raw',on_status=on_status,on_progress=on_progress)
    masks=media.read_video(files[0]) if files else []
    if len(masks)!=n:raise RuntimeError(f'Matte returned {len(masks)} frames; expected {n}. It was not applied.')
    expected=media.read_frame(frames_dir,0).shape[:2]
    if any(im.shape[:2]!=expected for im in masks):
        raise RuntimeError('The matte returned a lower or different resolution than the source; it was not applied.')
    md=out/'masks';md.mkdir(exist_ok=True)
    for i,im in enumerate(masks):cv2.imwrite(str(media.frame_path(md,i)),cv2.cvtColor(im,cv2.COLOR_BGR2GRAY))
    (out/'selection.json').write_text(json.dumps(cfg,indent=2))
    export_cutout(frames_dir,n,md,cfg,out/'cutout.mov')


def export_cutout(frames_dir: Path,n: int,masks: Path,cfg: dict,out: Path) -> None:
    CH,CW=media.read_frame(frames_dir,0).shape[:2]
    cmd=['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','bgra','-s',f'{CW}x{CH}','-r',str(FPS),'-i','-',
         '-an','-c:v','prores_ks','-profile:v','4','-pix_fmt','yuva444p10le',str(out)]
    w=subprocess.Popen(cmd,stdin=subprocess.PIPE)
    try:
        for i in range(n):
            im=media.read_frame(frames_dir,i).astype(np.uint8);a=np.rint(alpha(media.frame_path(masks,i),cfg)*255).astype(np.uint8)
            w.stdin.write(np.concatenate([im,a],axis=2).tobytes())
    finally:w.stdin.close()
    if w.wait():raise RuntimeError('Could not encode the transparent cutout.')


def guide_video(frames_dir: Path,n: int,masks: Path,cfg: dict,out: Path) -> Path:
    first=media.pad(media.read_frame(frames_dir,0))
    w=media._writer(out,first.shape[1],first.shape[0],lossless=True)
    for i in range(render_length(n)):
        f=min(i,n-1)
        im=media.read_frame(frames_dir,f)*alpha(media.frame_path(masks,f),cfg)
        w.stdin.write(media.pad(im).tobytes())
    w.stdin.close()
    if w.wait():raise RuntimeError('Could not prepare the selected-people reference.')
    return out


def composite(raw: Path,frames_dir: Path,n: int,masks: Path,cfg: dict,out: Path) -> Path:
    frames=media.read_video(raw,n)
    if len(frames)!=n:raise RuntimeError('The environment is incomplete; no cutout was applied.')
    if max(float(f.mean()) for f in frames[:5])<1:raise RuntimeError('Comfy returned black frames; the cutout was not applied over an invalid render.')
    height,width=media.unpad(frames[0]).shape[:2]
    w=media._writer(out,width,height,lossless=True)
    for i,bg in enumerate(frames):
        bg=media.unpad(bg)
        a=alpha(media.frame_path(masks,i),cfg);src=media.read_frame(frames_dir,i,native=True)
        if a.shape[:2]!=(height,width) or src.shape[:2]!=(height,width):
            w.stdin.close(); w.wait()
            raise ValueError('The source, matte and environment resolutions differ. Rebuild the matte and scene at native resolution.')
        w.stdin.write(np.clip(np.rint(src*a+bg*(1-a)),0,255).astype(np.uint8).tobytes())
    w.stdin.close()
    if w.wait():raise RuntimeError('Could not composite the original people.')
    return out
