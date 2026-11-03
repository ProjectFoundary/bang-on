import { burstFall,integrateParticle } from './particles.js';
import { smokeSourceTemperature, smokeDraftStrength } from './smoke-source.js';
import { state } from '../state.js';
import { DEG, gauss, lin, rng, seedMix } from '../util.js';
import { camAt } from '../io/camera.js';
import { flameSites, sourceHeat } from './emitters.js';

/* =========================================================================
   3D emitters: the same presets and randomness as emitters.js, but in world
   units. inst.w is the world position, inst.wdir the unit direction and
   inst.wscale the world size of one plate height at the effect, so preset
   sizes (fractions of plate height) and speeds (plate heights/s) carry over.
   Splats: {p, r, v (world/s), temp, vw, col, d, curl, turb, rate, linger}
   ========================================================================= */
function basis(d){
  const a=Math.abs(d[1])<0.9 ? [0,1,0] : [1,0,0];
  const e1=[d[1]*a[2]-d[2]*a[1], d[2]*a[0]-d[0]*a[2], d[0]*a[1]-d[1]*a[0]], L1=Math.hypot(...e1);
  for (let i=0;i<3;i++) e1[i]/=L1;
  return [e1, [d[1]*e1[2]-d[2]*e1[1], d[2]*e1[0]-d[0]*e1[2], d[0]*e1[1]-d[1]*e1[0]]];
}
/* random direction in a cone around d: polar angle theta, azimuth phi */
function cone(d,[e1,e2],theta,phi){
  const s=Math.sin(theta), c=Math.cos(theta), cp=Math.cos(phi), sp=Math.sin(phi);
  return [0,1,2].map(i=>d[i]*c+(e1[i]*cp+e2[i]*sp)*s);
}
const attrs=inst=>({curl:inst.curl, turb:inst.turb, rate:inst.dissipate, linger:inst.linger});
const grit3d=pr=>pr.grit?{...pr.grit,...pr.grit.volume}:null;
/* spray axis: Direction (wdir, in the camera plane) tilted toward the camera by facing x 90 deg, at the effect's frame */
function aim(inst){
  const f=Math.min(1,inst.facing||0); if (f<=0) return inst.wdir;
  const b=camAt(inst.frame).back, c=Math.cos(f*Math.PI/2), s=Math.sin(f*Math.PI/2);
  const a=[0,1,2].map(i=>inst.wdir[i]*c+b[i]*s), L=Math.hypot(...a);
  return a.map(v=>v/L);
}

/* Impact dust: the 2D spray fans across the image, but in 3D the same spread is a cone that sends half of it into
   depth, so the source and spray are widened to read at the 2D size (dialled in against the 2D dirt and concrete hits). */
export const DUST3D = {radius:1.5, spread:2.6, amount:1.6, speed:1.15, push:0.6, grit:1.1, gritR:2.5, fines:.6, finesFade:1.0, front:.5};

export function emitImpact3D(inst,pr,tA,tB,sub,splats){
  const fps=state.fps, s=inst.scale, Hw=inst.wscale, T0=inst.frame-0.25;
  const D=pr.dust; if (!D || inst.intensity<=0) return;
  if (D.fines && !(sub&1)) emitFines3D(inst,D.fines,((tA+tB)/2-T0)/fps,splats);   // every other substep: 3D splats cost a volume pass per 32
  if (tB<=T0 || tA>=T0+D.dur) return;
  const t=Math.max(0,(tA+tB)/2-T0)/fps, e=Math.exp(-t/D.tau);
  const r=rng(seedMix(inst.seed,sub,inst.id*7+1));
  // the DUST3D widening is for sprays across the frame; one aimed at the lens already reads at full width, and is as
  // deep as it is wide, so it takes less dust (front) to read as dense as the 2D puff
  const f=Math.min(1,inst.facing||0), K=k=>k+(1-k)*f, ax=aim(inst), n=D.n||6, cameraRight=camAt(inst.frame).right;
  // 2D front hits need very little in-plane push. A 3D burst can also drive
  // gas into depth; keep that optional impulse independent of the 2D reduction.
  const facingPush=(1-f)*(1-f)+(D.frontPush3d??0)*f*f;
  const col=lin(inst.color), rad=D.radius*Hw*s*K(DUST3D.radius)*(D.grow ? 1-0.7*Math.exp(-t/D.grow) : 1), B=basis(ax);
  const gravity=inst.gravityMul??1,fall=burstFall(D,t,gravity),material={
    settle:fall?.speed??(D.bulkFall?-D.bulkFall.up:(D.initialSettle3d??D.initialSettle??D.settle??0))*gravity,
    terminalSettle:(D.bulkFall?D.bulkFall.gravity/2:(D.settle??0))*gravity};
  const fallOffset=(fall?.offset??0)*Hw;
  const front=D.reach ? D.reach*Hw*s*(1-Math.exp(-t/D.tau)) : 0;   // see emitImpact
  for (let i=0;i<n;i++){
    const dir=cone(ax,B,Math.min(Math.PI*0.95,Math.abs(gauss(r))*D.spread*K(DUST3D.spread)*DEG*0.5),r()*6.2832);
    const dist=D.reach ? r()*front+r()*rad*0.5 : (D.grow ? Math.sqrt(r()) : r())*rad*1.2, sp=D.speed*Hw*s*DUST3D.speed*(0.35+0.9*r())*e;
    // Twelve persistent sub-clouds retain asymmetric structure through the burst instead
    // of averaging many large overlapping spheres into a smooth cap.
    const cl=pr.breakup3d ? rng(seedMix(inst.seed,i%12,inst.id*7+37)) : null;
    const cluster=cl ? cone(ax,B,Math.acos(2*cl()-1),cl()*6.2832) : dir;
    const reach=cl ? rad*(.2+.6*cl()) : dist;
    const size=cl ? 0.36+0.18*cl() : null;
    const j=()=>(r()-0.5)*rad*(cl?0.18:0.6);
    let gas=dir;
    if(cl&&D.radialPush3d){
      gas=dir.map((v,a)=>v+(cluster[a]-v)*D.radialPush3d);
      const length=Math.hypot(...gas);gas=length>1e-6?gas.map(v=>v/length):dir;
    }
    splats.push({p:[0,1,2].map(a=>inst.w[a]+cluster[a]*reach+j()+cameraRight[a]*(D.drift||0)*Hw*s*(1-Math.exp(-t/.15))+(a===1?(D.lift3d||0)*Hw*s*(1-Math.exp(-t/.08))-fallOffset:0)), r:rad*(size??(0.6+0.8*r())), v:gas.map(v=>v*sp),
      temp:D.temp*inst.heat*Math.min(1,e*1.5), vw:0.85*DUST3D.push*facingPush*(D.push??1)*Math.min(1,e*1.4), col, d:0.4*D.amount*inst.intensity*e*(cl?7.2:1.0)*(DUST3D.amount*(1-f)+DUST3D.front*f)*6/n,...attrs(inst),...material,rate:inst.dissipate*(D.puffRate??1),linger:D.puffLinger??inst.linger});
  }
  // A filled core joins the smaller asymmetric lobes. Keeping all density on
  // separated source centres leaves a split, antler-shaped front-facing puff.
  if(pr.breakup3d)splats.push({p:[0,1,2].map(a=>inst.w[a]+cameraRight[a]*(D.drift||0)*Hw*s*(1-Math.exp(-t/.15))+(a===1?(D.lift3d||0)*Hw*s*(1-Math.exp(-t/.08))-fallOffset:0)),
    r:rad*.8,v:[0,0,0],temp:0,vw:0,col,d:.4*D.amount*inst.intensity*e*4*(DUST3D.amount*(1-f)+DUST3D.front*f)*6/n,
    ...attrs(inst),...material,rate:inst.dissipate*(D.puffRate??1),linger:D.puffLinger??inst.linger});
}
/* the 2D fines (emitters.js emitFines) in world units: drifting out horizontally in a seeded direction, settling along -Y */
function emitFines3D(inst,F,t,splats){
  if (F.analytic || t<0 || t>F.dur) return;
  const cameraRight=camAt(inst.frame).right;
  const Hw=inst.wscale*inst.scale, col=lin(inst.color), ramp=Math.min(1,t/0.15), fade=Math.exp(-t/(F.fade*DUST3D.finesFade));
  const d=2*F.amount*inst.intensity*ramp*fade*DUST3D.fines/(state.fps*F.n), ease=F.tau*(1-Math.exp(-t/F.tau)), ev=Math.exp(-t/F.tau);
  for (let i=0;i<F.n;i++){
    const q=rng(seedMix(inst.seed,7919+i,inst.id*7+11));
    const u=gauss(q)*F.spread*Hw*1.4, w=F.fall*Hw*(2*q()-0.15), fr=0.2+0.3*q(), ph=q()*6.283, wa=F.wander*Hw, az=q()*6.283;
    const hx=Math.cos(az), hz=Math.sin(az), lat=u*ease+wa*Math.sin(6.283*fr*t+ph)*Math.min(1,t), vl=u*ev+wa*6.283*fr*Math.cos(6.283*fr*t+ph);
    const p=[inst.w[0]+gauss(q)*F.src*Hw+hx*lat+cameraRight[0]*(F.drift||0)*Hw*ease, inst.w[1]+gauss(q)*F.src*Hw-w*t, inst.w[2]+gauss(q)*F.src*Hw+hz*lat+cameraRight[2]*(F.drift||0)*Hw*ease];
    splats.push({p, r:F.r*Hw*(0.7+0.6*q())*(1+0.25*t), v:[hx*vl,-w,hz*vl], temp:0, vw:0, col, d, ...attrs(inst),rate:F.rate??inst.dissipate,linger:F.linger??inst.linger});
  }
}

/* A 3D plume spreads into depth as well, so at the same settings it reads narrower and thinner than the 2D sim
   (which is effectively an infinitely deep sheet). These factors make the preset defaults read like 2D (dialled in on
   the plume against a reference element): a narrower source with a stronger push (3D shows no streaks from it),
   more sideways turbulence and a wider draft so the column spreads with height, and a touch more draft heat. */
export const SMOKE3D = {radius:0.8, spread:1.3, amount:0.9, push:2.5, heat:1.05, turb:2, curl:1.5, cone:1.8};

export function emitSmoke3D(inst,pr,tA,tB,sub,splats){
  const S=pr.smoke, Dr=S.draft, K={...SMOKE3D, ...(S.volume||{})}, fps=state.fps, t0=inst.frame, t1=inst.frame+inst.duration, tm=(tA+tB)/2;
  if (tm<t0 || tm>t1+(Dr ? Dr.tail*fps : 0) || inst.intensity<=0) return;
  const s=inst.scale, Hw=inst.wscale, r=rng(seedMix(inst.seed,sub,inst.id*7+3));
  // same wander/puff as the 2D emitter, swinging in both axes around the direction
  const T=inst.turb, ts=tm/fps, q=rng(seedMix(inst.seed,0,inst.id*7+9)), ph=[0,0,0,0,0,0,0,0,0,0,0,0].map(()=>q()*6.28);
  const wob=(f,p)=>0.6*Math.sin(6.2832*f*ts+p)+0.4*Math.sin(6.2832*f*2.7*ts+p*1.7);
  const ageS=Math.max(0,(tm-t0)/fps);
  const sourceTemp=smokeSourceTemperature(S,'volume',ageS);
  const growth=1+(K.sourceGrowth??S.sourceGrowth??0)*Math.min(1,ageS/(K.growthTime??S.growthTime??18))**(K.growthPower??S.growthPower??2);
  const puff=Math.max(0.15, 1+(K.puff??0.9)*T*wob(0.31,ph[0]))*growth;
  const e=Math.max(0,Math.min(1,(tm-t0)/S.ramp,(t1-tm)/S.ramp))*puff;
  const col=lin(inst.color), rad=S.radius*Hw*s*K.radius*(1+(S.sourceWidthGrowth||0)*Math.min(1,ageS/(S.growthTime||18))**2), B=basis(inst.wdir), at={...attrs(inst),rate:inst.dissipate*(K.dissipate??1), curl:inst.curl*K.curl*(S.curl3d??1), turb:inst.turb*K.turb*(1+(K.turbGrowth||0)*Math.min(1,ageS/18)**2)};
  const tilt=(d,E,a1,a2)=>{ const v=[0,1,2].map(a=>d[a]+E[0][a]*Math.tan(a1)+E[1][a]*Math.tan(a2)), l=Math.hypot(...v); return v.map(x=>x/l); };
  const dir0=tilt(inst.wdir,B,T*16*DEG*wob(0.17,ph[1]),T*16*DEG*wob(0.21,ph[5])), B0=basis(dir0);
  const off=[0,1,2].map(a=>B[0][a]*T*rad*0.6*wob(0.13,ph[2])+B[1][a]*T*rad*0.6*wob(0.19,ph[3]));
  if (S.line){   // ground fire (see emitSmoke): the line runs across the view, along the camera's right flattened against wdir
    if (tm>t1) return;
    const R=camAt(inst.frame).right, k=R[0]*inst.wdir[0]+R[1]*inst.wdir[1]+R[2]*inst.wdir[2], L0=R.map((v,a)=>v-k*inst.wdir[a]), Ll=Math.hypot(...L0)||1, L=L0.map(v=>v/Ll);
    for (const f of flameSites(inst,S,ts,Hw)){
      const p=[0,1,2].map(a=>inst.w[a]+L[a]*f.u*f.half), sp=S.speed*Hw*s*inst.speedMul*(0.6+0.6*f.fl);
      splats.push({p, r:rad, v:inst.wdir.map(v=>v*sp), temp:sourceTemp*inst.heat*f.fl, vw:Math.min(0.9,(S.push??0.6)*K.push*e*f.fl), col, d:0, ...at});
      const j=()=>(r()-0.5)*rad;
      splats.push({p:p.map(v=>v+j()), r:rad*(0.6+0.6*r()), v:[0,0,0], temp:0, vw:0, col, d:S.amount*inst.intensity*e*f.fl*0.45*K.amount,
        heat:sourceHeat(inst,S.fire,r)-0.3*(1-f.fl), ...at});
    }
    return;
  }
  if (tm<=t1){
    // push: one jet fluttering across Spread, only the dye is jittered (see emitSmoke)
    const fs=S.spread*K.spread*DEG*0.5, dir=tilt(dir0,B0,fs*wob(1.1,ph[7]),fs*wob(1.3,ph[8]));
    const sp=S.speed*Hw*s*inst.speedMul*(1+0.3*T*wob(0.23,ph[4]));
    splats.push({p:[0,1,2].map(a=>inst.w[a]+off[a]), r:rad, v:dir.map(v=>v*sp), temp:sourceTemp*inst.heat, vw:Math.min(0.9,(S.push??0.6)*K.push*e), col, d:0, ...at});
    for (let i=0;i<2;i++){
      const j=()=>(r()-0.5)*rad*0.8;
      splats.push({p:[0,1,2].map(a=>inst.w[a]+off[a]+j()), r:rad*(0.7+0.6*r()), v:[0,0,0], temp:0, vw:0, col, d:S.amount*inst.intensity*e*0.45*K.amount*(1+(K.initialAmount||0)*Math.exp(-ageS/5)), heat:sourceHeat(inst,S.fire,r), ...at});
    }
  }
  if (!Dr || inst.heat<=0) return;
  // draft: the warm channel the plume rises through (see emitSmoke), straight up in world space, meandering in x and z
  const age=(tm-t0)/fps, u=Dr.rise*Hw*Math.sqrt(s)*inst.heat*Math.max(0.1,state.scene.buoyancy), rd=S.radius*Hw*s;
  const k=smokeDraftStrength(S,'volume',age)*Math.min(1,(tm-t0)/S.ramp,1-(tm-t1)/(Dr.tail*fps));
  const top=Math.min(Dr.height*Hw*Math.sqrt(s), rd*2+u*age*Dr.lead), n=Dr.n*2;   // overlapping spheres rather than capsules
  const amp=Math.min(0.6,Dr.wander*T)*Math.min(1,age/Dr.grow), lam=Dr.lambda*Hw*s, lean=state.scene.wind*Hw/u;
  const mz=(h,p1,p2)=>amp*h*(0.6*Math.sin(6.2832*(h/lam-ts*Dr.f)+p1)+0.4*Math.sin(6.2832*(h/lam*1.9-ts*Dr.f*1.3)+p2));
  for (let i=0;i<n;i++){
    const h=(i+0.5)*top/n, fo=Math.max(0,1-h/(rad*4));
    const center=[inst.w[0]+off[0]*fo+lean*h+mz(h,ph[6],ph[9]), inst.w[1]+h, inst.w[2]+off[2]*fo+mz(h,ph[10],ph[11])],width=rd*Dr.base+h*Dr.cone*K.cone;
    splats.push({p:center, r:width,
      v:[0,0,0], temp:Dr.temp*inst.heat*Math.sqrt(s)*K.heat*(1+(K.draftHeatGrowth??Dr.heatGrowth??0)*Math.max(0,Math.min(1,(age-4)/8)))*(1+(K.heatGrowth||0)*Math.max(0,Math.min(1,(ageS-12)/12))**2), vw:k, tempOnly:true, col, d:0, ...at});
    const late=Math.max(0,Math.min(1,(age-6)/10))**2;
    if(K.entrain&&late>0&&i>n*.5){
      for(const side of [[1,0,0],[-1,0,0],[0,0,1],[0,0,-1]])splats.push({
        p:center.map((p,a)=>p+side[a]*width*.4),r:width*.65,
        v:side.map(v=>v*K.entrain*Hw*late*Math.min(1,h/(Hw*.6))),
        temp:Dr.temp*inst.heat,vw:.012*late,col,d:0,...at});
    }
  }
}

/* ---------- debris and sparks in world space ----------
   Same presets and seeded randomness as the 2D particles, but positions and velocities are world units, gravity
   pulls along -Y, and everything bounces on the ground plane (a wall hit's debris falls to the floor). Debris trails
   feed dust into the effect's volume, which is what breaks impact dust into clumps and streaks. */
export function spawnParticles3D(inst,pr,ps){
  const s=inst.scale, Hw=inst.wscale, r=rng(seedMix(inst.seed,99991,inst.id*7+5)), ax=aim(inst), B=basis(ax), gy=state.cam.groundY;
  ps.debris.length=0; ps.sparks.length=0;
  const at=()=>inst.w.map(v=>v+(r()-0.5)*0.004*Hw);
  if (pr.debris){
    const D=pr.debris, n=Math.round(D.count*inst.debrisMul);
    for (let i=0;i<n;i++){
      const dir=cone(ax,B,Math.min(Math.PI*0.95,Math.abs(gauss(r))*D.spread*1.3*DEG*0.5),r()*6.2832), sp=D.speed*Hw*s*(0.25+0.95*r());
      const [x,y,z]=at();
      ps.debris.push({x,y,z, vx:dir[0]*sp, vy:dir[1]*sp, vz:dir[2]*sp, age:0, life:D.life*(0.6+0.8*r()), size:D.size*Hw*s*(0.5+1.6*r()*r()),
        rot:r()*6.283, spin:(r()-0.5)*26, shade:0.55+0.6*r(), k:r()*100, floor:gy});
    }
  }
  if (pr.grit){   // grit (see emitters.js): rains out of the whole puff, each grain with its own drag
    const G=grit3d(pr), n=Math.round(G.count*inst.debrisMul), j=G.src*Hw*s;
    for (let i=0;i<n;i++){
      const dir=cone(ax,B,Math.min(Math.PI*0.95,Math.abs(gauss(r))*G.spread*DEG*0.5),r()*6.2832), sp=G.speed*Hw*s*(0.15+1.1*r());
      const x=inst.w[0]+(2*r()-1)*j, y=inst.w[1]+(2*r()-1)*j*0.6, z=inst.w[2]+(2*r()-1)*j;
      ps.debris.push({g:1, x,y,z, vx:dir[0]*sp, vy:dir[1]*sp, vz:dir[2]*sp, age:0, life:G.life*(0.6+0.8*r()), size:G.size*Hw*s*(0.6+0.9*r()),
        rot:0, spin:0, shade:0.55+0.6*r(), k:0, floor:gy, kd:1+G.spreadK*r()*r(), vis:+(r()<G.show)});
    }
  }
  if (pr.sparks){
    const K=pr.sparks, n=Math.round(K.count*inst.sparksMul);
    for (let i=0;i<n;i++){
      const dir=cone(ax,B,Math.min(Math.PI*0.95,Math.abs(gauss(r))*K.spread*1.3*DEG*0.5),r()*6.2832), sp=K.speed*Hw*s*(0.4+0.9*r());
      const [x,y,z]=at();
      ps.sparks.push({x,y,z, vx:dir[0]*sp, vy:dir[1]*sp, vz:dir[2]*sp, age:0, life:K.life*(0.4+1.1*r()), heat:0.7+0.6*r(), floor:gy});
    }
  }
}
export function stepParticles3D(inst,pr,ps,dt,splats){
  const Hw=inst.wscale, s=inst.scale;
  if (ps.debris.length){
    const D=pr.debris, G=grit3d(pr), gravity=inst.gravityMul??1, col=lin(inst.color);
    for (const p of ps.debris){
      if (p.age>=p.life) continue;
      if (p.g){   // grit: near terminal fall, shedding dust once it is falling out of the puff
        integrateParticle(p,dt,G.drag*p.kd,-G.gravity*Hw*s*gravity,true);
        if (p.y<p.floor){ p.y=p.floor; p.vx=p.vy=p.vz=0; continue; }
        const reach=G.shedReach==null?1:Math.max(0,Math.min(1,(G.shedReach-(inst.w[1]-p.y)/(Hw*s))/(G.shedReach*.3)));
        const sp=Math.hypot(p.vx,p.vy,p.vz)+1e-6, w=reach*Math.min(1,sp/(0.8*Hw*s),(p.age/G.delay)**2)*Math.exp(-p.age/G.shed)*(Math.max(0,-p.vy)/sp)**2;
        if (inst.intensity>0 && p.age<(G.shedUntil??Infinity) && w>0.02) splats.push({p:[p.x-p.vx*dt*0.5, p.y-p.vy*dt*0.5, p.z-p.vz*dt*0.5], r:G.trailR*Hw*s*DUST3D.gritR, v:[0,0,0],
          temp:0, vw:0, col, d:G.trail*0.035*inst.intensity*w*DUST3D.grit, ...attrs(inst),settle:(G.settle??.55)*gravity,terminalSettle:(G.terminalSettle??.025)*gravity, rate:inst.dissipate*G.rate,linger:G.linger??inst.linger});
        continue;
      }
      integrateParticle(p,dt,D.drag,-D.gravity*Hw*s*gravity,true); p.rot+=p.spin*dt;
      if (p.y<p.floor){
        p.y=p.floor;
        if (p.vy<0){ p.vy=-p.vy*D.bounce; p.vx*=0.6; p.vz*=0.6; p.spin*=0.5; if (Math.abs(p.vy)<0.05*Hw){ p.vy=0; p.vx*=0.85; p.vz*=0.85; p.spin*=0.8; } }
      }
      const sp=Math.hypot(p.vx,p.vy,p.vz);
      if (D.trail>0 && sp>0.15*Hw && inst.intensity>0)
        splats.push({p:[p.x-p.vx*dt*0.5, p.y-p.vy*dt*0.5, p.z-p.vz*dt*0.5], r:Math.max(p.size*2.5,0.004*Hw)*DUST3D.radius, v:[p.vx*0.3,p.vy*0.3,p.vz*0.3],
          temp:0, vw:0.12, col, d:D.trail*0.035*inst.intensity*Math.min(1,sp/(0.8*Hw))*DUST3D.amount, ...attrs(inst)});
    }
  }
  if (pr.sparks && ps.sparks.length){
    const K=pr.sparks;
    for (const p of ps.sparks){
      if (p.age>=p.life) continue;
      integrateParticle(p,dt,K.drag,-K.gravity*Hw*s*(inst.gravityMul??1),true);
      if (p.y<p.floor && p.vy<0){ p.y=p.floor; p.vy=-p.vy*0.4; p.vx*=0.7; p.vz*=0.7; }
    }
  }
}

/* Fireball in world units: the 2D emitFireball (burn, then afterburn puffs boiling out at the surface) with directions
   spread over a sphere, leaning along wdir. */
export function emitFireball3D(inst,pr,tA,tB,sub,splats){
  const F=pr.fire; if (!F || inst.intensity<=0) return;
  const T0=inst.frame-0.25, t=((tA+tB)/2-T0)/state.fps;
  if (t<0 || t>F.dur+F.after) return;
  const s=inst.scale, Hw=inst.wscale, r=rng(seedMix(inst.seed,sub,inst.id*7+2));
  const burn=t<=F.dur, k=burn ? 1 : 1-(t-F.dur)/F.after;
  const R=F.radius*Hw*s*FIRE3D.radius*(0.4+0.6*(1-Math.exp(-t/F.grow))+F.swell*Math.max(0,t-F.dur)), e=Math.exp(-t/F.tau);
  const fh=inst.heat>0 ? F.heat+0.6*(inst.heat-1) : 0, col=lin(inst.color);
  for (let i=0, n=burn?5:2; i<n; i++){
    const d=[0,1,2].map(a=>gauss(r)+inst.wdir[a]*F.aim*(burn?2:3.5)), L=Math.hypot(...d)||1;
    for (let a=0;a<3;a++) d[a]/=L;
    const q=burn ? Math.cbrt(r())*0.75 : 0.65+0.3*r(), sp=F.speed*Hw*s*(0.4+0.6*r())*(burn ? e*(0.3+q) : 0.25*k);
    const h=(burn ? fh : fh-F.heat+F.aheat-F.cool*(1-k))-0.4*r()*r();
    splats.push({p:[0,1,2].map(a=>inst.w[a]+d[a]*R*q), r:R*(burn ? 0.3+0.25*r() : 0.15+0.2*r()), v:d.map(v=>v*sp), temp:F.temp*inst.heat*FIRE3D.temp, vw:burn?0.8:0.5,
      col, d:F.amount*inst.intensity*(burn ? 0.5+0.5*e : 0.3*k)*FIRE3D.amount, heat:h, ...attrs(inst), rate:inst.dissipate*FIRE3D.rate});
  }
}
/* 3D calibration against the 2D fireball: a sphere spreads into depth as well, so it reads smaller, and a 3D ball
   climbs slower on the same heat. It's denser so it reads as opaque as the reference, and its smoke clears faster
   to make up for that (dialled in against the reference fireball and the 2D look) */
export const FIRE3D = {radius:1.25, amount:2.2, temp:1.25, rate:1.2};
