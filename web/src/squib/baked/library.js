import {SmokeCache} from './format.js';
import {state,app} from '../state.js';

export let smokeCache=null;
let serial=0;const caches=new Map(),DECODED_LIMIT=256*1024*1024;
export const getSmokeCache=inst=>caches.get(typeof inst==='string'?inst:inst.preset)??null;
export function clearDecodedCaches(){for(const cache of caches.values())cache.clear();}
export async function loadProjectCaches(){
 const base=new URL((new URLSearchParams(location.search).get('cacheBase')??'/baked').replace(/\/$/,'')+'/',location.href);
 if(base.origin!==location.origin)throw Error('Smoke caches must be served with this project');
 let loaded=0;for(const name of ['plume','wallhit']){const response=await fetch(new URL(`${name}.sqc`,base));if(response.ok){installSmokeCache(new Uint8Array(await response.arrayBuffer()));loaded++;}}
 if(!loaded)throw Error('Project caches are unavailable. Load the .sqc files instead.');return cacheStatus();
}
export function installSmokeCache(input){
 if(typeof input==='string'){const s=atob(input);input=Uint8Array.from(s,c=>c.charCodeAt(0));}
 const next=new SmokeCache(input);
 if(next.meta.referenceImageryUsed!==false||next.meta.independentSimulation!==true)throw Error('Only independently generated simulation caches are supported. Reference-derived motion is excluded.');
 const preset=next.meta.preset??'plume';if(!['plume','concrete'].includes(preset))throw Error('Unsupported baked effect');
 next.serial=++serial;caches.get(preset)?.clear();caches.set(preset,next);smokeCache=caches.get('plume')??null;
 for(const cache of caches.values()){cache.clear();cache.budget=Math.floor(DECODED_LIMIT/caches.size);}return cacheStatus();
}
export const usesBakedSmoke=inst=>!!(inst.enabled&&state.scene.smokeMode==='baked'&&(inst.preset==='plume'||inst.preset==='concrete'&&caches.has('concrete')));
export function cacheStatus(){const all=[...caches.values()];return all.length?{ready:true,serial,duration:Math.max(...all.map(c=>c.meta.duration)),
 reconstructed:all.some(c=>c.meta.reconstructedVolume===true),referenceDerived:all.some(c=>c.meta.referenceImageryUsed===true),
 bytes:all.reduce((n,c)=>n+c.bytes.byteLength,0),decodedBytes:all.reduce((n,c)=>n+c.used,0),decodedBudget:DECODED_LIMIT,frames:all.reduce((n,c)=>n+c.meta.frames.length,0),
 assets:all.map(c=>({preset:c.meta.preset??'plume',seconds:c.meta.duration,reconstructed:!!c.meta.reconstructedVolume}))}:{ready:false};}
export function cacheClock(inst,frame){
 const elapsed=((app.freezeEffectsFrame??frame)-inst.frame)/state.fps,rate=Math.max(.05,(inst.speedMul??1)*(1+((inst.seed%17)-(1000%17))*.003));
 return{elapsed,time:Math.max(0,elapsed*rate),stop:inst.duration/state.fps*rate,rate};
}
export function cacheUniforms(inst,frame){
 const cache=getSmokeCache(inst);if(!cache)throw Error('Load a baked smoke cache or choose Live smoke');
 const {meta}=cache,clock=cacheClock(inst,frame),scale=inst.scale/(meta.baseScale??2);
 return{uAge:clock.time,uStop:clock.stop,uLinger:Math.max(.01,inst.linger??.7),
  uDust:inst.preset==='concrete'?1:0,
  uFadeRate:Math.max(-.25,Math.min(5,(inst.dissipate??.28)-meta.baseDissipate)),
  uOpacity:Math.max(0,inst.intensity)*state.scene.density/meta.baseSceneDensity*scale,
  uAfter:clock.time>meta.duration?Math.exp(-(clock.time-meta.duration)*Math.max(.2,inst.dissipate??.28)):1,
  uMirror:(inst.seed&1)?-1:1,uWind:state.scene.wind??0};
}
