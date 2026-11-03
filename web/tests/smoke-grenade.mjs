import assert from 'node:assert/strict';
import { grenadeFront, emitGrenadeSmoke } from '../src/squib/sim/smoke-grenade.js';

const settings={plateHeight:576,amount:8,parcels:32,kick:.42,decay:.7,spread:.06};
const smoke={sheet:{grenadeBurst:settings,turbBase:.5,dissipate:.9}};
const inst={frame:-12,duration:80,intensity:1,scale:1.6,seed:42,x:400,y:540,color:'#d9dadb',curl:10,turb:.5,dissipate:.2,linger:2};
const emit=(a,b,fps=24)=>{const out=[];emitGrenadeSmoke(inst,smoke,a,b,fps,out);return out;};
assert.equal(emit(-14,-13).length,0);
assert.deepEqual(emit(0,.5),emit(0,.5),'Deterministic emission is required for scrubbing');
for(const f of [0,12,24,48]){
 const rows=emit(f,f+.5);
 assert.equal(rows.length,64);
 assert.ok(rows.every(row=>Number.isFinite(row.x+row.y+row.r)&&row.r>0));
 assert.ok(rows.filter(row=>row.d>0).every(row=>row.heat===0));
}
assert.ok(grenadeFront(2.5,settings)>grenadeFront(.5,settings)*1.8,'Cloud must keep expanding over the shot');
const mass=(fps)=>{
 let total=0;
 for(let n=0;n<fps*2;n++)for(const row of emitGrenadeSmokeRows(n/fps,(n+1)/fps))total+=row.d;
 return total;
};
function emitGrenadeSmokeRows(a,b){const out=[];emitGrenadeSmoke({...inst,frame:0},smoke,a,b,1,out);return out;}
assert.ok(Math.abs(mass(24)/mass(48)-1)<.01,'Emission mass should not depend on integration rate');
assert.equal(emit(70,71).length,0);
console.log('Grenade emission: pre-roll, growth, deterministic scrubbing, finite smoke-only parcels and integration-rate mass checks passed.');
