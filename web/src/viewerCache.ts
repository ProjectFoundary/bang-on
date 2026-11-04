import type {Effect,Preset,Project} from './api';
import {frameAtTime,frameTime,safeFps} from './nativeTiming';

export const VIEWER_CACHE_BYTES=256*1024*1024;
export type ViewerCacheStatus={revision:number;frame:number;ready:boolean;error:boolean;frames:number[];capacity:number;total:number;warm:boolean;fits:boolean};

/** Bounded handle-free viewport snapshots. Callers own the resource's release
 * operation (canvas backing stores or ImageBitmap.close), including rejection. */
export class ViewerFrameCache<T>{
  private entries=new Map<number,{value:T;bytes:number}>();
  private recipe='';
  private used=0;
  private hits=0;
  private misses=0;
  constructor(readonly budget=VIEWER_CACHE_BYTES,private release:(value:T)=>void=()=>{}){}
  configure(recipe:string){if(this.recipe!==recipe){this.clear();this.recipe=recipe;return true;}return false;}
  get(frame:number):T|undefined{
    const entry=this.entries.get(frame);
    if(!entry){this.misses++;return;}
    this.entries.delete(frame);this.entries.set(frame,entry);this.hits++;return entry.value;
  }
  put(frame:number,value:T,bytes:number){
    const old=this.entries.get(frame);
    if(old){this.entries.delete(frame);this.used-=old.bytes;this.release(old.value);}
    if(!Number.isSafeInteger(bytes)||bytes<=0||bytes>this.budget){this.release(value);return false;}
    while(this.used+bytes>this.budget&&this.entries.size){
      const key=this.entries.keys().next().value!,entry=this.entries.get(key)!;
      this.entries.delete(key);this.used-=entry.bytes;this.release(entry.value);
    }
    this.entries.set(frame,{value,bytes});this.used+=bytes;return true;
  }
  clear(){for(const {value} of this.entries.values())this.release(value);this.entries.clear();this.used=0;this.hits=0;this.misses=0;}
  stats(){return {frames:this.entries.size,bytes:this.used,budget:this.budget,hits:this.hits,misses:this.misses};}
  frames(){return [...this.entries.keys()];}
}

/** A cold first traversal must not skip expensive frames just because rendering
 * is slower than source time. Once warm, retain ordinary native-PTS playback. */
export class ViewerPlaybackClock{
  private last:number;
  private origin:number;
  private carry=0;
  constructor(now:number,frame:number,fps:number,times?:readonly number[]){this.last=now;this.origin=now-frameTime(frame,fps,times)*1000;}
  next(now:number,current:number,frames:number,fps:number,times:readonly number[]|undefined,duration:number,cold:boolean,ready=true){
    const elapsed=Math.max(0,(now-this.last)/1000);this.last=now;
    if(cold){
      if(!ready){this.carry=0;this.origin=now-frameTime(current,fps,times)*1000;return current;}
      const start=frameTime(current,fps,times),end=current+1<frames?frameTime(current+1,fps,times):duration;
      this.carry+=elapsed;
      const hold=Math.max(.001,end>start?end-start:1/safeFps(fps));
      const next=this.carry+1e-8>=hold?(current+1)%Math.max(1,frames):current;
      if(next!==current)this.carry=0;
      this.origin=now-frameTime(next,fps,times)*1000;
      return next;
    }
    this.carry=0;
    return frameAtTime(Math.max(0,(now-this.origin)/1000)%Math.max(.001,duration),frames,fps,times);
  }
}

export function viewerPlateKey(p:Project){
  return JSON.stringify([p.id,p.frames,p.source,p.native_profile,isPlateElements(p)?'plate':p.environment??'plate']);
}
const isPlateElements=(p:Project)=>p.settings.pipeline==='ltx_elements';

/** Hash values rather than Map/array identities, so recomputed identical paths
 * retain warm frames and edits to tracking values cannot display stale pixels. */
export function viewerPathKey(paths:Map<string,Float32Array>){
  return [...paths].map(([id,path])=>{
    let hash=2166136261;for(const word of new Uint32Array(path.buffer,path.byteOffset,path.length))hash=Math.imul(hash^word,16777619);
    return [id,path.length,hash>>>0];
  });
}
export function viewerVisualKey(p:Project,presets:Record<string,Preset>,paths:Map<string,Float32Array>,quality:string,solo:string|null,volumeKey:string|null,colliderKey:string|null,assetRevision:number){
  return JSON.stringify([viewerPlateKey(p),p.camera_mode,p.settings,p.effects,p.geometry,p.lighting,p.flashes,
    p.effects.map((e:Effect)=>presets[e.preset]),viewerPathKey(paths),quality,solo,volumeKey,colliderKey,assetRevision]);
}
