import {spawnParticles,stepParticles} from '../sim/emitters.js';
import {spawnParticles3D,stepParticles3D} from '../sim/emitters3d.js';
import {PRESETS} from '../presets.js';
import {state} from '../state.js';
import {SUBSTEPS} from '../util.js';
import {cacheClock} from './library.js';

const memo=new Map(),discard={push(){}};
// Keep the existing large chips, gravity, drag, bounce and foreground layering.
// Replaying only their short CPU lifetimes also makes arbitrary scrubs exact.
export function cachedParticles(inst,frame,world){
 const pr=PRESETS[inst.preset],clock=cacheClock(inst,frame),physical=inst.frame+clock.time*state.fps;
 const key=JSON.stringify([world,physical,inst,pr.debris,pr.grit,pr.sparks,state.fps,state.cam?.groundY]);
 const previous=memo.get(inst.id);if(previous?.key===key)return previous.parts;
 const parts={debris:[],sparks:[]},start=Math.floor((inst.frame-.25)*SUBSTEPS),end=Math.round(physical*SUBSTEPS),dt=1/(state.fps*SUBSTEPS);
 if(clock.elapsed>=0&&end>start){
  (world?spawnParticles3D:spawnParticles)(inst,pr,parts);
  for(let sub=start;sub<end;sub++){
   (world?stepParticles3D:stepParticles)(inst,pr,parts,dt,discard);
   if(parts.debris.every(p=>p.age>=p.life)&&parts.sparks.every(p=>p.age>=p.life))break;
  }
 }
 if(memo.size>=32)memo.delete(memo.keys().next().value);memo.set(inst.id,{key,parts});return parts;
}
