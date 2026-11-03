import { state } from '../state.js';
import { DEG, lin, rng, seedMix } from '../util.js';

/* =========================================================================
   Snow: blowing flakes and ridge spindrift. Both are analytic (a pure function
   of the frame), so they scrub instantly and never touch the fluid sim.
   ========================================================================= */
const TAU = 6.2831853;

/* 0..1 fade at the start/end of a continuous effect. No fade where it runs off the plate, so snow is already falling on the first frame. */
export function envelope(inst, frame, ramp){
  const pl=state.plate, t0=inst.frame, t1=inst.frame+inst.duration;
  if (frame<t0 || frame>t1) return 0;
  const a = t0<=pl.first ? 1 : (frame-t0)/ramp, b = t1>=pl.last ? 1 : (t1-frame)/ramp;
  return Math.max(0, Math.min(1, a, b));
}

/* Blowing snow. Each flake slot loops across the region on its own period; every loop gets a fresh random
   path through the region and fades at its ends, so coverage is steady from the first frame. */
export function pushSnowFlakes(inst, pr, frame, spr, rscale, tint){
  const F=pr.flakes, e=envelope(inst,frame,F.ramp);
  if (e<=0 || inst.intensity<=0) return;
  const W=state.plate.w, H=state.plate.h, fps=state.fps, S=state.scene;
  let x0,y0,x1,y1;
  if (inst.full){ const m=0.06*H; x0=-m; y0=-m; x1=W+m; y1=H+m; }
  else { const hs=inst.area*H*0.5; x0=inst.x-hs; x1=inst.x+hs; y0=inst.y-hs; y1=inst.y+hs; }
  const bw=x1-x0, bh=y1-y0, diag=Math.hypot(bw,bh), edge=0.15*Math.min(bw,bh);
  const n=Math.min(20000, Math.round(F.count*inst.intensity*(bw*bh)/(W*H)));
  const a=inst.angle*DEG, wx=Math.sin(a), wy=-Math.cos(a);
  const V=F.speed*H*inst.speedMul, windS=S.wind*H;
  const t=frame/fps, sh=0.25/fps;                     // +-quarter frame = 180 degree shutter
  const turbA=inst.curl*0.0012*H, depth=inst.depth, gust=inst.gust;
  const gr=rng(seedMix(inst.seed,0,inst.id*13+1)), g1=gr()*TAU, g2=gr()*TAU;
  const w1=TAU*0.17, w2=TAU*0.53;
  // integral of the gust speed factor 1 + 0.5*gust*(0.6 sin(w1 t) + 0.4 sin(w2 t))
  const G=(t,ph)=>t - gust*0.5*(0.6*Math.cos(w1*t+g1+ph)/w1 + 0.4*Math.cos(w2*t+g2+ph)/w2);
  const col=lin(inst.color), cr=col[0]*tint[0], cg=col[1]*tint[1], cb=col[2]*tint[2];
  const pos=[0,0], pos2=[0,0];
  for (let i=0;i<n;i++){
    const r=rng(seedMix(inst.seed,i,inst.id*13+2));
    const z=Math.sqrt(r());                            // 0 near .. 1 far; most flakes are far
    const pf=1+(0.9-1.45*z)*depth;                     // parallax: near flakes cross the frame faster
    const sp=(0.55+0.9*r())*pf;
    const vwx=(wx*V+windS)*sp, vwy=wy*V*sp, vfy=F.fall*H*pf*(0.6+0.8*r());
    const P=diag/Math.max(Math.hypot(vwx,vwy+vfy), 0.02*H);
    const ph=r()*P, gph=z*1.7;                         // far flakes feel the gust a beat later
    const A1=turbA*pf*(0.4+1.2*r()), f1=TAU*(0.35+0.5*r()), f2=TAU*(0.9+1.1*r()), p1=r()*TAU, p2=r()*TAU, p3=r()*TAU;
    const q=r(), size=F.size*H*inst.sizeMul*(1+(1.6-2.2*z)*depth)*(0.5+q*q*q*1.8);   // mostly fine, a few big ones (real snowfall)
    const shade=0.75+0.25*r();
    const k=Math.floor((t+ph)/P), tc=k*P-ph, tm=tc+P/2, cyc=(t-tc)/P;
    const rc=rng(seedMix(inst.seed,i*7919+k,inst.id*13+3));
    const mx=x0+rc()*bw, my=y0+rc()*bh, Gm=G(tm,gph);
    const at=(tt,out)=>{
      const gw=G(tt,gph)-Gm, bx=mx+vwx*gw, by=my+vwy*gw+vfy*(tt-tm);
      // per-flake flutter plus a shared swirl field, so neighbouring flakes move together
      out[0]=bx + A1*(Math.sin(f1*tt+p1)+0.4*Math.sin(f2*tt+p2)) + turbA*1.4*Math.sin(by/(0.23*H)+tt*0.9+g1);
      out[1]=by + A1*0.7*(Math.cos(f1*1.3*tt+p2)+0.4*Math.sin(f2*tt+p3)) + turbA*1.1*Math.sin(bx/(0.31*H)-tt*0.7+g2);
    };
    at(t-sh,pos); at(t+sh,pos2);
    let al=0.9*e*shade*Math.min(1,cyc/0.1,(1-cyc)/0.1);
    const cx=(pos[0]+pos2[0])/2, cy=(pos[1]+pos2[1])/2;
    if (!inst.full) al*=Math.max(0,Math.min(1,(cx-x0)/edge,(x1-cx)/edge,(cy-y0)/edge,(y1-cy)/edge));
    if (al<=0.004) continue;
    // near flakes defocus: bigger, softer, same energy
    const blur=depth*Math.max(0,0.25-z)*4, hw=size*(1+blur*2.5);
    al/=(1+blur*2.5)*(1+blur*2.5)/(1+blur);
    const ax=(pos2[0]-pos[0])/2, ay=(pos2[1]-pos[1])/2;
    const hwp=hw*rscale, L=Math.hypot(ax,ay)*rscale;
    if (hwp<0.75) al*=(hwp/0.75)*(hwp/0.75);          // sub-pixel flakes: keep their energy, not their size
    al*=Math.max(hwp,0.75)/(L+Math.max(hwp,0.75));     // motion blur spreads the same energy along the streak
    spr.push(cx,cy,ax,ay,hw,blur>0.3?1:0,0,0,cr*al,cg*al,cb*al,al);
  }
}

/* smooth 1D value noise per streamer: n(j, x) in 0..1 */
const h01=(a,b)=>seedMix(a,b,0x5bd1)/4294967296;
function vn(j,x){ const i=Math.floor(x), f=x-i, u=f*f*(3-2*f); return h01(j,i)*(1-u)+h01(j,i+1)*u; }
const fbm1=(j,x)=>0.65*vn(j,x)+0.35*vn(j+7919,x*2.3+3.1);

/* Spindrift grains: fine snow particles streaming off the ridge. Most grains ride one of a few dozen streamers
   whose cross-wind path depends on emission time, so each streamer is a wavy filament that travels downwind like
   a flag. Grains only exist once emitted, so the plume grows out of the ridge after the start frame and blows
   away after the end frame. */
export function pushDriftGrains(inst, pr, frame, spr, rscale, tint){
  const D=pr.drift, pl=state.plate, W=pl.w, H=pl.h, fps=state.fps;
  if (inst.intensity<=0 || frame<inst.frame) return;
  const speed=Math.max(D.speed*H*inst.speedMul, 0.005*H), len=inst.length*H, t=frame/fps, sh=0.25/fps;
  const tStart = inst.frame<=pl.first ? -1e9 : inst.frame/fps, end=inst.frame+inst.duration, tEnd = end>=pl.last ? 1e9 : end/fps;
  const a=inst.angle*DEG, wx=Math.sin(a), wy=-Math.cos(a), nx=-wy, ny=wx;
  const rr=inst.ridge*DEG, lx=Math.cos(rr), ly=-Math.sin(rr), half=inst.width*H*0.5;
  const detail=Math.min(2,inst.curl/30), spread=D.spread*inst.spreadMul, thick=D.thick*H;
  const M=Math.max(6, Math.round(12+40*inst.width)), loose=0.35/(0.5+detail);   // loose share falls as wisp detail rises
  const n=Math.min(40000, Math.round(D.grains*inst.intensity*(0.4+inst.width)));
  const col=lin(inst.color), cr=col[0]*tint[0], cg=col[1]*tint[1], cb=col[2]*tint[2];
  const S=inst.seed*31+inst.id, life=len*1.8;
  const pA=[0,0], pB=[0,0];
  for (let i=0;i<n;i++){
    const r=rng(seedMix(inst.seed,i,inst.id*13+11));
    const sf=0.75+0.5*r(), T=life/(speed*sf), ph=r()*T;
    const k=Math.floor((t+ph)/T), age=t+ph-k*T, te=t-age;           // this grain's emission time
    if (te<tStart || te>tEnd) continue;
    const rc=rng(seedMix(inst.seed,i*7919+k,inst.id*13+12));
    const isLoose=rc()<loose, j=Math.floor(rc()*M);
    const s0=isLoose ? rc()*2-1 : h01(S+j,1)*2-1;                    // where on the ridge it left from
    const fan=(isLoose ? rc()*2-1 : h01(S+j,2)*2-1)*spread;         // streamers diverge a little
    const g=(rc()+rc()+rc()-1.5)*1.4;                               // spread around the streamer centreline
    const puff=isLoose ? 0.6 : 0.25+1.1*fbm1(S+j*3+1, te*0.6);       // gusts travel down each streamer
    const size=D.grainSize*H*inst.sizeMul*(0.6+0.9*rc()*rc());
    const at=(ag,out)=>{
      const d=speed*sf*ag*(1-0.25*ag*speed*sf/life);                // grains ease off as they leave the ridge
      const sig=thick+spread*d;
      // centreline wobble depends on emission time, so the wave shape rides downwind with the grains
      const wob=isLoose ? 0 : (fbm1(S+j*3, te*0.35+d/len*0.8)-0.5)*2*sig*1.8*detail;
      const c=wob+fan*d+g*(thick*0.25+sig*(isLoose?0.9:0.12));
      const lift=inst.lift*0.4*d*d/len;
      out[0]=inst.x+lx*half*s0+wx*d+nx*c;
      out[1]=inst.y+ly*half*s0+wy*d+ny*c-lift;
      return d;
    };
    const d=at(age,pA); at(age+sh*2,pB);
    const env=Math.min(1,d/(0.05*len+1))*Math.exp(-d/(len*0.55))*Math.max(0,Math.min(1,(len*1.7-d)/(len*0.5)));
    let al=D.grainAlpha*env*puff;
    if (al<=0.003) continue;
    const cx=(pA[0]+pB[0])/2, cy=(pA[1]+pB[1])/2;
    if (cx<-20 || cy<-20 || cx>W+20 || cy>H+20) continue;
    const ax=(pB[0]-pA[0])/2, ay=(pB[1]-pA[1])/2, hwp=size*rscale, L=Math.hypot(ax,ay)*rscale;
    if (hwp<0.75) al*=(hwp/0.75)*(hwp/0.75);
    al*=Math.max(hwp,0.75)/(L+Math.max(hwp,0.75));
    al=Math.min(al,1);
    spr.push(cx,cy,ax,ay,size,0,0,0,cr*al,cg*al,cb*al,al);
  }
}

/* Uniforms for one spindrift haze pass (FS.drift), or null when nothing is visible this frame. */
export function driftUniforms(inst, pr, frame, T, lighting){
  const D=pr.drift, pl=state.plate, H=pl.h, fps=state.fps;
  if (inst.haze<=0 || frame<inst.frame) return null;
  const speed=D.speed*H*inst.speedMul, len=inst.length*H, end=inst.frame+inst.duration;
  // the plume front grows out of the ridge at the wind speed, and the tail blows away after the end frame
  const front = inst.frame<=pl.first ? 1e7 : speed*(frame-inst.frame)/fps;
  const tail = (end>=pl.last || frame<=end) ? -1e7 : speed*(frame-end)/fps;
  if (tail>len*1.6) return null;
  const a=inst.angle*DEG, rr=inst.ridge*DEG, sr=rng(seedMix(inst.seed,0,inst.id*13+7));
  return {uRes:[T.w,T.h], uPlate:[pl.w,H], uC:[inst.x,inst.y], uW:[Math.sin(a),-Math.cos(a)], uRidge:[Math.cos(rr),-Math.sin(rr)],
    uHalf:inst.width*H*0.5, uLen:len, uThick:D.thick*H, uSpread:D.spread*inst.spreadMul, uLift:inst.lift,
    uT:frame/fps, uSpeed:speed, uFront:front, uTail:tail, uAmt:D.amount*inst.haze, uDetail:inst.curl/30*0.35,
    uAmb:lighting.amb, uSinE:lighting.sinE, uAlb:lin(inst.color), uKey:lighting.key, uSky:lighting.sky,
    uSeed:[sr()*97, sr()*97, sr()*97]};
}
