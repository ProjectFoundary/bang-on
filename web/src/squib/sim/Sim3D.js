import { F16, P, R16, RG16, freeFBO, gl, makeDouble, makeFBO, makeFBO2, pass, program } from '../gl/context.js';
import { FS3, FS_RAYMARCH } from '../gl/shaders3d.js';
import {PressureSolver} from '../gl/pressure.js';
import { camAt } from '../io/camera.js';
import { PRESETS, SIMULATED } from '../presets.js';
import { app, state } from '../state.js';
import { detailNoise } from '../gl/detail-noise.js';
import { fineCloud,fineSettings } from './fines.js';
import { FineParticles3D,supportsFineParticles } from './fine-particles3d.js';
import { SmokeParticles3D,usesSmokeParticles,smokeParticleSettings,smokeParticleBlend } from './smoke-particles3d.js';
import {usesBakedSmoke} from '../baked/library.js';
import {BakedVolume} from '../baked/Volume.js';
import { DEG, FLOW_PERIOD3, SUBSTEPS, lin } from '../util.js';
import { emitFireball3D, emitImpact3D, emitSmoke3D, spawnParticles3D, stepParticles3D } from './emitters3d.js';

/* =========================================================================
   3D volume sim (opt-in). One volume per effect (Vol), each a world-space box
   sized and centred on its effect, managed by Sim3D, which has the same
   interface as the 2D Sim: goto(), invalidateFrom(), invalidateAll(), destroy().
   The 2D path never touches this module unless state.cam.mode is '3d', and its
   shaders only compile on first use.
   ========================================================================= */
const SNAP_EVERY3 = 6, SNAP_BUDGET = 700*1024*1024;
const F32=()=>[gl.RGBA32F,gl.RGBA,gl.FLOAT];
const pointField=k=>['finePos','fineVel','smokePos','smokeMass'].includes(k);
const fullFloat=k=>k==='birth'||pointField(k);
/* 3D look tuning (tweakable from devtools via state.cam.tune while dialling in) */
export const TUNE3 = {jacobi:20, curl:0.8, turb:1.8, sharp:0};   // dialled in on the plume (was grenade) against the 2D look
const tune = () => ({...TUNE3, ...(state.cam.tune||{})});
let P3 = null;
function programs(){
  if (!P3){ P3={}; for (const k in FS3) P3[k]=program(FS3[k]); P3.raymarch=program(FS_RAYMARCH); P3.raymarchFines=program(FS_RAYMARCH.replace('#version 300 es','#version 300 es\n#define ANALYTIC_FINES')); }
  return P3;
}
function makeFineDouble(w,h){
  const [read,readFlow]=makeFBO2(w,h,F16()),[write,writeFlow]=makeFBO2(w,h,F16());
  return {read,readFlow,write,writeFlow,swap(){
    [this.read,this.write]=[this.write,this.read];[this.readFlow,this.writeFlow]=[this.writeFlow,this.readFlow];
  }};
}

export const is3D = () => !!(state.cam && state.cam.mode==='3d' && app.sim3d);
export const activeSim = () => is3D() ? app.sim3d : app.sim;
const volInstances = () => state.instances.filter(i=>i.enabled && i.w && SIMULATED(PRESETS[i.preset].kind));

/* The box for one effect, in world units. R = world size of one plate height at the effect. Dust sprays along the
   aim direction, so impact boxes lean that way; smoke gets headroom to rise. */
export function boxFor(inst){
  // Bang On's depth collider adapter supplies a validated finite camera-space box.
  if(inst.volumeFXBox)return {min:inst.volumeFXBox.min.slice(),size:inst.volumeFXBox.size.slice()};
  const C=state.cam, pr=PRESETS[inst.preset], D=pr.smoke?.draft;
  if (D){
    // a plume is a tall column: as tall as its draft (which grows with sqrt(scale); the lid fade starts at 85%) and
    // about as wide as it spreads late (with scale), so the voxels go across the smoke instead of into empty sides
    // (at most 0.8 x as wide as tall, so large scales don't blow up the voxel count)
    const Hw=inst.wscale*(C.boxMul??1), s=inst.scale, up=D.height*Hw*Math.sqrt(s)*1.2, half=Math.min(D.boxW*Hw*s, 0.4*up);
    const y0=Math.max(C.groundY, inst.w[1]-pr.smoke.radius*Hw*s*3);
    return {min:[inst.w[0]-half, y0, inst.w[2]-half], size:[2*half, inst.w[1]-y0+up, 2*half]};
  }
  const R=inst.wscale*Math.sqrt(Math.max(0.25,inst.scale))*(C.boxMul??1), smoke=pr.kind==='smoke';
  if (pr.fire){   // fireball: a ball ~0.35 H across that climbs ~0.65 H before it clears
    const y0=Math.max(C.groundY, inst.w[1]-0.25*R);
    return {min:[inst.w[0]-0.3*R, y0, inst.w[2]-0.3*R], size:[0.6*R, inst.w[1]-y0+0.9*R, 0.6*R]};
  }
  if (inst.preset==='concrete' && (inst.facing||0)>.5){
    // A wall puff is shallow: the old near-cubic box spent most voxels on empty depth and headroom.
    // Keep the full fall curtain and haze width, with the same longest-axis voxel resolution.
    const view=camAt(inst.frame), length=Math.hypot(view.right[0],view.right[2]);
    const rx=length>1e-4?Math.abs(view.right[0])/length:1, rz=length>1e-4?Math.abs(view.right[2])/length:0;
    const hx=(.34*rx+.23*rz)*R, hz=(.34*rz+.23*rx)*R, y0=Math.max(C.groundY,inst.w[1]-.85*R);
    return {min:[inst.w[0]-hx,y0,inst.w[2]-hz],size:[2*hx,inst.w[1]-y0+.30*R,2*hz]};
  }
  // impacts facing the lens (wall hits) sit centred, with less headroom and more room below for the falling grit curtain
  const f=smoke ? 0 : Math.min(1,inst.facing||0);
  const half=smoke ? 0.42*R : 0.45*R, up=smoke ? 1.15*R : 0.9*R*(1-0.4*f);   // sized to the plume, not the frame: more voxels across the smoke
  const c=inst.w.map((v,a)=>v+inst.wdir[a]*(smoke?0:0.15*R*(1-f)));
  const y0=Math.max(C.groundY, c[1]-half*(1+0.6*f));
  return {min:[c[0]-half, y0, c[2]-half], size:[2*half, Math.max(c[1]-y0,0)+up, 2*half]};
}

// Busy shots reserve fine voxels for the plume. This decision uses the setup, not the current
// frame or export mode: no resolution changes while playing, and preview/export remain identical.
function resolutionFor(inst){
  const C=state.cam;
  if (inst.preset==='metal') return Math.min(C.res,64);
  if (C.adaptive!==false && volInstances().length>2){
    if (PRESETS[inst.preset].dust) return Math.min(C.res,inst.preset==='concrete'?64:80);
    if (PRESETS[inst.preset].smoke?.draft) return Math.min(C.res,96);
  }
  return C.res;
}

function substepsFor(inst){
 const C=state.cam,n=Math.max(1,Math.min(4,Math.round(C.substeps??PRESETS[inst.preset].smoke?.volume?.substeps??SUBSTEPS)));
 return C.adaptive!==false&&volInstances().length>2?Math.min(2,n):n;
}

const clonePs=ps=>({debris:ps.debris.map(p=>({...p})), sparks:ps.sparks.map(p=>({...p}))});
function scalarScale(inst){
  const pr=PRESETS[inst.preset];
  if(pr.dust){
    const requested=Math.max(1,Math.min(2,state.cam.dustDensityScale??pr.dust.densityScale??1));
    return state.cam.adaptive!==false&&volInstances().length>2?Math.min(1.25,requested):requested;
  }
  return pr.smoke&&!pr.smoke.fire?Math.max(1,Math.min(2,state.cam.densityScale??pr.smoke.volume?.densityScale??1)):1;
}
function cachedDetail(inst){
  const pr=PRESETS[inst.preset];
  return !!(pr.smoke&&!pr.smoke.fire&&(state.cam.cachedDetail??pr.smoke.volume?.cachedDetail??false));
}
function birthDetail(inst){return cachedDetail(inst)&&!!(state.cam.birthDetail??PRESETS[inst.preset].smoke?.volume?.birthDetail);}
function particleFines(inst){const F=fineSettings(PRESETS[inst.preset].dust?.fines,true);return !!(F.analytic&&F.particles&&supportsFineParticles());}
function atlas(box,vox){
  const dim=box.size.map(s=>Math.max(8,Math.round(s/vox))),tx=Math.ceil(Math.sqrt(dim[2])),ty=Math.ceil(dim[2]/tx);
  return {vox,dim,tiles:[tx,ty],w:dim[0]*tx,h:dim[1]*ty};
}

class Vol{
  constructor(inst, P, budget){
    const C=state.cam, b=boxFor(inst), coarseVox=Math.max(...b.size)/resolutionFor(inst);
    const coarse=atlas(b,coarseVox),fine=atlas(b,coarseVox/scalarScale(inst));
    this.coarse=coarse;this.inst=inst; this.P=P; this.vox=fine.vox; this.boxMin=b.min.slice(); this.box=b;
    this.key=Vol.keyFor(inst);
    this.dim=fine.dim;this.tiles=fine.tiles;this.aw=fine.w;this.ah=fine.h;
    const mx=gl.getParameter(gl.MAX_TEXTURE_SIZE);
    if (Math.max(this.aw,this.ah,coarse.w,coarse.h)>mx) throw new Error(`volume too large for this GPU (${this.aw}×${this.ah} atlas)`);
    const W=this.aw, H=this.ah;
    this.vel=makeDouble(coarse.w,coarse.h,F16()); this.dye=makeDouble(W,H,F16()); this.attr=makeDouble(W,H,F16()); this.p=makeDouble(coarse.w,coarse.h,R16());
    const pr=PRESETS[inst.preset],smokeParticles=usesSmokeParticles(inst); this.hasFire=!!(pr.fire || pr.smoke?.fire); this.fields=['vel','dye','attr','p'];
    this.material=pr.dust?makeDouble(W,H,RG16()):null;
    if(this.material)this.fields.push('material');
    // Live smoke carries density with particles. Its renderer needs no
    // displacement/noise maps, so avoid their transport and snapshot storage.
    this.flowA=smokeParticles?null:makeDouble(W,H,F16()); this.flowB=smokeParticles?null:makeDouble(W,H,F16());
    if(!smokeParticles)this.fields.push('flowA','flowB');
    if (this.hasFire) this.fields.push('heat');
    if (this.hasFire) this.heat=makeDouble(W,H,R16());   // fire heat x density, only for effects that burn
    this.fines=pr.dust?.fines?.analytic?makeFineDouble(W,H):null;this.combined=this.fines?makeFBO(W,H,F16()):null;this.renderKey=null;
    this.lightGrid=(C.lightFine??pr.smoke?.volume?.lightFine)?fine:coarse;
    this.div=makeFBO(coarse.w,coarse.h,R16()); this.curl=makeFBO(coarse.w,coarse.h,F16()); this.light=makeFBO(this.lightGrid.w,this.lightGrid.h,F16());
    this.velScratch=makeFBO(coarse.w,coarse.h,F16());
    this.tA=makeFBO(W,H,F16()); this.tB=makeFBO(W,H,F16());   // MacCormack scratch
    this.carrier=fine.vox!==coarse.vox?makeFBO(W,H,F16()):null;
    this.optical=cachedDetail(inst)&&!smokeParticles?{dye:makeFBO(W,H,F16()),reductions:[],key:null}:null;
    if(this.optical){
      // Broad density pockets need persistent material coordinates. The short
      // renewable maps remain useful for fine detail but visibly pulse at this scale.
      this.macroFlow=makeDouble(W,H,F16());this.fields.push('macroFlow');
      let w=W,h=H;
      do{w=Math.max(1,Math.ceil(w/2));h=Math.max(1,Math.ceil(h/2));
        this.optical.reductions.push(makeFBO(w,h,[gl.RG32F,gl.RG,gl.FLOAT],gl.NEAREST));
      }while(w>1||h>1);
    }
    this.birth=birthDetail(inst)&&!smokeParticles?makeDouble(W,H,F32()):null;
    if(this.birth)this.fields.push('birth');
    this.fineParticles=particleFines(inst)?new FineParticles3D(this):null;
    if(this.fineParticles){
      this.finePos=this.fineParticles.position;this.fineVel=this.fineParticles.velocity;this.fields.push('finePos','fineVel');
      if(!P.forcesFines)P.forcesFines=program(FS3.forces.replace('#version 300 es','#version 300 es\n#define PARTICLE_FINES'));
    }
    this.smokeParticles=smokeParticles?new SmokeParticles3D(this):null;
    if(this.smokeParticles){this.smokePos=this.smokeParticles.position;this.smokeMass=this.smokeParticles.mass;this.fields.push('smokePos','smokeMass');}
    this.snaps=new Map(); this.pool=[]; this.frame=null; this.valid=false; this.startFrame=null; this.lightFrame=null;
    this.parts={debris:[],sparks:[]};   // world-space debris and sparks (impacts)
    this.snapshotBytes=this.fields.reduce((n,k)=>n+this[k].read.w*this[k].read.h*(fullFloat(k)?16:k==='p'||k==='heat'?2:k==='material'?4:8),0);
    this.setBudget(budget);
  }
  /* rebuild when anything that shapes the box or grid changes */
  static keyFor(i){ const C=state.cam,F=fineSettings(PRESETS[i.preset].dust?.fines,true),S=smokeParticleSettings(i),particles=usesSmokeParticles(i); return [i.w,i.wdir,i.wscale,i.scale,i.facing,resolutionFor(i),scalarScale(i),cachedDetail(i),birthDetail(i),C.birthRate,C.rk2,PRESETS[i.preset].smoke?.volume?.rk2,C.lightFine??PRESETS[i.preset].smoke?.volume?.lightFine,particleFines(i),F.particleCount,particles,particles?[C.smokeParticleCount??S.particleCount,C.smokeParticleLife??S.particleLife,state.fps]:null,C.refineTurb,substepsFor(i),C.multigrid,C.pressureCycles,F.particleCountByGrid,C.projectAfter,PRESETS[i.preset].smoke?.volume?.projectAfter,PRESETS[i.preset].smoke?.volume?.flowPeriod,C.boxMul,C.groundY].map(v=>JSON.stringify(v)).join('|'); }
  get substeps(){return substepsFor(this.inst);}
  get Href(){ return this.inst.wscale/this.vox; }                   // voxels per plate height at the effect
  get flowPeriod(){return PRESETS[this.inst.preset].smoke?.volume?.flowPeriod??FLOW_PERIOD3;}
  setBudget(budget){
    this.maxSnaps=Math.max(0,Math.floor(budget/this.snapshotBytes));
    while(this.pool.length+this.snaps.size>this.maxSnaps){
      let s=this.pool.pop();
      if(!s){const frame=[...this.snaps.keys()].at(-1);s=this.snaps.get(frame);this.snaps.delete(frame);}
      for(const name of this.fields)freeFBO(s[name]);
    }
  }
  u(coarse=false){const c=this.coarse;return {uEdgeStep:SUBSTEPS/this.substeps,uDim:coarse?c.dim:this.dim,uTiles:coarse?c.tiles:this.tiles,uBoxMin:this.boxMin,uVox:coarse?c.vox:this.vox,uGround:state.cam.groundY,
    uRk2:{i:(state.cam.rk2??PRESETS[this.inst.preset].smoke?.volume?.rk2??false)?1:0},
    uVelocityDim:c.dim,uVelocityTiles:c.tiles,uVelocityVox:c.vox,uFieldDim:this.dim,uFieldTiles:this.tiles,uFieldVox:this.vox,
    uRefineTurb:coarse||c.vox===this.vox?0:(state.cam.refineTurb??PRESETS[this.inst.preset].smoke?.volume?.refineTurb??0)*this.inst.turb*this.Href,
    uRefineK:.9*this.vox/Math.max(.07*this.inst.wscale,9*this.vox),uRefinePhase:(this.inst.seed%1024)*.017,uRefineNoise:detailNoise()};}
  destroy(){
    this.pressureSolver?.destroy();
    this.fineParticles?.destroy();
    this.smokeParticles?.destroy();
    if(this.optical){freeFBO(this.optical.dye);for(const f of this.optical.reductions)freeFBO(f);}
    if(this.fines){freeFBO(this.fines.read);freeFBO(this.fines.write);freeFBO(this.fines.readFlow);freeFBO(this.fines.writeFlow);freeFBO(this.combined);}
    for (const k of this.fields){ freeFBO(this[k].read); freeFBO(this[k].write); }
    for (const f of [this.div,this.curl,this.light,this.velScratch,this.tA,this.tB,this.carrier]) freeFBO(f);
    for (const s of this.snaps.values()) this.pool.push(s);
    for (const s of this.pool) for (const k of this.fields) freeFBO(s[k]);
  }
  clearTex(f){ gl.bindFramebuffer(gl.FRAMEBUFFER,f.fbo); gl.clearColor(0,0,0,0); gl.clear(gl.COLOR_BUFFER_BIT); }
  startAt(){ return Math.floor(this.inst.frame)-2; }
  reset(){
    if(this.optical)this.optical.key=null;
    this.renderKey=null;
    for (const k of this.fields){ this.clearTex(this[k].read); this.clearTex(this[k].write); }
    this.fineParticles?.init();
    this.startFrame=this.startAt(); this.frame=this.startFrame; this.valid=true; this.lightFrame=null;
    this.parts={debris:[],sparks:[]};
  }
  invalidateFrom(keep){
    if(this.optical)this.optical.key=null;
    this.renderKey=null;
    for (const [F,s] of this.snaps) if (F>keep){ this.snaps.delete(F); this.pool.push(s); }
    if (this.frame===null || this.frame>keep) this.valid=false;
    this.lightFrame=null;
  }
  blit(src,dst){
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER,src.fbo); gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER,dst.fbo);
    gl.blitFramebuffer(0,0,src.w,src.h,0,0,dst.w,dst.h,gl.COLOR_BUFFER_BIT,gl.NEAREST);
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER,null); gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER,null);
  }
  save(){
    if (this.snaps.size>=this.maxSnaps) return;   // memory budget: later frames just re-simulate from the last snapshot
    const s=this.pool.pop() || Object.fromEntries(this.fields.map(k=>[k,makeFBO(this[k].read.w,this[k].read.h,fullFloat(k)?F32():k==='p'||k==='heat'?R16():k==='material'?RG16():F16(),pointField(k)?gl.NEAREST:gl.LINEAR)]));
    for (const k of this.fields) this.blit(this[k].read,s[k]);
    s.parts=clonePs(this.parts); s.frame=this.frame; this.snaps.set(this.frame,s);
  }
  restore(s){ if(this.optical)this.optical.key=null;this.renderKey=null;for (const k of this.fields) this.blit(s[k],this[k].read); this.parts=clonePs(s.parts); this.frame=s.frame; this.valid=true; this.lightFrame=null; }
  /* returns false while the effect hasn't started (nothing to draw) */
  goto(f){
    if (f<=this.startAt()) return false;
    if (this.startAt()!==this.startFrame){ this.invalidateFrom(-Infinity); this.startFrame=null; }
    if (this.valid && this.frame===f) return true;
    if (!this.valid || this.frame>f || this.startFrame===null){
      let best=null; for (const [F,s] of this.snaps) if (F<=f && (!best||F>best.frame)) best=s;
      if (best && this.startFrame!==null) this.restore(best); else { this.snaps.forEach(s=>this.pool.push(s)); this.snaps.clear(); this.reset(); }
    }
    while (this.frame<f){
      this.step();
      if ((this.frame-this.startFrame)%SNAP_EVERY3===0 && !this.snaps.has(this.frame)) this.save();
    }
    return true;
  }
  step(){
    const N=this.substeps, fps=state.fps, dt=1/(fps*N), inst=this.inst, pr=PRESETS[inst.preset];
    const impact=this.fineParticles&&pr.dust?.particleCoreAmount!=null?{...pr,dust:{...pr.dust,amount:pr.dust.particleCoreAmount}}:pr;
    for (let k=0;k<N;k++){
      const tA=this.frame+k/N, tB=this.frame+(k+1)/N, sub=this.frame*N+k, splats=[];
      if (pr.kind==='impact'){
        const T0=inst.frame-0.25;   // same spawn time as 2D: the impact frame shows the flash plus the start of the burst
        if (T0>=tA && T0<tB) spawnParticles3D(inst,pr,this.parts);
        emitImpact3D(inst,impact,tA,tB,sub,splats); emitFireball3D(inst,pr,tA,tB,sub,splats); stepParticles3D(inst,pr,this.parts,dt,splats);
      } else emitSmoke3D(inst,pr,tA,tB,sub,splats);
      if(N!==SUBSTEPS)for(const s of splats)if(s.d>0)s.d*=SUBSTEPS/N;
      this.fluid(dt,splats,sub*dt,sub);
    }
    this.frame++;
  }
  /* splats in batches of 32 per pass (0 velocity, 1 dye, 2 attributes, 3 fire heat, 4 material) */
  splat(list,f,mode,offset=[0,0,0],time=0){
    const coarse=mode===0,P=this.P,vox=coarse?this.coarse.vox:this.vox,U=this.u(coarse);
    for (let b=0;b<list.length;b+=32){
      const n=Math.min(32,list.length-b), A={uP:[],uA:[],uB:[],uC:[],uE:[],uM:[]};
      for (let i=0;i<32;i++){
        const s=list[b+Math.min(i,n-1)];
        // sources under 2 voxels can't push the fluid: widen them and thin them so the total smoke is unchanged
        const r=s.r/vox, R=Math.max(r,2.0), thin=Math.pow(r/R,3);
        A.uP.push(...s.p.map((v,a)=>(v-this.boxMin[a])/vox), R);
        A.uA.push(...s.v.map(v=>v/vox), s.temp); A.uB.push(s.vw, s.d*thin, (s.heat||0)*s.d*thin, s.tempOnly?1:0); A.uC.push(...s.col, 0);
        A.uE.push(s.curl, s.turb, s.rate, -s.linger);
        const settle=s.settle??(PRESETS[this.inst.preset].dust?.settle??0)*(this.inst.gravityMul??1);
        A.uM.push(settle,s.terminalSettle??settle,0,0);
      }
      const age=Math.max(0,time-this.inst.frame/state.fps),rate=state.cam.birthRate??1;
      const thermal=PRESETS[this.inst.preset].smoke?.volume??{},phase=(this.inst.seed%997)*.031;
      pass(P.splat,f.write,f.write.w,f.write.h,{...U,
        uBirthOrigin:this.inst.w.map((v,k)=>(v-this.boxMin[k])/vox),uBirthUnit:this.inst.wscale/vox,
        uBirthOffset:[.035,.047,-.031].map(v=>age*rate*v+(this.inst.seed%97)*.037),
        uSourceStep:SUBSTEPS/this.substeps,uThermalBreakup:(thermal.thermalBreakup??0)*this.inst.turb/.32,
        uThermalK:vox/(this.inst.wscale*(thermal.thermalScale??.045)),
        uThermalOffset:[phase+age*.41,phase*.7-age*.63,phase*1.3+age*.27],
        uFragmentOffset:offset,uBreakup:PRESETS[this.inst.preset].breakup3d??0,uBreakupK:vox/(this.inst.wscale*.014),uN:{i:n}, uP:{v4:A.uP}, uA:{v4:A.uA}, uB:{v4:A.uB}, uC:{v4:A.uC}, uE:{v4:A.uE},uM:{v4:A.uM}, uSrc:f.read.tex, uMode:{i:mode}}); f.swap();
    }
  }
  splatFines(list){
    const f=this.fines,vox=this.vox,U=this.u();
    for(let b=0;b<list.length;b+=32){
      const n=Math.min(32,list.length-b),A={uP:[],uC:[],uM:[]};
      for(let i=0;i<32;i++){
        const s=list[b+Math.min(i,n-1)],r=s.r/vox,R=Math.max(r,2),thin=Math.pow(r/R,3);
        A.uP.push(...s.p.map((v,a)=>(v-this.boxMin[a])/vox),R);
        A.uC.push(...s.col,s.d*thin);A.uM.push(...s.airMove.map(v=>v/vox),0);
      }
      pass(this.P.fineSplat,f.write,this.aw,this.ah,{...U,uN:{i:n},uP:{v4:A.uP},uC:{v4:A.uC},uM:{v4:A.uM},uSrc:f.read.tex,uMotion:f.readFlow.tex});f.swap();
    }
    pass(this.P.shapeFines,f.write,this.aw,this.ah,{...U,uDetailNoise:detailNoise(),uSrc:f.read.tex,uMotion:f.readFlow.tex,
      uBreakup:PRESETS[this.inst.preset].breakup3d??0,uBreakupK:1/(this.Href*.014)});f.swap();
  }
  fluid(dt,splats,time,sub){
    if(this.optical){this.optical.key=null;this.lightFrame=null;}
    const P=this.P, W=this.aw, H=this.ah, S=state.scene, U={...this.u(),uRefineTime:time}, Href=this.Href, K=tune();
    const C=this.coarse,VU=this.u(true),VH=this.inst.wscale/C.vox;
    // velocity only from splats that push (or heat), dye and attributes only from those that carry smoke:
    // a smoke draft adds ten temperature-only splats that would otherwise be looped over in every pass
    const dye=splats.filter(s=>s.d>0);
    this.splat(splats.filter(s=>s.vw>0),this.vel,0,[0,0,0],time); this.splat(dye,this.dye,1); this.splat(dye,this.attr,2);
    if(this.birth)this.splat(dye,this.birth,5,[0,0,0],time);
    if(this.material)this.splat(dye,this.material,4);
    if (this.hasFire) this.splat(dye.filter(s=>s.heat>0),this.heat,3);
    pass(P.curl,this.curl,C.w,C.h,{...VU,uVel:this.vel.read.tex});
    const inst=this.inst,fp=this.fineParticles,age=time-inst.frame/state.fps;
    fp?.prepare(age,dt);
    if(fp)fp.depositForce(age+dt*.5);
    const fineF=fp?.settings();
    pass(fp?P.forcesFines:P.forces,this.vel.write,C.w,C.h,{...VU,uVel:this.vel.read.tex,uCurl:this.curl.tex,uDye:this.dye.read.tex,uAttr:this.attr.read.tex,
      uFineDensity:fp?fp.forceDensity.tex:this.dye.read.tex,uFineAir:[fineF?.particleCurl??inst.curl,fineF?.particleTurb??inst.turb],
      uDt:dt,uTime:time,uTurbK:0.2*VH*K.turb,uCurlK:VH/216*K.curl,uNoiseK:1/(0.22*VH),uCurlPotential:K.curlNoise??0,uBuoy:S.buoyancy*VH,uWindK:S.drag,uWind:[S.wind*VH,0,0]});
    this.vel.swap();
    const project=()=>{
      pass(P.divergence,this.div,C.w,C.h,{...VU,uVel:this.vel.read.tex});
      let pressure;
      if(state.cam.multigrid){
        this.pressureSolver??=new PressureSolver(C.dim,true);pressure=this.pressureSolver.solve(this.div.tex,state.cam.pressureCycles??1);
      }else{
        pass(P.scale,this.p.write,C.w,C.h,{...VU,uSrc:this.p.read.tex,uK:0.8});this.p.swap();
        for(let i=0;i<(this.inst.preset==='concrete'?Math.min(K.jacobi,12):K.jacobi);i++){pass(P.pressure,this.p.write,C.w,C.h,{...VU,uP:this.p.read.tex,uDiv:this.div.tex});this.p.swap();}
        pressure=this.p.read.tex;
      }
      pass(P.gradient,this.vel.write,C.w,C.h,{...VU,uP:pressure,uVel:this.vel.read.tex});this.vel.swap();
    };
    const projectAfter=state.cam.projectAfter??PRESETS[this.inst.preset].smoke?.volume?.projectAfter??false;
    if(!projectAfter)project();
    const kt=Math.pow(.6,dt);
    const dust=PRESETS[this.inst.preset].dust,maxFall=Math.max(dust?.initialSettle3d??dust?.initialSettle??0,dust?.settle??0,
      dust?.bulkFall?.up??0,(dust?.bulkFall?.gravity??0)/2,PRESETS[this.inst.preset].grit?.settle??.55)*(this.inst.gravityMul??1);
    const one=[1,1,1,1],B={...U,uMaterial:(this.material||this.dye).read.tex,uSettleDye:this.dye.read.tex,
      uHasMaterial:this.material?1:0,uSettle:0,uHref:Href,uDragAvg:-Math.expm1(-2*dt)/(2*dt),uFallReach:Math.max(1,Math.ceil(maxFall*Href*dt)+1)};
    const VB={...B,...VU,uHref:VH};
    if (state.cam.mc!==false){   // MacCormack (on by default in 3D): plain step into scratch, then the correction pass
      pass(P.advect,this.velScratch,C.w,C.h,{...VB,uVel:this.vel.read.tex,uSrc:this.vel.read.tex,uDt:dt,uKeep:one,uScalar:{i:0}});
      // Bind every active sampler even in mode0: a retained sampler unit may
      // otherwise alias the new 3D carrier-noise sampler from a prior pass.
      pass(P.mc,this.vel.write,C.w,C.h,{...VB,uVel:this.vel.read.tex,uSrc:this.vel.read.tex,uFwd:this.velScratch.tex,uFwdD:this.dye.read.tex,uFwdA:this.attr.read.tex,uDt:dt,uKeep:[1,1,1,kt],uMode:{i:0}}); this.vel.swap();
    } else {
      pass(P.advect,this.vel.write,C.w,C.h,{...VB,uVel:this.vel.read.tex,uSrc:this.vel.read.tex,uDt:dt,uKeep:[1,1,1,kt],uScalar:{i:0}}); this.vel.swap();
    }
    if(projectAfter)project();
    // Prolong and perturb once per substep; all scalar/material passes share
    // this carrier rather than re-evaluating twelve noise taps independently.
    let fieldVelocity=this.vel.read.tex,FU=B;
    if(this.carrier){
      pass(P.refineCarrier,this.carrier,W,H,{...U,uVel:this.vel.read.tex});fieldVelocity=this.carrier.tex;
      FU={...B,uVelocityDim:this.dim,uVelocityTiles:this.tiles,uVelocityVox:this.vox,uRefineTurb:0};
    }
    if(state.cam.mc!==false){
      pass(P.advect,this.tA,W,H,{...FU,uVel:fieldVelocity,uSrc:this.dye.read.tex,uDt:dt,uKeep:one,uScalar:{i:1}});
      pass(P.advect,this.tB,W,H,{...FU,uVel:fieldVelocity,uSrc:this.attr.read.tex,uDt:dt,uKeep:one,uScalar:{i:1}});
      const m={...FU,uVel:fieldVelocity,uFwdD:this.tA.tex,uFwdA:this.tB.tex,uDt:dt};
      pass(P.mc,this.dye.write,W,H,{...m,uSrc:this.dye.read.tex,uFwd:this.tA.tex,uMode:{i:1}});
      pass(P.mc,this.attr.write,W,H,{...m,uSrc:this.attr.read.tex,uFwd:this.tB.tex,uMode:{i:2}});
    }else{
      const fu={...FU,uVel:fieldVelocity,uDye:this.dye.read.tex,uAttr:this.attr.read.tex,uDt:dt};
      pass(P.advectFade,this.dye.write,W,H,{...fu,uOut:{i:0}});
      pass(P.advectFade,this.attr.write,W,H,{...fu,uOut:{i:1}});
    }
    if(this.material)pass(P.material,this.material.write,W,H,{...FU,uVel:fieldVelocity,uDyeNew:this.dye.write.tex,uDt:dt});
    if(this.birth){
      pass(P.birth,this.birth.write,W,H,{...FU,uVel:fieldVelocity,uBirth:this.birth.read.tex,uDyeNew:this.dye.write.tex,uDt:dt});
      this.birth.swap();
    }
    this.smokeParticles?.step(dt,dye,time,sub,fieldVelocity,this.dye.write.tex);
    if (this.hasFire){   // after the dye pass: heat is written back against the new dye
      pass(P.heat,this.heat.write,W,H,{...FU,uVel:fieldVelocity,uDye:this.dye.read.tex,uHeat:this.heat.read.tex,uDyeNew:this.dye.write.tex,uDt:dt,uTime:time,uNoiseK:14/Href});
      this.heat.swap();
    }
    const nP=Math.max(2,Math.round(this.flowPeriod/dt));
    for (const [name,offset] of [['flowA',0],['flowB',nP>>1],...(this.macroFlow?[['macroFlow',null]]:[])]){
      const field=this[name];
      if(!field)continue;
      pass(P.flow,field.write,W,H,{...FU,uVel:fieldVelocity,uSrc:field.read.tex,uDt:dt,uReset:offset!==null&&!this.material&&(sub+offset)%nP===0?1:0}); field.swap();
    }
    this.dye.swap(); this.attr.swap();
    this.material?.swap();
    fp?.step(age,dt);
  }
  opticalField(frame){
    const O=this.optical,pr=PRESETS[this.inst.preset],ratio=this.coarse.vox/this.vox;
    const detail=(state.cam.detail??1)*(pr.detail3d??1),edge=pr.detailEdge3d??0;
    const macro=state.cam.opticalMacro??pr.smoke?.volume?.opticalMacro??0;
    const birthK=state.cam.birthK??pr.smoke?.volume?.birthK??45,birthMacro=state.cam.birthMacro!==false;
    const persistentFine=state.cam.persistentFine??pr.smoke?.volume?.persistentFine??false;
    const key=JSON.stringify([frame,this.inst,detail,edge,macro,birthK,birthMacro,persistentFine]);
    if(detail<=0){O.key=key;return this.dye.read.tex;}
    if(O.key!==key){
      const U=this.u(),P=this.P;
      pass(P.opticalDensity,this.tA,this.aw,this.ah,{...U,uDetailNoise:detailNoise(),uDye:this.dye.read.tex,
        uBirth:this.birth?.read.tex??this.dye.read.tex,uBirthMode:{i:this.birth?1:0},uBirthK:birthK,uBirthMacro:{i:birthMacro?1:0},uPersistentFine:{i:persistentFine?1:0},
        uFlowA:this.flowA.read.tex,uFlowB:this.flowB.read.tex,uMacroFlow:this.macroFlow.read.tex,uDetail:detail,uDetailEdge:edge,uMacro:macro,uDetailK:1/(2.5*ratio),
        uFlowWeight:.5-.5*Math.cos(2*Math.PI*((frame/this.flowPeriod/state.fps)%1))});
      let source=this.dye.read,w=this.aw,h=this.ah;
      for(let n=0;n<O.reductions.length;n++){
        const target=O.reductions[n];
        pass(P.opticalReduce,target,target.w,target.h,{...U,uOriginal:source.tex,uModified:this.tA.tex,uInputSize:[w,h],uFirst:{i:n===0?1:0}});
        source=target;w=target.w;h=target.h;
      }
      pass(P.opticalNormalize,O.dye,this.aw,this.ah,{...U,uOriginal:this.dye.read.tex,uModified:this.tA.tex,uMass:source.tex});O.key=key;
    }
    return O.dye.tex;
  }
  renderField(frame){
    if(this.smokeParticles){
      const physical=app.freezeEffectsFrame??frame,options=smokeParticleSettings(this.inst),key=JSON.stringify([physical,this.inst,state.cam.smokeParticleKernel??options.particleKernel??1,smokeParticleBlend(this.inst)]);
      if(this.renderKey!==key){this.smokeParticles.render((physical-this.inst.frame)/state.fps);this.renderKey=key;}
      return this.smokeParticles.density.tex;
    }
    if(this.optical&&state.cam.opticalActive!==false)return this.opticalField(frame);
    if(!this.fines)return this.dye.read.tex;
    const physicalFrame=app.freezeEffectsFrame??frame,key=JSON.stringify([physicalFrame,this.inst]);
    if(this.renderKey!==key){
      if(this.fineParticles)this.fineParticles.render(this.fines.read,(physicalFrame-this.inst.frame)/state.fps);
      else {
      gl.bindFramebuffer(gl.FRAMEBUFFER,this.fines.read.fbo);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT);
      const F=PRESETS[this.inst.preset].dust.fines;
      const cloud=fineCloud(this.inst,F,(physicalFrame-this.inst.frame)/state.fps,
        this.inst.wscale,true,camAt(this.inst.frame).right,!F.distribution);
      if(PRESETS[this.inst.preset].dust.fines.distribution){
        if(cloud.field){
          const f=this.fines,right=camAt(this.inst.frame).right;
          pass(this.P.fineDistribution,f.write,this.aw,this.ah,{...this.u(),...cloud.field,
            uFineOrigin:this.inst.w,uFinePlane:[right[0],right[2]],uFineUnit:this.inst.wscale*this.inst.scale});f.swap();
          pass(this.P.shapeFines,f.write,this.aw,this.ah,{...this.u(),uDetailNoise:detailNoise(),uSrc:f.read.tex,uMotion:f.readFlow.tex,
            uBreakup:PRESETS[this.inst.preset].breakup3d??0,uBreakupK:1/(this.Href*.014)});f.swap();
        }
      }else this.splatFines(cloud.parcels);
      }
      pass(P.combine,this.combined,this.aw,this.ah,{uA:this.dye.read.tex,uB:this.fines.read.tex,uMask:0});this.renderKey=key;
    }
    return this.combined.tex;
  }
  opacityK(){ return this.vox/(0.022*this.inst.wscale); }   // so a preset puff reads about as dense as it did in 2D
  render(T, cam, L, lighting, exporting){
    const P=this.P, S=state.scene, U=this.u(),dye=this.renderField(this.frame),C=this.coarse,G=this.lightGrid,ratio=C.vox/this.vox;
    const optical=!!this.smokeParticles||!!this.optical&&state.cam.opticalActive!==false;
    const lightKey=JSON.stringify([this.frame,S.density,L,this.smokeParticles?this.renderKey:optical?this.optical.key:null]);
    if (this.lightFrame!==lightKey){
      const lr=C.vox/G.vox;
      pass(P.lightVol,this.light,G.w,G.h,{...this.u(G===C),uDye:dye,uL:L,uK:G.vox/(.022*this.inst.wscale)*S.density*0.8,
        uSunSteps:{i:32+Math.ceil(Math.log(lr)/Math.log(1.1))},uSkySteps:{i:24+Math.ceil(Math.log(lr)/Math.log(1.12))}});
      this.lightFrame=lightKey;
    }
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);   // premultiplied over, far boxes first
    pass(this.fines?P.raymarchFines:P.raymarch,T.S,T.w,T.h,{...U,uDetailNoise:detailNoise(),uDetailLod:state.cam.noiseLod??0,uDye:dye,uHeat:(this.hasFire?this.heat:this.dye).read.tex,uHasFire:{i:this.hasFire?1:0},uFireK:26/this.Href,uLightV:this.light.tex,uLightDim:G.dim,uLightTiles:G.tiles,uLightVox:G.vox,uRes:[T.w,T.h],
      uCam:cam.pos,uRight:cam.right,uUp:cam.up,uBack:cam.back,uTanH:cam.tanH,uTanV:cam.tanV,uL:L,
      uStep:ratio*(exporting?0.4:(state.cam.fast?1.2:(state.cam.adaptive!==false && volInstances().length>2?0.9:0.6))),uK:this.opacityK(),uDens:S.density,uShadow:S.shadow,uAmb:lighting.amb,uGlow:S.glow,
      uKey:lighting.key,uSky:lighting.sky,uFire:lighting.fire,
      uAlbedo:lin(this.inst.color),uUniformAlbedo:{i:state.cam.uniformAlbedo?1:0},
      uFineFlow:this.fines?this.fines.readFlow.tex:this.dye.read.tex,
      uFineParticles:{i:this.fineParticles?1:0},
      uCoreDye:this.dye.read.tex,uCoreWarp:(PRESETS[this.inst.preset].detailCoreWarp3d??0)*ratio,
      uCoreErode:PRESETS[this.inst.preset].detailCoreErode3d??PRESETS[this.inst.preset].detailErode3d??.02,
      uCoreScale:PRESETS[this.inst.preset].detailCoreScale3d??1,uCoreShade:PRESETS[this.inst.preset].detailCoreShade3d??PRESETS[this.inst.preset].detailShade3d??1,
      uFlowA:this.flowA?.read.tex??this.dye.read.tex,uFlowB:this.flowB?.read.tex??this.dye.read.tex,uFlowWeight:this.material?1:0.5-0.5*Math.cos(2*Math.PI*((this.frame/this.flowPeriod/state.fps)%1)),
      uDetail:optical?0:(state.cam.detail??1.0)*(PRESETS[this.inst.preset].detail3d??1), uErode:PRESETS[this.inst.preset].detailErode3d??0.02,uDetailEdge:PRESETS[this.inst.preset].detailEdge3d??0,uDetailShade:PRESETS[this.inst.preset].detailShade3d??1,uDetailK:1/(2.5*ratio), uSharp:tune().sharp, uTime:this.frame/state.fps, uSmooth:{i:state.cam.smooth===false?0:1}});
    gl.disable(gl.BLEND);
  }
}

export class Sim3D{
  constructor(){ this.P=programs(); this.vols=new Map(); this.ms=0; this.live=0; }
  /* id -> {debris, sparks} of every live volume, for the particle render (world space) */
  get parts(){ const m=new Map(); for (const [id,v] of this.vols) if (v.valid && v.frame===this.frame) m.set(id,v.parts); return m; }
  get label(){
    const v=[...this.vols.values()];if(!v.length)return 'no 3D effects';
    if(v.some(x=>x.cached))return `${v.filter(x=>x.cached).length} cached · ${v.filter(x=>!x.cached).length} live volumes`;
    const flow=Math.max(...v.map(x=>Math.max(...x.coarse.dim))),density=Math.max(...v.map(x=>Math.max(...x.dim)));
    return `${v.length} vol${v.length>1?'s':''} · ${flow}${density!==flow?` flow / ${density} density`:''}${state.cam.adaptive!==false&&v.length>2?' budgeted':''}`;
  }
  /* one Vol per enabled 3D effect; rebuilt only when its box or grid would change */
  sync(){
    const want=new Set();
    const budget=SNAP_BUDGET/Math.max(1,volInstances().filter(i=>!usesBakedSmoke(i)).length);
    for (const inst of volInstances()){
      want.add(inst.id);
      const v=this.vols.get(inst.id);
      const cached=usesBakedSmoke(inst),key=cached?BakedVolume.keyFor(inst,resolutionFor(inst)):Vol.keyFor(inst);
      if (v && v.key===key && v.inst===inst){v.setBudget(budget);continue;}
      if (v) v.destroy();
      this.vols.set(inst.id,cached?new BakedVolume(inst,resolutionFor(inst)):new Vol(inst,this.P,budget));
    }
    for (const [id,v] of this.vols) if (!want.has(id)){ v.destroy(); this.vols.delete(id); }
  }
  goto(f){
    const t0=performance.now();
    this.sync(); this.frame=f; this.live=0;
    for (const v of this.vols.values()) if (v.goto(f)) this.live++;
    this.ms=performance.now()-t0;
  }
  invalidateFrom(keep){ for (const v of this.vols.values()) v.invalidateFrom(keep); }
  invalidateAll(){ this.invalidateFrom(-Infinity); }
  invalidateInst(id, keep){ const v=this.vols.get(id); if (v) v.invalidateFrom(keep); }
  destroy(){ for (const v of this.vols.values()) v.destroy(); this.vols.clear(); }
  /* unit vector toward the sun, world space: azimuth around +Y (0 = from +Z), elevation above the horizon */
  sunDir(){ const C=state.cam, az=C.sunAz*DEG, el=C.sunEl*DEG; return [Math.sin(az)*Math.cos(el), Math.sin(el), Math.cos(az)*Math.cos(el)]; }
  /* ray-march every live volume into T.S, back to front */
  render(T, frame, lighting, exporting){
    gl.bindFramebuffer(gl.FRAMEBUFFER,T.S.fbo); gl.viewport(0,0,T.w,T.h); gl.clearColor(0,0,0,0); gl.clear(gl.COLOR_BUFFER_BIT);
    const cam=camAt(frame), L=this.sunDir();
    const vols=[...this.vols.values()].filter(v=>v.valid && v.frame===frame).map(v=>{
      const c=v.box.min.map((m,a)=>m+v.box.size[a]/2); return {v, d:Math.hypot(...c.map((x,a)=>x-cam.pos[a]))};
    }).sort((a,b)=>b.d-a.d);
    for (const {v} of vols) v.render(T,cam,L,lighting,exporting);
  }
}

/* (Re)build the volume sim; on failure fall back to 2D so the app keeps working. */
export function rebuildSim3D(){
  if (app.sim3d){ app.sim3d.destroy(); app.sim3d=null; }
  if (!state.cam || state.cam.mode!=='3d') return null;
  try { app.sim3d=new Sim3D(); app.sim3d.sync(); }
  catch(e){ console.error(e); if (app.sim3d) app.sim3d.destroy(); state.cam.mode='2d'; app.sim3d=null; return e.message||String(e); }
  return null;
}
