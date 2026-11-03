// A projected expanding source for short smoke-grenade shots. The normal fluid
// solver still transports the smoke and handles moving colliders. This is an
// art-directed 2D volume source, not a calibrated 3D pressure blast.
import { lin, rng, seedMix } from '../util.js';

export function grenadeFront(age, settings) {
  const t = Math.max(0, age), tau = Math.max(.05, settings.decay ?? .7);
  return (settings.core ?? .018) + (settings.kick ?? .42) * tau * (1-Math.exp(-t/tau)) + (settings.spread ?? .06)*t;
}

export function emitGrenadeSmoke(inst, smoke, tA, tB, fps, splats) {
  const B = smoke.sheet.grenadeBurst, age = ((tA+tB)*.5-inst.frame)/fps;
  if (age < 0 || age > inst.duration/fps || inst.intensity <= 0) return;
  const unit = B.plateHeight*inst.scale, dt = (tB-tA)/fps;
  const front = unit*grenadeFront(age, B), n = B.parcels ?? 32;
  const tau = Math.max(.05, B.decay ?? .7), speed = unit*((B.kick ?? .42)*Math.exp(-age/tau)+(B.spread ?? .06));
  const ramp = Math.min(1, age/.10), feed = (B.amount ?? 8)*ramp*Math.exp(-age/(B.feedDecay ?? 4));
  const col = lin(inst.color), vertical = B.vertical ?? .62;
  const cohort = Math.floor(age*8), frontPhase = (inst.seed%997)*.013;
  for (let j=0; j<n; j++) {
    // Renew a dense set of overlapping source samples. Persistent sparse rays
    // leave fingers as the emission footprint advances outwards.
    const q = rng(seedMix(inst.seed,j+cohort*2047,719));
    const phase=q()*Math.PI*2, theta=Math.PI*2*(j+.30*(q()-.5))/n + .12*Math.sin(age*.9+phase);
    const fraction = j%3===0 ? .72+.26*q() : Math.sqrt(q())*.82;
    const asymmetry = 1+.12*Math.sin(3*theta+frontPhase)+.07*Math.sin(7*theta-age*.5+frontPhase);
    const reach = front*fraction*asymmetry;
    const radius = unit*(B.parcelRadius ?? .020)*(.72+.6*q())*(1+.22*(1-Math.exp(-age)));
    const dx=Math.cos(theta), dy=Math.sin(theta)*vertical;
    const x=inst.x+dx*reach, y=inst.y+dy*reach-front*.16;
    // Several small overlapping parcels avoid a pair of oversized round source
    // stamps. Coherent directions avoid a comb of unrelated per-step jets.
    splats.push({x,y,r:radius*1.45,vx:dx*speed*fraction,vy:(dy-.16)*speed*fraction,
      temp:B.temp ?? .025,vw:Math.min(.55,(B.push ?? .16)*ramp),d:0});
    splats.push({x,y,r:radius,ax:dx*radius*.38,ay:dy*radius*.38,fragment:B.fragment ?? .5,vw:0,
      col,d:feed*dt*(32/n)*(.65+.35*q())*inst.intensity,heat:0,
      curl:inst.curl,turb:inst.turb*(smoke.sheet.turbBase ?? 1),
      rate:inst.dissipate*(smoke.sheet.dissipate ?? 1),linger:inst.linger});
  }
}
