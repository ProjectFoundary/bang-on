"""Step 1: build a location using a plate frame as the viewpoint reference.

A frame of the green-screen plate goes through Qwen-Image-Edit-2511 (Lightning, 4 steps, GPU only): the green screen and studio
gear become the described location and camera perspective is guided by the plate. People stay by default. Precise props
are a separate FLUX 3 edit of the approved result, implemented in setdressing.py.
"""
from __future__ import annotations

from pathlib import Path
import json

import cv2
import numpy as np

from . import comfy, polish, image_models, people, room_refs
from .config import CW, CH

LIGHTING = {
    'match': '',
    'overcast': 'soft, even overcast daylight coming through the windows',
    'sun': 'harsh direct sunlight streaming through the windows, with strong contrast and hard shadows',
    'dusk': 'dim late-evening light with deep shadows and a few warm practical lamps',
    'night': 'night-time, lit only by a few practical work lights, mostly dark with pools of light',
}


def where(box: dict) -> str:
    """Placement words for a box drawn on the frame (normalised x0,y0,x1,y1)."""
    x0, y0, x1, y1 = (box[k] for k in ('x0', 'y0', 'x1', 'y1'))
    cx, cy, h = (x0 + x1) / 2, (y0 + y1) / 2, y1 - y0
    horiz = 'on the far left' if cx < 0.2 else 'on the left' if cx < 0.4 else 'in the centre' if cx < 0.6 else 'on the right' if cx < 0.8 else 'on the far right'
    depth = 'in the near foreground, close to camera' if y1 > 0.85 and h > 0.3 else 'in the foreground' if y1 > 0.7 else \
        'in the middle distance' if y1 > 0.5 else 'in the background'
    size = 'large in frame' if h > 0.45 else 'medium-sized in frame' if h > 0.2 else 'small in frame'
    return (f'{horiz} of the frame, {depth}, {size}, filling the area from {x0:.0%} to {x1:.0%} across and '
            f'{y0:.0%} to {y1:.0%} down the picture')


def describe_dressing(dressing: list[dict]) -> list[str]:
    """Each object as text; a boxed object also gets its placement in the frame."""
    out = []
    for d in dressing:
        t = d.get('text', '').strip().rstrip('.')
        if t:
            out.append(f"{t}, {where(d['box'])}" if d.get('box') else t)
    return out


def edit_prompt(location: str, dressing: list[dict], lighting: str, keep_people: bool = True, room_references: int = 0) -> str:
    loc = location.strip().rstrip('.') or ('the room shown in the supplied reference photographs' if room_references else 'a realistic location that suits the action')
    parts = ['Edit the supplied photograph; the original plate is the authority for camera and foreground appearance.']
    if keep_people:
        parts.append('Preserve each existing person and held object: the original face, hair or headwear, wardrobe, '
                     'body proportions, hands, exact pose, silhouette and screen position. Preserve the captured focus, '
                     'motion blur and grain; soft or obscured details stay soft rather than being reconstructed.')
    parts.append(f'Replace the green screen, the studio floor and all studio equipment, stands, cases and cloth with {loc}.')
    boxed = [d for d in dressing if d.get('box') and d.get('text', '').strip()]
    loose = [d for d in dressing if not d.get('box') and d.get('text', '').strip()]
    if boxed:
        parts.append('Include: ' + '; '.join(describe_dressing(boxed)) + '.')
    if loose:
        parts.append('Also place: ' + '; '.join(d['text'].strip().rstrip('.') for d in loose) + '.')
    light = LIGHTING.get(lighting, lighting) if lighting else ''
    if light:
        parts.append(f'Lighting: {light}.')
    parts.append('Keep the exact camera position, camera height, lens and perspective of this photograph: the same horizon line, '
                 'the same floor plane and vanishing points, so the new location is shot from exactly this viewpoint.')
    parts.append('Change only the people\'s illumination and colour spill to match the location; their appearance and geometry stay fixed. '
                 'Add natural contact shadows at their existing floor contacts.' if keep_people else
                 'Remove the people and anything they hold, and fill in the location behind them, leaving an empty set.')
    parts.append('Retain the source photographic texture and framing. Return one photograph without added text.')
    return ' '.join(parts)


def reference_prompt(text: str, count: int) -> str:
    if not count:return text
    roles=['Picture 1 is the original plate and the sole authority for the output camera position, height, framing, '
           'lens perspective, floor plane, horizon and vanishing points. It is also the sole authority for every '
           'foreground person\'s identity, hair or headwear, wardrobe, pose, silhouette, held objects, focus and motion blur.']
    for i in range(count):
        roles.append(f'Picture {i+2} is room reference {i+1}: use its permanent architecture, room layout, '
                     'furniture, materials and surface detail as evidence of the environment.')
    roles.append('Treat the room photographs as views of the same place and reconstruct that place from the viewpoint '
                 'of Picture 1. Their camera viewpoints, framing, people and temporary objects are not output instructions. '
                 'The description and requested lighting control intentional changes. '
                 'Return one coherent photographic view from the plate camera.\n\n')
    return '\n'.join(roles)+text


def reference_graph(model: str, text: str, seed: int, source: Path, references: list[Path]) -> tuple[dict,dict,str]:
    """Physical input order is always plate first, then the room photos."""
    from . import flashes
    room_refs.check_limit(model,len(references))
    g=image_models.graph(model,text,seed)
    source_node=flashes.BIND['clean'] if model=='nano' else 'img'
    output=flashes.BIND['output'] if model=='nano' else 'save'
    uploads={source_node:('image',source)}
    if model=='nano':
        g['room_batch']={'class_type':'BatchImagesNode','inputs':{'images.image0':[source_node,0]}}
        g[flashes.BIND['model']]['inputs']['images']=['room_batch',0]
    for i,path in enumerate(references,1):
        node=f'room_{i}'
        g[node]={'class_type':'LoadImage','inputs':{'image':path.name},'_meta':{'title':f'Room reference {i}'}}
        uploads[node]=('image',path)
        if model=='nano':g['room_batch']['inputs'][f'images.image{i}']=[node,0]
        elif model=='qwen':
            for encoder in ['pos','neg']:g[encoder]['inputs'][f'image{i+1}']=[node,0]
        else:g['ref']['inputs'][f'images.image{i}']=[node,0]
    g[output]['inputs']['filename_prefix']='keyfx/location_references'
    for name,spec in g.items():spec.setdefault('_meta',{'title':f'Location references: {name}'})
    return g,uploads,output


def prepare_reference(path: Path, model: str, dest: Path, canvas_size: tuple[int,int] | None = None) -> Path:
    im=cv2.imread(str(path))
    if im is None:raise ValueError('A room photo cannot be read. Upload it again.')
    h,w=im.shape[:2]
    # Qwen/FLUX have independent image inputs. Nano batches require identical
    # canvases, so fit the entire photo here; its batch node would otherwise
    # silently resize and centre-crop mismatched dimensions.
    if model=='nano':
        cw,ch=canvas_size or (polish.EDIT_W,polish.EDIT_H)
        scale=min(1.,cw/w,ch/h)
        if scale<1:
            im=cv2.resize(im,(max(1,round(w*scale)),max(1,round(h*scale))),interpolation=cv2.INTER_AREA)
            h,w=im.shape[:2]
        canvas=np.full((ch,cw,3),24,np.uint8)
        y,x=(ch-h)//2,(cw-w)//2
        canvas[y:y+h,x:x+w]=im;im=canvas
    elif model=='flux3':
        im=cv2.copyMakeBorder(im,0,(-h)%32,0,(-w)%32,cv2.BORDER_REPLICATE)
    cv2.imwrite(str(dest),im)
    return dest


def build(frame: np.ndarray, text: str, seed: int, out_dir: Path, model: str = 'qwen', references: list[Path] | None = None) -> Path:
    """Edit one plate frame at its native resolution into the location. GPU only. Returns the saved location image."""
    out_dir.mkdir(parents=True, exist_ok=True)
    references=references or []
    room_refs.check_limit(model,len(references))
    text=reference_prompt(text,len(references))
    inp = out_dir / 'plate_frame.png'
    work = image_models.prepare(frame, model)
    cv2.imwrite(str(inp), work)
    (out_dir / 'prompt.txt').write_text(text)
    if references:
        refs=[prepare_reference(path,model,out_dir/f'room_{i:02d}.png',work.shape[1::-1]) for i,path in enumerate(references,1)]
        g,uploads,output=reference_graph(model,text,seed,inp,refs)
        if model=='flux3':
            mask=out_dir/'full_mask.png';cv2.imwrite(str(mask),np.full(work.shape[:2],255,np.uint8))
            uploads['mask']=('image',mask)
        (out_dir/'graph.json').write_text(json.dumps(g,indent=2))
        (out_dir/'reference_map.json').write_text(json.dumps({'camera':'Picture 1: plate_frame.png',
            'plate_canvas':list(work.shape[1::-1]),
            'room_photos':{f'Picture {i+1}':path.name for i,path in enumerate(refs,1)},
            'reference_input_policy':'Nano: complete photos fitted to the plate canvas without cropping. Qwen/FLUX: original dimensions plus grid padding. Room-photo masters remain unchanged.'},indent=2))
        if model=='nano':
            extra={node:path for node,(_,path) in uploads.items() if node.startswith('room_')}
            files=comfy.run_partner_graph(g,'11',inp,output,out_dir/'raw','Bang On room-reference location',additional_images=extra)
        else:files=comfy.run_graph(g,uploads,output,out_dir/'raw')
    else:
        files = comfy.run_graph(polish.graph(text, seed, 'keyfx/location'), {'img': ('image', inp)}, 'save', out_dir / 'raw') if model == 'qwen' else [image_models.edit(inp, text, seed, out_dir / 'raw', model)]
    if not files:
        raise RuntimeError('location job returned no image')
    im = cv2.imread(str(files[0]))
    if im is None: raise RuntimeError('The location image could not be read.')
    im = image_models.restore(im, work, frame.shape[1::-1])
    out = out_dir / 'location.png'
    cv2.imwrite(str(out), im)
    return out


def build_opening(frame: np.ndarray, mask: Path, cfg: dict, description: str, lighting: str,
                  seed: int, out_dir: Path, model: str = 'qwen', on_stage=None, references: list[Path] | None = None) -> Path:
    """Empty set -> selected source people -> lighting edit. No isolated video is needed.

    The original full plate supplies perspective to the empty-set pass. The
    matte supplies the only people placed into the lighting pass, at source
    coordinates. Only smooth colour/illumination transfers back onto source
    pixels; generated face, wardrobe and geometry are never composited in.
    """
    out_dir.mkdir(parents=True,exist_ok=True)
    references=references or []
    room_refs.check_limit(model,len(references))
    CH,CW=frame.shape[:2]
    a=people.alpha(mask,cfg)
    if a.shape != (*frame.shape[:2],1) or not np.any(a>.5):
        raise ValueError('The people matte is empty on this frame. Refine the selection before building the opening.')
    if on_stage:on_stage('building_location')
    base_prompt=edit_prompt(description,[],lighting,keep_people=False,room_references=len(references))
    base_path=build(frame,base_prompt,seed,out_dir/'empty_location',model=model,**({'references':references} if references else {}))
    base=cv2.resize(cv2.imread(str(base_path)),(CW,CH),interpolation=cv2.INTER_AREA)
    composite=np.clip(np.rint(frame*a+base*(1-a)),0,255).astype(np.uint8)
    cv2.imwrite(str(out_dir/'selected_people.png'),composite)
    cv2.imwrite(str(out_dir/'people_mask.png'),np.rint(a[...,0]*255).astype(np.uint8))
    integration_prompt=(
        'Edit this photograph to integrate only its existing foreground people into the existing location. '
        'Change their illumination and edge contamination to match the visible environment and requested lighting. '
        'Remove green-screen spill from skin, hair and clothing. Rebuild directional highlights, soft shadows, '
        'reflected colour and exposure across their actual forms. Add natural contact and cast shadows on the floor. '
        'The original captured focus, motion blur, skin and fabric texture remain unchanged. '
        'Treat soft or obscured features as unknown and preserve their original softness. '
        'Keep every person\'s facial identity, expression, hair, exact pose, silhouette, body proportions, hands, feet, '
        'clothing, held props and screen position unchanged. Keep the camera, framing, perspective and all existing '
        'architecture and set dressing unchanged. Include only the people already visible in this image. '
        'Return the same single photographic frame without text, studio equipment or effects.'
    )
    light=LIGHTING.get(lighting,lighting) if lighting else ''
    if light:integration_prompt+=f' Match this lighting setup: {light}.'
    if on_stage:on_stage('integrating_people')
    edited_path=build(composite,integration_prompt,seed,out_dir/'people_lighting',model=model)
    edited=cv2.resize(cv2.imread(str(edited_path)),(CW,CH),interpolation=cv2.INTER_AREA)
    final=preserve_people_lighting(frame,base,edited,a)
    out=out_dir/'location.png';cv2.imwrite(str(out),final)
    (out_dir/'identity_policy.json').write_text(json.dumps({'policy':'source-detail-lighting-v1',
        'source_geometry':True,'generated_person_pixels_used':False,
        'transfer':'Smooth bounded colour gains on original source pixels; soft darkening at floor contacts.'},indent=2))
    (out_dir/'prompt.txt').write_text(reference_prompt(base_prompt,len(references))+'\n\nSELECTED PEOPLE LIGHTING PASS:\n'+integration_prompt)
    return out


def preserve_people_lighting(frame: np.ndarray, base: np.ndarray, edited: np.ndarray, a: np.ndarray) -> np.ndarray:
    """Transfer broad lighting without accepting AI-generated performer detail.

    All performer texture and geometry come from the original source. Masked
    smoothing prevents the new background from contaminating the lighting
    estimate, and gains are bounded to avoid extreme changes on dark faces.
    """
    if frame.shape!=base.shape or frame.shape!=edited.shape or a.shape!=(*frame.shape[:2],1):
        raise ValueError('The source, lighting edit and matte must have matching native dimensions.')
    src=frame.astype(np.float32).copy();target=edited.astype(np.float32)
    # Suppress strong green-screen spill without neutralising every green item.
    ceiling=np.maximum(src[...,0],src[...,2])
    src[...,1]-=np.maximum(src[...,1]-ceiling*1.2,0)*.8
    sigma=max(12,min(frame.shape[:2])*.025)
    weight=cv2.GaussianBlur(a[...,0],(0,0),sigma)[...,None]
    def smooth(im):
        return cv2.GaussianBlur(im*a,(0,0),sigma)/np.maximum(weight,.001)
    gain=np.clip(smooth(target)/np.maximum(smooth(src),12),.35,2.5)
    gain=cv2.GaussianBlur(gain,(0,0),sigma)
    relit=np.clip(src*gain,0,255)
    # Floor shadows use smooth darkening only: no generated boots, bodies or
    # new scene detail can leak outside the original source silhouette.
    region=np.zeros(frame.shape[:2],np.float32)
    _,_,stats,_=cv2.connectedComponentsWithStats((a[...,0]>.5).astype(np.uint8))
    for x,y,w,h,area in stats[1:]:
        if area<25:continue
        cv2.ellipse(region,(int(x+w/2),int(y+h-2)),(max(24,int(w)),max(16,round(frame.shape[0]*.03))),0,0,360,1,-1)
    region=cv2.GaussianBlur(region,(0,0),2)[...,None]
    target_luma=cv2.GaussianBlur(cv2.cvtColor(edited.astype(np.uint8),cv2.COLOR_BGR2GRAY).astype(np.float32),(0,0),sigma)
    base_luma=cv2.GaussianBlur(cv2.cvtColor(base.astype(np.uint8),cv2.COLOR_BGR2GRAY).astype(np.float32),(0,0),sigma)
    shadow=np.clip(target_luma/np.maximum(base_luma,12),.45,1)[...,None]
    background=base.astype(np.float32)*(1-region+region*shadow)
    return np.clip(np.rint(relit*a+background*(1-a)),0,255).astype(np.uint8)
