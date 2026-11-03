import type { Project } from './api';
import { api } from './api';
import type { VolumeFXScene } from './volumeGeometry';
import { elementOnset } from './elementOptions';

const cache=new Map<string,Promise<VolumeFXScene>>();
const masks=new Map<string,ReturnType<typeof decodeMask>>();
type DecodedMask=Awaited<ReturnType<typeof decodeMask>>;
type OpticalEntry={value:DecodedMask;references:{frames:WeakRef<Map<number,Uint8Array>>;frame:number}[]};
const opticalCache=new Map<string,OpticalEntry>(),opticalPending=new Map<string,Promise<DecodedMask>>();
const opticalReloaders=new WeakMap<VolumeFXScene,()=>Promise<void>>();
const OPTICAL_CACHE_BYTES=128*1024*1024,OPTICAL_SCENE_BYTES=96*1024*1024;
let opticalBytes=0;
let decodeCanvas:HTMLCanvasElement|undefined;
export function volumeOpticalCacheStats(){return {bytes:opticalBytes,entries:opticalCache.size,limit:OPTICAL_CACHE_BYTES,pending:opticalPending.size};}
export const isElementProject=(p:Project)=>p.settings.pipeline==='ltx_elements';
export const nativeFPS=(p:Project)=>p.native_profile?.fps??24;
export const nativeAspect=(p:Project)=>(p.native_profile?.width??1920)/(p.native_profile?.height??1080);
export function sceneKey(p:Project,w:number,h:number){return JSON.stringify([p.id,p.geometry,p.effects.map(e=>[e.id,e.keys,e.element?.duration_s,e.element?.colliders]),w,h]);}

async function decodeMask(url:string){
  const response=await fetch(url);if(!response.ok)throw new Error('A collision/optical matte frame is missing.');
  const image=await createImageBitmap(await response.blob()),canvas=decodeCanvas??=document.createElement('canvas');
  canvas.width=image.width;canvas.height=image.height;const ctx=canvas.getContext('2d',{willReadFrequently:true})!;ctx.drawImage(image,0,0);image.close();
  const rgba=ctx.getImageData(0,0,canvas.width,canvas.height).data,values=new Uint8Array(canvas.width*canvas.height);
  for(let i=0;i<values.length;i++)values[i]=rgba[i*4];
  return {values,width:canvas.width,height:canvas.height};
}
function mask(url:string){if(!masks.has(url)){masks.set(url,decodeMask(url));if(masks.size>2048)masks.delete(masks.keys().next().value!);}return masks.get(url)!;}
function rememberOptical(url:string,value:DecodedMask){
  let entry=opticalCache.get(url);
  if(entry){opticalCache.delete(url);opticalCache.set(url,entry);return entry;}
  entry={value,references:[]};opticalCache.set(url,entry);opticalBytes+=value.values.byteLength;
  while(opticalBytes>OPTICAL_CACHE_BYTES){
    const key=opticalCache.keys().next().value!,old=opticalCache.get(key)!;opticalCache.delete(key);opticalBytes-=old.value.values.byteLength;
    // A count-only cache would still pin every pixel through scene frame maps.
    for(const reference of old.references){const frames=reference.frames.deref();if(frames?.get(reference.frame)===old.value.values)frames.delete(reference.frame);}
  }
  return entry;
}
async function opticalMask(url:string,frames:Map<number,Uint8Array>,frame:number){
  let entry=opticalCache.get(url);
  if(entry){opticalCache.delete(url);opticalCache.set(url,entry);}
  else{
    let pending=opticalPending.get(url);
    if(!pending){pending=decodeMask(url);opticalPending.set(url,pending);}
    let value:DecodedMask;
    try{value=await pending;}finally{if(opticalPending.get(url)===pending)opticalPending.delete(url);}
    entry=rememberOptical(url,value);
  }
  frames.set(frame,entry.value.values);
  if(!entry.references.some(r=>r.frame===frame&&r.frames.deref()===frames))entry.references.push({frames:new WeakRef(frames),frame});
  return entry.value;
}
function sizedOpticalUrl(template:string,frame:number,maxEdge:number){
  const url=new URL(template.replaceAll('{nativeFrame}',String(frame)).replaceAll('{frame}',String(frame)),location.origin);
  url.searchParams.set('max_edge',String(maxEdge));return url.pathname+url.search;
}

export async function loadVolumeScene(p:Project,w=960,h=540):Promise<VolumeFXScene>{
  const key=sceneKey(p,w,h);
  if(cache.has(key)){const value=await cache.get(key)!;await opticalReloaders.get(value)?.();return value;}
  const promise=(async()=>{
    let data:any;
    const mode=(p.geometry as {collision_mode?:string}|undefined)?.collision_mode;
    if(p.geometry?.depth||mode!==undefined||p.geometry?.mattes?.length){
      const response=await fetch(`/api/projects/${p.id}/geometry/scene`);
      if(!response.ok){const error=await response.json().catch(()=>({detail:'Could not load depth/collider geometry.'}));throw new Error(error.detail);}data=await response.json();
    }else{
      if(p.effects.some(e=>e.element?.colliders?.length))throw new Error('Add a depth pass before assigning3D colliders.');
      const track=await api.track(p.id);
      data={key,width:960,height:540,referenceFrame:0,fps:nativeFPS(p),sourceTimes:p.native_profile?.timestamps,collisionMode:'matte2d',
        depth:{width:1,height:1,values:[1],kind:'inverse',scale:4,bias:.05},
        camera:{focalPixels:900,homographies:track.camera.map(([a,b,x,y]:number[])=>[a,-b,x,b,a,y,0,0,1])},mattes:[]};
    }
    const sx=w/data.width,sy=h/data.height;
    const hs=data.camera.homographies.map((a:number[])=>[a[0],a[1]*sx/sy,a[2]*sx,a[3]*sy/sx,a[4],a[5]*sy,a[6]/sx,a[7]/sy,a[8]]);
    const needed=new Set<number>([data.referenceFrame??0]);
    const fps=nativeFPS(p),times=p.native_profile?.timestamps??Array.from({length:p.frames},(_,i)=>i/fps);
    for(const e of p.effects){
      const onset=Math.max(0,Math.min(p.frames-1,elementOnset(e)));
      const start=times[onset],duration=e.element?.duration_s??2;
      times.forEach((t,i)=>{if(t>=start-1/fps&&t<=start+duration+1/fps)needed.add(i);});
    }
    // Keep exact native frame alignment even for high-frame-rate shots. Reduce
    // only the optical preview proxy size if the whole active window would
    // exceed the scene budget. Native composition reads the original assets.
    const previews=data.mattes.filter((m:any)=>m.holdoutMaskUrl&&m.role!=='collide');
    const desired=Math.max(16,Math.min(1024,Math.ceil(Math.max(w,h))));
    const estimate=previews.reduce((sum:number,m:any)=>{
      const mw=m.holdoutWidth??p.native_profile?.width??m.width,mh=m.holdoutHeight??p.native_profile?.height??m.height;
      const scale=Math.min(1,desired/Math.max(mw,mh));return sum+(m.holdoutCoordinates==='reference'?1:needed.size)*Math.ceil(mw*scale)*Math.ceil(mh*scale);
    },0);
    const opticalEdge=Math.max(16,Math.floor(desired*Math.min(1,Math.sqrt(OPTICAL_SCENE_BYTES/Math.max(1,estimate)))));
    const mattes=[],opticalLoads:{matte:any;template:string;indices:number[];width?:number;height?:number}[]=[];
    for(const m of data.mattes){
      const frames=new Map<number,Uint8Array>(),indices=m.static?[0]:[...needed].sort((a,b)=>a-b);let mw=m.width,mh=m.height;
      const queue=[...indices];
      await Promise.all(Array.from({length:6},async()=>{while(queue.length){const f=queue.shift()!;const value=await mask(m.maskUrl.replace('{frame}',String(f)));mw=value.width;mh=value.height;frames.set(f,value.values);}}));
      const matte={...m,width:mw,height:mh,frames,coordinates:m.static?'reference':'source'};
      if(m.holdoutMaskUrl&&m.role!=='collide'){
        matte.holdoutCoordinates=m.holdoutCoordinates??'source';matte.holdoutFrames=new Map<number,Uint8Array>();
        opticalLoads.push({matte,template:m.holdoutMaskUrl,indices:matte.holdoutCoordinates==='reference'?[0]:[...needed].sort((a,b)=>a-b)});
      }
      mattes.push(matte);
    }
    const focal=data.camera.focalPixels*sx;
    const value={...data,key,width:w,height:h,depth:{...data.depth,values:new Float32Array(data.depth.values)},
      camera:{focalPixels:focal,focalY:focal*h*nativeAspect(p)/w,homographies:hs},mattes} as VolumeFXScene;
    const reload=async()=>{
      const queue=opticalLoads.flatMap(load=>load.indices.map(frame=>({load,frame})));
      await Promise.all(Array.from({length:6},async()=>{while(queue.length){
        const {load,frame}=queue.shift()!,url=sizedOpticalUrl(load.template,frame,opticalEdge),decoded=await opticalMask(url,load.matte.holdoutFrames,frame);
        if(load.width!==undefined&&(load.width!==decoded.width||load.height!==decoded.height))throw new Error('Optical matte frames have inconsistent dimensions.');
        load.width=decoded.width;load.height=decoded.height;
        load.matte.holdoutWidth=decoded.width;load.matte.holdoutHeight=decoded.height;
      }}));
    };
    opticalReloaders.set(value,reload);await reload();return value;
  })();cache.set(key,promise);if(cache.size>4)cache.delete(cache.keys().next().value!);
  try{return await promise;}catch(error){cache.delete(key);throw error;}
}
