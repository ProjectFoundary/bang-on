import type {Effect,Project} from './api';
import type {VolumeElementOptions} from './volumeFX';
import {nativeFPS} from './volumeScene';

const kinds=new Set(['explosion','fire','smoke_white','smoke_plume','smoke_plume_hq','smoke_grenade','wall_hit','dirt_hit','dust_hit']);
export const isSmokePreset=(kind:string)=>['smoke_white','smoke_plume','smoke_plume_hq','smoke_grenade'].includes(kind);
export const isGrenadeSmokePreset=(kind:string)=>kind==='smoke_grenade'||kind==='smoke_white';
export const volumeKind=(effect:Effect):string|null=>kinds.has(effect.preset)?effect.preset:null;
export const elementDefaults={duration_s:2,cadence:10,frame_blend:10,guide_style:'painted' as 'painted'|'rendered',guide_displacement:0,guide_displacement_blur:32,light_intensity:1,fill_intensity:1,rim_width:16,rim_strength:0,light_rotation:0,light_elevation:60,blur:2,shading:1,seed:42,alpha_seed:1234,opacity:1,gain:1,light:0,shadow:0,depth_offset:0,colliders:[] as string[],wind_dispersion:1,smoke_dispersion:0,density:1,brightness:1};
export function elementSettings(effect:Effect){
  const saved=effect.element,legacy=!!saved&&saved.cadence!==undefined&&saved.frame_blend===undefined&&saved.guide_style===undefined;
  return {...elementDefaults,...(legacy?{cadence:1,frame_blend:1,guide_style:'rendered' as const,light_elevation:effect.preset==='smoke_plume_hq'?40:60}:{}),...saved};
}
export function elementOnset(effect:Effect):number {
  const keys=effect.keys.filter(k=>k.enabled!==false).sort((a,b)=>a.frame-b.frame);
  const phase=effect.preset==='explosion'?'onset':effect.preset==='fire'?'ignite':effect.preset.startsWith('smoke_')?'start':'impact';
  return keys.find(k=>k.phase===phase)?.frame??keys.find(k=>k.phase!=='before')?.frame??effect.anchors?.[0]?.frame??0;
}
export function elementTime(project:Project,frame:number):number {
  return project.native_profile?.timestamps?.[frame]??frame/nativeFPS(project);
}
/** Canonical simulation recipe shared by live preview and generation capture.
 * All project paths remain logical960x540; output canvases retain native aspect. */
export function volumeOptions(project:Project,effect:Effect,frame:number,paths:Map<string,Float32Array>,w:number,h:number):VolumeElementOptions {
  const fps=nativeFPS(project),start=elementOnset(effect),onsetSeconds=elementTime(project,start),duration=effect.element?.duration_s??2;
  // HQ plume duration describes the visible shot, in addition to its warm-up.
  const emissionDuration=duration+(effect.preset==='smoke_plume_hq'?Math.max(0,-onsetSeconds):0);
  const anchor=Math.max(0,Math.min(project.frames-1,start)),path=paths.get(effect.id),fallback=effect.anchors?.[0]??{x:.5,y:.5};
  const x=path?path[anchor*2]/960:fallback.x,y=path?path[anchor*2+1]/540:fallback.y;
  const angle=effect.angle??0,windDriven=effect.preset.startsWith('smoke_')||effect.preset==='fire'||effect.preset==='explosion';
  const windScale=effect.preset==='explosion'?1.2:.28,strength=windDriven?(effect.strength??0):0,a=angle*Math.PI/180;
  const tracking=(effect.tracking as 'camera'|'features'|'screen'|undefined)??'camera';
  return {w,h,n:project.frames,x:x*w,y:y*h,start,end:Math.min(project.frames-1,Math.ceil((onsetSeconds+emissionDuration)*fps)),
    onsetSeconds,durationSeconds:emissionDuration,size:effect.size??1,seed:effect.element?.seed??project.settings.seed??42,
    angle,wind:strength*Math.sin(a)*windScale,windVector:[Math.sin(a)*strength*windScale,Math.cos(a)*strength*windScale,0],windResponse:effect.preset==='explosion'?2:undefined,
    windDispersion:isGrenadeSmokePreset(effect.preset)?effect.element?.smoke_dispersion??0:effect.element?.wind_dispersion??1,
    ...(isSmokePreset(effect.preset)?{density:effect.element?.density??1,brightness:effect.element?.brightness??1}:{}),
    tracking,path:tracking==='camera'?undefined:path,
    matteIds:effect.element?.colliders??[],depthOffset:effect.element?.depth_offset??0,shading:effect.element?.shading??1,
    lightIntensity:effect.element?.light_intensity??1,fillIntensity:effect.element?.fill_intensity??1,lightRotation:effect.element?.light_rotation??0,lightElevation:elementSettings(effect).light_elevation,quality:'high'};
}
