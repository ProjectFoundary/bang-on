import { burstFall,integrateParticle } from './particles.js';
import { smokeSourceTemperature, smokeDraftStrength } from './smoke-source.js';
import { emitGrenadeSmoke } from './smoke-grenade.js';
import { PRESETS } from '../presets.js';
import { state } from '../state.js';
import { DEG, gauss, lin, rng, seedMix } from '../util.js';

const prSettle=inst=>PRESETS[inst.preset].dust?.settle||0;

/* Impact spray direction on screen, [x, y] in plate px (y down), length <= 1 (foreshortened).
   The in-plane fan along Direction (signed spread angle th) is tilted toward the camera by facing x 90 deg, and the fan
   swings round the tilted axis as it tilts, so facing 1 is a cone straight at the lens: a radial burst whose screen
   speed is |v| sin(angle off the camera axis). Facing 0 draws no extra randoms, so older setups keep their particles. */
function spray(inst, r, spreadDeg){
  const f=inst.facing||0, a=inst.angle*DEG, th=gauss(r)*spreadDeg*DEG*0.5;
  if (f<=0) return [Math.sin(a+th), -Math.cos(a+th),0];
  // axis = cos b d + sin b z, dir = axis cos th + (n cos ph + e2 sin ph) sin th, n = in-plane normal, e2 = -sin b d + cos b z
  const b=Math.min(f,1)*Math.PI/2, ph=(r()-0.5)*Math.PI*Math.min(f,1), st=Math.sin(th);
  const k=Math.cos(b)*Math.cos(th) - Math.sin(b)*Math.sin(ph)*st, m=Math.cos(ph)*st;
  return [Math.sin(a)*k + Math.cos(a)*m, -Math.cos(a)*k + Math.sin(a)*m,Math.sin(b)*Math.cos(th)+Math.cos(b)*Math.sin(ph)*st];
}

/* ---------- emitters ---------- */
export function emitImpact(inst,pr,tA,tB,sub,splats,ps){
  const H=state.plate.h, s=inst.scale, fps=state.fps;
  const T0=inst.frame-0.25;
  if (T0>=tA && T0<tB) spawnParticles(inst,pr,ps);
  const D=pr.dust; if (!D || inst.intensity<=0) return;
  if (D.fines) emitFines(inst,D.fines,((tA+tB)/2-T0)/fps,splats);
  if (tB<=T0 || tA>=T0+D.dur) return;
  const t=Math.max(0,(tA+tB)/2-T0)/fps;
  const e=Math.exp(-t/D.tau);
  const fall=burstFall(D,t,inst.gravityMul??1);
  const r=rng(seedMix(inst.seed,sub,inst.id*7+1));
  // grow (s): the source starts small and swells to full size, filled evenly (a front-on puff); 0 = full size at once
  const col=lin(inst.color), rad=D.radius*H*s*(D.radius2d??1)*(D.grow ? 1-0.7*Math.exp(-t/D.grow) : 1);
  const n=D.n||6;   // splats per substep: more, fainter ones give a smoother, rounder puff
  // a spray at the lens has little momentum across the frame: pushing the 2D fluid radially only rolls it into vortex pairs
  const f=Math.min(1,inst.facing||0);
  // reach (H): the dust is laid out along the spray to a front that advances and slows (tau), and push scales how hard
  // it drives the fluid. A jet pushed into still 2D fluid rolls into a mushroom; this keeps a long, low dust jet.
  const front=D.reach ? D.reach*H*s*(1-Math.exp(-t/D.tau)) : 0;
  for (let i=0;i<n;i++){
    const [dx,dy]=spray(inst,r,D.spread);
    const dist=D.reach ? r()*front+r()*rad*0.5 : (D.grow ? Math.sqrt(r()) : r())*rad*1.2;
    const sp=D.speed*H*s*(0.35+0.9*r())*e;
    const puff={x:inst.x+dx*dist+(r()-0.5)*rad*0.6+(D.drift||0)*H*s*(1-Math.exp(-t/.15)), y:inst.y+dy*dist+(r()-0.5)*rad*0.6-(D.lift||0)*H*s*(1-Math.exp(-t/.08))+(fall?.offset??0)*H,
      r:rad*(0.6+0.8*r()), vx:dx*sp, vy:dy*sp, temp:D.temp*inst.heat*Math.min(1,e*1.5), vw:0.85*(1-f)*(1-f)*(D.push??1)*Math.min(1,e*1.4),
      fragment:pr.breakup2d||0, settle:fall?.speed??(D.bulkFall?-D.bulkFall.up:(D.settle??0))*(inst.gravityMul??1),terminalSettle:(D.bulkFall?D.bulkFall.gravity/2:(D.settle??0))*(inst.gravityMul??1),col,d:0.4*D.amount*(D.amount2d??1)*inst.intensity*e*6/n, curl:inst.curl, turb:inst.turb, rate:inst.dissipate*(D.puffRate??1),linger:D.puffLinger??inst.linger};
    if(D.clusters2d){
      const cl=rng(seedMix(inst.seed,i%D.clusters2d,inst.id*7+37)),angle=cl()*Math.PI*2,reach=rad*(.25+.45*cl()),radius=rad*(.35+.25*cl());
      // Persistent source lobes retain asymmetry instead of averaging fresh
      // large splats into a round cap. Preserve each kernel's integrated dye.
      puff.x=inst.x+Math.cos(angle)*reach+(D.drift||0)*H*s*(1-Math.exp(-t/.15));
      puff.y=inst.y+Math.sin(angle)*reach-(D.lift||0)*H*s*(1-Math.exp(-t/.08))+(fall?.offset??0)*H;
      puff.d*=(puff.r/radius)**2;puff.r=radius;
    }
    splats.push(puff);
  }
}
/* Fine dust that hangs after the hit: n analytic parcels (seeded per parcel, so a pure function of t like the snow)
   drift out from the puff (spread H/s, easing off over tau s), settle at their own slow fall speed (fall H/s) and
   wander a little, laying dust as they go (fading over fade s). The 2D fluid is incompressible, so it can't swell a
   cloud on its own; this is what widens, sinks and softens the dust over the seconds after the hit. */
function emitFines(inst,F,t,splats){
  if (F.analytic || t<0 || t>F.dur) return;
  const H=state.plate.h*inst.scale, col=lin(inst.color), ramp=Math.min(1,t/0.15), fade=Math.exp(-t/F.fade);
  const d=F.amount*inst.intensity*ramp*fade/(state.fps*F.n), ease=F.tau*(1-Math.exp(-t/F.tau)), ev=Math.exp(-t/F.tau);
  for (let i=0;i<F.n;i++){
    const q=rng(seedMix(inst.seed,7919+i,inst.id*7+11));
    const u=gauss(q)*F.spread*H, w=F.fall*H*(2*q()-0.15), f=0.2+0.3*q(), ph=q()*6.283, wa=F.wander*H;
    const x=inst.x+gauss(q)*F.src*H + (u+(F.drift||0)*H)*ease + wa*Math.sin(6.283*f*t+ph)*Math.min(1,t);
    const y=inst.y+gauss(q)*F.src*H + w*t;
    splats.push({x, y, r:F.r*H*(0.7+0.6*q())*(1+0.25*t), vx:u*ev+wa*6.283*f*Math.cos(6.283*f*t+ph), vy:w, temp:0, vw:0,
      fragment:PRESETS[inst.preset].breakup2d||0, settle:F.settle??prSettle(inst), col, d, curl:inst.curl, turb:inst.turb, rate:F.rate??inst.dissipate, linger:F.linger??inst.linger});
  }
}
/* Fireball: while the fuel burns (F.dur s) hot soot is laid down as lumpy puffs through a sphere growing toward
   F.radius, each pushed outward so the edge rolls into billows. Then an afterburn (F.after s): fewer, cooler puffs
   boil out at the surface as the ball keeps swelling, so it grows lobe by lobe with fresh flame over darker soot.
   Splat heat feeds the fire field (the glow); temp is only buoyancy, so the ball can flare white-hot and still climb
   slowly. Heat above 1 burns hotter and longer. */
export function emitFireball(inst,pr,tA,tB,sub,splats){
  const F=pr.fire; if (!F || inst.intensity<=0) return;
  const T0=inst.frame-0.25, t=((tA+tB)/2-T0)/state.fps;
  if (t<0 || t>F.dur+F.after) return;
  const H=state.plate.h, s=inst.scale, r=rng(seedMix(inst.seed,sub,inst.id*7+2));
  const burn=t<=F.dur, k=burn ? 1 : 1-(t-F.dur)/F.after;   // afterburn strength, fading to nothing
  const R=F.radius*H*s*(0.4+0.6*(1-Math.exp(-t/F.grow))+F.swell*Math.max(0,t-F.dur)), e=Math.exp(-t/F.tau);
  const fh=inst.heat>0 ? F.heat+0.6*(inst.heat-1) : 0, col=lin(inst.color);
  const ax=Math.sin(inst.angle*DEG), ay=-Math.cos(inst.angle*DEG);
  for (let i=0, n=burn?5:2; i<n; i++){
    // outward from the centre, leaning along the aim direction (afterburn more so: new flame boils off the top)
    const lean=F.aim*(burn?2:3.5); let dx=gauss(r)+ax*lean, dy=gauss(r)+ay*lean; const L=Math.hypot(dx,dy)||1; dx/=L; dy/=L;
    const q=burn ? Math.sqrt(r())*0.75 : 0.65+0.3*r(), sp=F.speed*H*s*(0.4+0.6*r())*(burn ? e*(0.3+q) : 0.25*k);
    // a few puffs burn at full heat, most a little cooler, so the ball is mottled rather than evenly hot
    const h=(burn ? fh : fh-F.heat+F.aheat-F.cool*(1-k))-0.4*r()*r();
    splats.push({x:inst.x+dx*R*q, y:inst.y+dy*R*q, r:R*(burn ? 0.3+0.25*r() : 0.15+0.2*r()), vx:dx*sp, vy:dy*sp, temp:F.temp*inst.heat, vw:burn?0.8:0.5,
      col, d:F.amount*inst.intensity*(burn ? 0.5+0.5*e : 0.3*k), heat:h, curl:inst.curl, turb:inst.turb, rate:inst.dissipate, linger:inst.linger});
  }
}
/* A burning source (smoke.fire): the smoke leaves it hot, so it glows at the base and goes dark as it cools on the way up
   (the heat field and fireCool do the rest). Heat above 1 burns hotter. */
export const sourceHeat=(inst,F,r)=>F && inst.heat>0 ? F.heat+0.6*(inst.heat-1)-0.4*r()*r() : 0;
/* Flame sites along a burning line: u in -1..1 across it (half = half its length, plate px), fl the flicker 0..1 at
   time ts (s). Seeded per site, smooth in time, so tongues flare and die on their own and scrubbing is deterministic. */
export function flameSites(inst,S,ts,unit=state.plate.h){
  const half=0.5*(inst.width??0.4)*unit*inst.scale, n=Math.max(2,Math.round(S.sites*(inst.width??0.4)/0.4)), out=[];
  for (let k=0;k<n;k++){
    const q=rng(seedMix(inst.seed,k+101,inst.id*7+13)), u=((k+0.5)/n)*2-1+(q()-0.5)*1.2/n, f=0.6+1.2*q(), p=q()*6.28;
    const fl=Math.max(0, 0.45+0.75*(0.6*Math.sin(6.2832*f*ts+p)+0.4*Math.sin(6.2832*f*2.3*ts+p*1.9)));
    if (fl>0.02) out.push({u, half, fl});
  }
  return out;
}
export function emitSmoke(inst,pr,tA,tB,sub,splats){
  const S=pr.smoke, Dr=S.draft, fps=state.fps, t0=inst.frame, t1=inst.frame+inst.duration, tm=(tA+tB)/2;
  if (tm<t0 || tm>t1+(Dr ? Dr.tail*fps : 0) || inst.intensity<=0) return;
  if (S.sheet?.grenadeBurst) {
    emitGrenadeSmoke(inst,{...S,sheet:{...S.sheet,grenadeBurst:{...S.sheet.grenadeBurst,plateHeight:state.plate.h}}},tA,tB,fps,splats);
    return;
  }
  const H=state.plate.h, s=inst.scale, r=rng(seedMix(inst.seed,sub,inst.id*7+3));
  // turbulence: the source wanders, swings and puffs over time (smooth, seeded) instead of firing a steady jet
  const T=inst.turb*(S.sheet?.turbBase??1), ts=tm/fps, q=rng(seedMix(inst.seed,0,inst.id*7+9)), ph=[0,0,0,0,0,0,0,0].map(()=>q()*6.28);
  const wob=(f,p)=>0.6*Math.sin(6.2832*f*ts+p)+0.4*Math.sin(6.2832*f*2.7*ts+p*1.7);
  const ageS=Math.max(0,(tm-t0)/fps);
  const sourceTemp=smokeSourceTemperature(S,'sheet',ageS);
  const growth=1+(S.sheet?.sourceGrowth??S.sourceGrowth??0)*Math.min(1,ageS/(S.sheet?.growthTime??S.growthTime??18))**(S.sheet?.growthPower??S.growthPower??2);
  const puff=Math.max(0.15, 1+(S.sheet?.puff??.9)*T*wob(0.31,ph[0]))*growth;
  const e=Math.max(0,Math.min(1,(tm-t0)/S.ramp,(t1-tm)/S.ramp))*puff;
  const col=lin(inst.color), rad=S.radius*H*s*(1+(S.sourceWidthGrowth||0)*Math.min(1,ageS/(S.growthTime||18))**2);
  const swing=T*16*DEG*wob(0.17,ph[1]), ox=T*rad*0.6*wob(0.13,ph[2]), oy=T*rad*0.3*wob(0.19,ph[3]);
  if (S.line){   // a burning line (ground fire): flame sites spread across Fire line length, each flickering on its own
    if (tm>t1) return;
    const a0=inst.angle*DEG, dx=Math.sin(a0), dy=-Math.cos(a0), lx=Math.cos(a0), ly=Math.sin(a0);   // up the flame / along the line
    for (const f of flameSites(inst,S,ts)){
      const x=inst.x+lx*f.u*f.half, y=inst.y+ly*f.u*f.half, sp=S.speed*H*s*inst.speedMul*(0.6+0.6*f.fl);
      splats.push({x, y, r:rad, vx:dx*sp, vy:dy*sp, temp:sourceTemp*inst.heat*f.fl, vw:Math.min(0.9,(S.push??0.6)*e*f.fl), d:0});
      splats.push({x:x+(r()-0.5)*rad, y:y+(r()-0.5)*rad*0.5, r:rad*(0.6+0.6*r()), vw:0, col, d:S.amount*inst.intensity*e*f.fl*0.45,
        heat:sourceHeat(inst,S.fire,r)-0.3*(1-f.fl), curl:inst.curl*(S.sheet?.curl??1), turb:inst.turb, rate:inst.dissipate, linger:inst.linger});
    }
    return;
  }
  if (tm<=t1){
    // push: one jet whose direction flutters smoothly across Spread. Random speeds and angles per splat printed a comb
    // of vertical streaks into the column (neighbouring texels pushed at different speeds), so only the dye is jittered.
    const a=inst.angle*DEG+swing+S.spread*DEG*0.5*wob(1.1,ph[7]), sp=S.speed*H*s*inst.speedMul*(S.sheet?.speed??1)*(1+0.3*T*wob(0.23,ph[4]));
    splats.push({x:inst.x+ox, y:inst.y+oy, r:rad, vx:Math.sin(a)*sp, vy:-Math.cos(a)*sp, temp:sourceTemp*inst.heat, thermal:(S.sheet?.thermalBreakup??0)*inst.turb/.32, thermalPhase:(inst.seed%997)*.031+ageS*.41, vw:Math.min(0.9,(S.push??0.6)*e), d:0});
    for (let i=0;i<2;i++) splats.push({x:inst.x+ox+(r()-0.5)*rad*0.8, y:inst.y+oy+(r()-0.5)*rad*0.8, r:rad*(0.7+0.6*r()),
      fragment:S.sheet?.fragment||0, vw:0, col, d:S.amount*(S.sheet?.amount??1)*(1+(S.sheet?.initialAmount||0)*Math.exp(-ageS/4))*inst.intensity*e*0.45, heat:sourceHeat(inst,S.fire,r), curl:inst.curl*(S.sheet?.curl??1), turb:T*(1+(S.sheet?.turbGrowth||0)*Math.max(0,Math.min(1,(ageS-12)/12))**2), rate:inst.dissipate*(S.sheet?.dissipate??1), linger:inst.linger});
  }
  if (!Dr || inst.heat<=0) return;
  // Draft: sim heat cools within a second, so a plume on its own only climbs as far as its push carries it, then
  // stalls and mushrooms out. A real one stays warm all the way up, so keep a warm channel (temperature only: velocity and
  // eddies are left alone) standing over the source. It reaches ahead of the expected rise (Dr.rise, H/s), bends with
  // the scene wind, meanders slowly with Turbulence and cools off over Dr.tail seconds once the source stops.
  const age=(tm-t0)/fps, u=Dr.rise*H*Math.sqrt(s)*inst.heat*Math.max(0.1,state.scene.buoyancy);
  const k=smokeDraftStrength(S,'sheet',age)*Math.min(1,(tm-t0)/S.ramp,1-(tm-t1)/(Dr.tail*fps));
  const top=Math.min(Dr.height*H*Math.sqrt(s), rad*2+u*age*Dr.lead), n=Dr.n, seg=top/n;
  const amp=Math.min(0.6,Dr.wander*T)*Math.min(1,age/Dr.grow), lam=Dr.lambda*H*s, lean=state.scene.wind*H/u;
  const axis=h=>inst.x+ox*Math.max(0,1-h/(rad*4))+lean*h+amp*h*(0.6*Math.sin(6.2832*(h/lam-ts*Dr.f)+ph[5])+0.4*Math.sin(6.2832*(h/lam*1.9-ts*Dr.f*1.3)+ph[6]));
  for (let i=0;i<n;i++){   // capsules from the source up, widening with height
    const h0=i*seg, h1=h0+seg, x0=axis(h0), x1=axis(h1);
    splats.push({tempOnly:true, x:(x0+x1)/2, y:inst.y-(h0+h1)/2, ax:(x1-x0)/2, ay:-seg/2, r:rad*Dr.base+(h0+h1)/2*Dr.cone,
      temp:Dr.temp*inst.heat*Math.sqrt(s)*(1+(Dr.heatGrowth||0)*Math.max(0,Math.min(1,(age-4)/8))), vw:k, d:0});
    // As the source strengthens, rising air entrains and spreads through the crown.
    // A paired lateral draft widens the upper plume without sharpening its vortices.
    const late=Math.max(0,Math.min(1,(age-(S.sheet?.entrainStart??12))/(S.sheet?.entrainTime??8)))**2;
    if(S.sheet?.entrain && late>0 && h1>rad*4){
      const h=(h0+h1)/2,w=rad*Dr.base+h*Dr.cone;
      for(const side of [-1,1])splats.push({x:axis(h)+side*w*.55,y:inst.y-h,r:w*.8,
        vx:side*S.sheet.entrain*H*late*Math.min(1,h/(H*.6)),vy:-u*(S.sheet.entrainLift??.15),temp:Dr.temp*inst.heat,
        vw:.012*late,d:0});
    }
  }
}
/* Debris: chunks (pr.debris) and, for presets that have it, fine grit (pr.grit, flagged g:1 in the same list so
   snapshots carry it). Grit is small and draggy: it rains out of the puff at near terminal speed and its dust trails
   build the falling curtain under a wall hit. */
export function spawnParticles(inst,pr,ps){
  const H=state.plate.h, s=inst.scale, r=rng(seedMix(inst.seed,99991,inst.id*7+5));
  ps.debris.length=0; ps.sparks.length=0;
  const floor=inst.y+1;
  if (pr.debris){
    const D=pr.debris, n=Math.round(D.count*inst.debrisMul);
    for (let i=0;i<n;i++){
      const [ux,uy,uz]=spray(inst,r,D.spread), sp=D.speed*H*s*(0.25+0.95*r());
      ps.debris.push({x:inst.x+(r()-0.5)*4, y:inst.y-1-r()*2,z:0, vx:ux*sp, vy:uy*sp,vz:uz*sp,
        age:0, life:D.life*(0.6+0.8*r()), size:D.size*H*s*(0.5+1.6*r()*r()), rot:r()*6.283, spin:(r()-0.5)*26,
        shade:0.55+0.6*r(), k:r()*100, floor});
    }
  }
  if (pr.grit){
    const G=pr.grit, n=Math.round(G.count*inst.debrisMul), j=G.src*H*s;
    for (let i=0;i<n;i++){
      const [ux,uy]=spray(inst,r,G.spread), sp=G.speed*H*s*(0.15+1.1*r()), jx=(2*r()-1)*j, jy=(2*r()-1)*j*0.6;   // rains out of the whole puff
      ps.debris.push({g:1, x:inst.x+jx, y:inst.y+jy, vx:ux*sp, vy:uy*sp, age:0, life:G.life*(0.6+0.8*r()),
        size:G.size*H*s*(0.6+0.9*r()), rot:0, spin:0, shade:0.55+0.6*r(), k:0, floor, kd:1+G.spreadK*r()*r(), vis:+(r()<G.show)});   // kd: finer grit falls slower; vis: drawn as a speck
    }
  }
  if (pr.sparks){
    const K=pr.sparks, n=Math.round(K.count*inst.sparksMul);
    for (let i=0;i<n;i++){
      const [ux,uy]=spray(inst,r,K.spread), sp=K.speed*H*s*(0.4+0.9*r());
      ps.sparks.push({x:inst.x, y:inst.y-1, vx:ux*sp, vy:uy*sp, age:0, life:K.life*(0.4+1.1*r()), heat:0.7+0.6*r(), floor});
    }
  }
}
export function stepParticles(inst,pr,ps,dt,splats){
  const H=state.plate.h, s=inst.scale;
  if (ps.debris.length){
    const col=lin(inst.color), live=inst.intensity>0, A={settle:prSettle(inst)*(inst.gravityMul??1),curl:inst.curl, turb:inst.turb, rate:inst.dissipate, linger:inst.linger};
    const D=pr.debris, G=pr.grit, gravity=inst.gravityMul??1;
    for (const p of ps.debris){
      if (p.age>=p.life) continue;
      if (p.g){   // grit: falls at about terminal speed (gravity / drag x kd), laying the curtain dust
        integrateParticle(p,dt,G.drag*p.kd,G.gravity*H*s*gravity);
        if (inst.floor && p.y>p.floor){ p.y=p.floor; p.vx=p.vy=0; continue; }
        // dust is shed once the grain is out of the puff and falling (sideways flyers would draw arcs), fading over shed s
        const reach=G.shedReach==null?1:Math.max(0,Math.min(1,(G.shedReach-(p.y-inst.y)/(H*s))/(G.shedReach*.3)));
        const sp=Math.hypot(p.vx,p.vy)+1e-6, w=reach*Math.min(1,sp/(0.8*H*s),(p.age/G.delay)**2)*Math.exp(-p.age/G.shed)*(Math.max(0,p.vy)/sp)**2;
        // capsules trimmed so consecutive substeps' round ends don't overlap into beads along the streak
        const tr=G.trailR*H*s, ex=Math.max(0,1-0.36*tr/(sp*dt*0.5));
        if (live && p.age<(G.shedUntil??Infinity) && w>0.02) splats.push({x:p.x-p.vx*dt*0.5,y:p.y-p.vy*dt*0.5,ax:p.vx*dt*0.5*ex,ay:p.vy*dt*0.5*ex,r:tr,
          vx:0,vy:0,temp:0,vw:0,col,d:G.trail*0.035*inst.intensity*w, ...A, settle:(G.settle??.55)*gravity,terminalSettle:(G.terminalSettle??.025)*gravity,rate:A.rate*G.rate,linger:G.linger??A.linger});   // curtain dust outlasts the puff
        continue;
      }
      integrateParticle(p,dt,D.drag,D.gravity*H*s*gravity,true); p.rot+=p.spin*dt;
      if (inst.floor && p.y>p.floor){
        p.y=p.floor;
        if (p.vy>0){ p.vy=-p.vy*D.bounce; p.vx*=0.6;p.vz*=.6; p.spin*=0.5; if (Math.abs(p.vy)<0.05*H){ p.vy=0; p.vx*=0.85;p.vz*=.85; p.spin*=0.8; } }
      }
      const sp=Math.hypot(p.vx,p.vy);
      if (D.trail>0 && sp>0.15*H && live)
        splats.push({x:p.x-p.vx*dt*0.5,y:p.y-p.vy*dt*0.5,ax:p.vx*dt*0.5,ay:p.vy*dt*0.5,r:Math.max(p.size*2.5,0.004*H),vx:p.vx*0.3,vy:p.vy*0.3,temp:0,vw:0.12,col,d:D.trail*0.035*inst.intensity*Math.min(1,sp/(0.8*H)), ...A});
    }
  }
  if (pr.sparks && ps.sparks.length){
    const K=pr.sparks;
    for (const p of ps.sparks){
      if (p.age>=p.life) continue;
      integrateParticle(p,dt,K.drag,K.gravity*H*s*(inst.gravityMul??1));
      if (inst.floor && p.y>p.floor && p.vy>0){ p.y=p.floor; p.vy=-p.vy*0.4; p.vx*=0.7; }
    }
  }
}
