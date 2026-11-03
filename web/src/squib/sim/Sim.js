import { F16, P, R16, drawSprites, freeFBO, gl, makeDouble, makeFBO, makeFBO2, makeTex, pass, program, sprAttr, sprDye, sprHeat, sprMaterial, sprVel } from '../gl/context.js';
import { FS } from '../gl/shaders.js';
import {PressureSolver} from '../gl/pressure.js';
import { PRESETS, SIMULATED } from '../presets.js';
import { emitFireball, emitImpact, emitSmoke, stepParticles } from './emitters.js';
import { app, state } from '../state.js';
import { detailNoise } from '../gl/detail-noise.js';
import { fineCloud } from './fines.js';
import { FineParticles2D,usesSheetParticles } from './fine-particles2d.js';
import {usesBakedSmoke} from '../baked/library.js';
import { FLOW_PERIOD, JACOBI, SNAP_EVERY, SUBSTEPS, seedMix } from '../util.js';

/* =========================================================================
   Simulation
   ========================================================================= */
export function simStartFrame(){
  let s=state.plate.first;
  for (const i of state.instances) if (i.enabled && !usesBakedSmoke(i) && SIMULATED(PRESETS[i.preset].kind)) s=Math.min(s, Math.floor(i.frame)-1);
  return s-1;
}
class Sheet{
  constructor(gw,gh,layer=0){
    this.layer=layer;
    this.gw=gw; this.gh=gh; this.texel=[1/gw,1/gh];
    this.vel=makeDouble(gw,gh,F16()); this.dye=makeDouble(gw,gh,F16()); this.p=makeDouble(gw,gh,R16());
    // attr = (curl, turbulence, dissipation rate, age-linger) x dye density, advected and faded with the dye,
    // so attr/density is a per-element material property
    this.attr=makeDouble(gw,gh,F16());
    this.material=makeDouble(gw,gh,F16());   // instantaneous / terminal fall speed, birth coordinates, all x density
    this.heat=makeDouble(gw,gh,R16());   // fire heat x density (explosions), carried and dissipated with the dye
    this.flow=makeDouble(gw,gh,F16());   // render detail: two advected displacement fields (xy, zw), reset in turn (flow noise)
    [this.fines,this.finesFlow]=makeFBO2(gw,gh,F16());
    [this.combined,this.combinedFlow]=makeFBO2(gw,gh,F16());this.renderKey=null;
    this.shadeBlur=makeFBO(gw,gh,F16());this.shadeDye=makeFBO(gw,gh,F16());
    this.div=makeFBO(gw,gh,R16()); this.curl=makeFBO(gw,gh,R16());
    this.tA=makeFBO(gw,gh,F16()); this.tB=makeFBO(gw,gh,F16());   // MacCormack scratch
    this.obs=makeTex(gw,gh,gl.R8,gl.RED,gl.UNSIGNED_BYTE,gl.LINEAR);
    this.obsVel=makeTex(gw,gh,gl.RG16F,gl.RG,gl.FLOAT,gl.LINEAR);
    this.zeroObsVel=new Float32Array(gw*gh*2);
    this.obsKey=null; this.zeroObs=new Uint8Array(gw*gh);
    this.maxSnaps=Math.max(2,Math.floor(128*1024*1024/(gw*gh*44)));   // KeyFX: one sim per explosion, so a smaller snapshot budget
    this.snaps=new Map(); this.pool=[]; this.parts=new Map();
    this.frame=null; this.valid=false; this.startFrame=null;
    this.fineParticles=new Map();this.particleKey=null;this.particleDensity=null;this.particleForces=null;
  }
  layerInstance(inst){return this.layer?{...inst,seed:seedMix(inst.seed,this.layer,47)}:inst;}
  get snapshotBytes(){return this.gw*this.gh*44+[...this.fineParticles.values()].reduce((n,p)=>n+p.snapshotBytes,0);}
  freeSnapshot(s){
    for(const key of ['vel','dye','p','attr','material','heat','flow'])freeFBO(s[key]);
    for(const p of s.particles?.values()??[]){freeFBO(p.position);freeFBO(p.velocity);}
  }
  setBudget(bytes){
    this.maxSnaps=Math.max(0,Math.floor(bytes/this.snapshotBytes));
    while(this.snaps.size>this.maxSnaps){const key=this.snaps.keys().next().value;this.pool.push(this.snaps.get(key));this.snaps.delete(key);}
    while(this.pool.length+this.snaps.size>this.maxSnaps)this.freeSnapshot(this.pool.pop());
  }
  syncParticles(){
    const effects=state.instances.filter(i=>i.enabled&&!usesBakedSmoke(i)&&usesSheetParticles(i));
    const key=JSON.stringify(effects.map(i=>[i.id,i.preset,PRESETS[i.preset].dust.fines.sheet?.particleCount??PRESETS[i.preset].fineGrains?.count??14000]));
    if(key!==this.particleKey){
      this.invalidateAll();for(const s of this.pool)this.freeSnapshot(s);this.pool=[];
      for(const p of this.fineParticles.values())p.destroy();this.fineParticles.clear();
      for(const i of effects)this.fineParticles.set(i.id,new FineParticles2D(this,this.layerInstance(i)));
      this.particleKey=key;this.startFrame=null;this.valid=false;
      if(effects.length&&!this.particleDensity){
        this.particleDensity=makeFBO(this.gw,this.gh,[gl.RGBA32F,gl.RGBA,gl.FLOAT],gl.NEAREST);
        this.particleForces=makeFBO(this.gw,this.gh,[gl.RGBA32F,gl.RGBA,gl.FLOAT],gl.NEAREST);
        P.forcesParticles??=program(FS.forces.replace('#version 300 es','#version 300 es\n#define PARTICLE_FINES'));
      }
    }
    for(const i of effects)this.fineParticles.get(i.id).inst=this.layerInstance(i);
  }
  destroy(){
    this.pressureSolver?.destroy();
    for (const d of [this.vel,this.dye,this.p,this.attr,this.heat,this.material,this.flow]){ freeFBO(d.read); freeFBO(d.write); }
    freeFBO(this.fines);freeFBO(this.finesFlow);freeFBO(this.combined);freeFBO(this.combinedFlow);
    freeFBO(this.shadeBlur);freeFBO(this.shadeDye);
    freeFBO(this.div); freeFBO(this.curl); freeFBO(this.tA); freeFBO(this.tB); gl.deleteTexture(this.obs);gl.deleteTexture(this.obsVel);
    for (const s of this.snaps.values()) this.pool.push(s);
    for (const s of this.pool)this.freeSnapshot(s);
    for(const p of this.fineParticles.values())p.destroy();freeFBO(this.particleDensity);freeFBO(this.particleForces);
  }
  clearTex(f){ gl.bindFramebuffer(gl.FRAMEBUFFER,f.fbo); gl.clearColor(0,0,0,0); gl.clear(gl.COLOR_BUFFER_BIT); }
  reset(){
    for (const d of [this.vel,this.dye,this.p,this.attr,this.heat,this.material,this.flow]){ this.clearTex(d.read); this.clearTex(d.write); }
    this.parts=new Map(); this.startFrame=simStartFrame(); this.frame=this.startFrame; this.valid=true;
    for(const p of this.fineParticles.values())p.init();
  }
  invalidateFrom(keep){ // keep states for frames <= keep
    this.renderKey=null;
    for (const [F,s] of this.snaps) if (F>keep){ this.snaps.delete(F); this.pool.push(s); }
    if (this.frame===null || this.frame>keep) this.valid=false;
  }
  invalidateAll(){ this.invalidateFrom(-Infinity); }
  blit(src,dst){
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER,src.fbo); gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER,dst.fbo);
    gl.blitFramebuffer(0,0,src.w,src.h,0,0,dst.w,dst.h,gl.COLOR_BUFFER_BIT,gl.NEAREST);
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER,null); gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER,null);
  }
  cloneParts(m){ const n=new Map(); for (const [k,v] of m) n.set(k,{debris:v.debris.map(p=>({...p})), sparks:v.sparks.map(p=>({...p}))}); return n; }
  save(){
    if (this.snaps.size>=this.maxSnaps) return;
    const s=this.pool.pop() || {vel:makeFBO(this.gw,this.gh,F16()), dye:makeFBO(this.gw,this.gh,F16()), p:makeFBO(this.gw,this.gh,R16()), attr:makeFBO(this.gw,this.gh,F16()), material:makeFBO(this.gw,this.gh,F16()), heat:makeFBO(this.gw,this.gh,R16()), flow:makeFBO(this.gw,this.gh,F16())};
    this.blit(this.vel.read,s.vel); this.blit(this.dye.read,s.dye); this.blit(this.p.read,s.p); this.blit(this.attr.read,s.attr); this.blit(this.heat.read,s.heat); this.blit(this.material.read,s.material); this.blit(this.flow.read,s.flow);
    s.particles??=new Map([...this.fineParticles].map(([id,p])=>[id,p.snapshot()]));
    for(const [id,p]of this.fineParticles){this.blit(p.position.read,s.particles.get(id).position);this.blit(p.velocity.read,s.particles.get(id).velocity);}
    s.parts=this.cloneParts(this.parts); s.frame=this.frame;
    this.snaps.set(this.frame,s);
  }
  restore(s){
    this.blit(s.vel,this.vel.read); this.blit(s.dye,this.dye.read); this.blit(s.p,this.p.read); this.blit(s.attr,this.attr.read); this.blit(s.heat,this.heat.read); this.blit(s.material,this.material.read); this.blit(s.flow,this.flow.read);
    for(const [id,p]of this.fineParticles){this.blit(s.particles.get(id).position,p.position.read);this.blit(s.particles.get(id).velocity,p.velocity.read);}
    this.parts=this.cloneParts(s.parts); this.frame=s.frame; this.valid=true;
  }
  goto(f){
    this.syncParticles();
    const empty=!state.instances.some(i=>i.enabled&&!usesBakedSmoke(i)&&SIMULATED(PRESETS[i.preset].kind));
    if(empty){if(!this.wasEmpty||!this.valid){this.invalidateAll();this.reset();}this.wasEmpty=true;this.frame=f;this.valid=true;return;}
    if(this.wasEmpty){this.wasEmpty=false;this.invalidateAll();this.startFrame=null;}
    if (simStartFrame()!==this.startFrame){ this.invalidateAll(); this.startFrame=null; }
    if (this.valid && this.frame===f) return;
    if (!this.valid || this.frame>f || this.startFrame===null){
      let best=null; for (const [F,s] of this.snaps) if (F<=f && (!best||F>best.frame)) best=s;
      if (best && this.startFrame!==null) this.restore(best); else { this.snaps.forEach(s=>this.pool.push(s)); this.snaps.clear(); this.reset(); }
    }
    while (this.frame<f){
      this.step();
      if ((this.frame-this.startFrame)%SNAP_EVERY===0 && !this.snaps.has(this.frame)) this.save();
    }
  }
  renderField(frame){
    const effects=state.instances.filter(i=>i.enabled&&!usesBakedSmoke(i)&&PRESETS[i.preset].dust?.fines?.analytic);
    this.fineActive=effects.length>0;
    if(!effects.length)return this.dye.read.tex;
    const physicalFrame=app.freezeEffectsFrame??frame,key=JSON.stringify([physicalFrame,effects]);
    if(this.renderKey!==key){
      this.clearTex(this.fines);this.clearTex(this.finesFlow);
      if(this.particleDensity)this.clearTex(this.particleDensity);
      for(const original of effects){
        const inst=this.layerInstance(original),particles=this.fineParticles.get(inst.id);
        if(particles){particles.deposit(this.particleDensity,(physicalFrame-inst.frame)/state.fps);continue;}
        gl.enable(gl.BLEND);gl.blendFunc(gl.ONE,gl.ONE);
        const F=PRESETS[inst.preset].dust.fines;
        const cloud=fineCloud(inst,F,(physicalFrame-inst.frame)/state.fps,state.plate.h,false,[1,0,0],!F.distribution);
        if(PRESETS[inst.preset].dust.fines.distribution){
          if(cloud.field)pass(P.fineDistribution,this.fines,this.gw,this.gh,{...cloud.field,
            uFineOrigin:[inst.x,inst.y],uFinePlate:[state.plate.w,state.plate.h],uFineUnit:state.plate.h*inst.scale});
          continue;
        }
        sprDye.clear();for(const p of cloud.parcels)sprDye.push(p.p[0],p.p[1],0,0,p.r,0,0,0,p.col[0]*p.d,p.col[1]*p.d,p.col[2]*p.d,p.d);
        drawSprites(this.fines.only,this.gw,this.gh,this.gw/state.plate.w,sprDye.a,sprDye.n,false);
        // Density-weighted material displacement lets detail follow overlapping
        // analytic clouds at different ages without renewing their texture.
        sprDye.clear();for(const p of cloud.parcels)sprDye.push(p.p[0],p.p[1],0,0,p.r,0,0,0,
          -p.airMove[0]/state.plate.h*p.d,p.airMove[1]/state.plate.h*p.d,0,p.d);
        drawSprites(this.finesFlow,this.gw,this.gh,this.gw/state.plate.w,sprDye.a,sprDye.n,false);
      }
      gl.disable(gl.BLEND);
      pass(P.combineFines,this.combined,this.gw,this.gh,{uDetailNoise:detailNoise(),uFluid:this.dye.read.tex,uFines:this.fines.tex,uMotion:this.finesFlow.tex,
        uParticleFines:this.particleDensity?.tex??this.fines.tex,uHasParticles:this.fineParticles.size?1:0,
        uGridH:this.gh,uPhase:[this.layer*31.7,this.layer*57.3],uObs:this.obs});this.renderKey=key;
    }
    return this.combined.tex;
  }
  shadeField(field){
    gl.disable(gl.BLEND);
    // Finer transport must not shrink the optical lighting footprint.
    const k=Math.max(1,Math.max(this.gw,this.gh)/768);
    pass(P.blurDensity,this.shadeBlur,this.gw,this.gh,{uSrc:field,uStep:[this.texel[0]*k,0]});
    pass(P.blurDensity,this.shadeDye,this.gw,this.gh,{uSrc:this.shadeBlur.tex,uStep:[0,this.texel[1]*k]});
    return this.shadeDye.tex;
  }
  partsFor(id){ let p=this.parts.get(id); if(!p){ p={debris:[],sparks:[]}; this.parts.set(id,p); } return p; }
  uploadObstacle(f){
    const m=state.matte;
    let key='none', data=this.zeroObs,velocity=this.zeroObsVel;
    if (m && m.data.size && m.gw===this.gw && m.gh===this.gh && (state.scene.matteUse||'collide')!=='holdout'){   // a holdout-only matte doesn't block
      let pick=m.frames[0]; for (const fr of m.frames){ if (fr<=f) pick=fr; else break; }
      key=pick+(m.invert?'i':''); data=m.invert ? m.inv(pick) : m.data.get(pick);
      if(m.motion)velocity=m.motion(pick,state.fps);
    }
    if (key===this.obsKey) return;
    gl.bindTexture(gl.TEXTURE_2D,this.obs); gl.pixelStorei(gl.UNPACK_ALIGNMENT,1);
    gl.texSubImage2D(gl.TEXTURE_2D,0,0,0,this.gw,this.gh,gl.RED,gl.UNSIGNED_BYTE,data);
    gl.bindTexture(gl.TEXTURE_2D,this.obsVel);
    gl.texSubImage2D(gl.TEXTURE_2D,0,0,0,this.gw,this.gh,gl.RG,gl.FLOAT,velocity);
    this.obsKey=key;
  }
  step(){
    const N=SUBSTEPS, fps=state.fps, dt=1/(fps*N);
    this.uploadObstacle(this.frame+1);
    for (let k=0;k<N;k++){
      const tA=this.frame+k/N, tB=this.frame+(k+1)/N, sub=this.frame*N+k;
      const splats=[];
      for (const original of state.instances){
        if (!original.enabled || usesBakedSmoke(original)) continue;
        const inst=this.layer ? {...original,seed:seedMix(original.seed,this.layer,47)} : original;
        const pr=PRESETS[inst.preset];
        if (pr.kind==='impact'){
          const ps=this.partsFor(inst.id),impact=this.fineParticles.has(inst.id)&&pr.dust?.particleCoreAmount!=null?{...pr,dust:{...pr.dust,amount:pr.dust.particleCoreAmount}}:pr;
          emitImpact(inst,impact,tA,tB,sub,splats,ps);emitFireball(inst,pr,tA,tB,sub,splats);stepParticles(inst,pr,ps,dt,splats);
        }
        else if (pr.kind==='smoke') emitSmoke(inst,pr,tA,tB,sub,splats);
      }
      this.fluid(dt,splats,sub*dt,sub);
    }
    this.frame++;
  }
  fluid(dt,splats,time,sub){
    const S=state.scene, gw=this.gw, gh=this.gh, H=state.plate.h, W=state.plate.w;
    const g=gh/H, sc=gw/W;
    const obs=this.obs, texel=this.texel;
    if (splats.length){
      sprVel.clear(); sprDye.clear(); sprAttr.clear(); sprHeat.clear(); sprMaterial.clear();
      const warm=[];
      for (const s of splats){
        if (s.tempOnly){ warm.push(s); continue; }
        if (s.vw>0) sprVel.push(s.x,s.y,s.ax||0,s.ay||0,s.r,0,s.thermalPhase??0,s.thermal??0, s.vx*g, -s.vy*g, s.temp, s.vw);
        if (s.d>0){
          sprDye.push(s.x,s.y,s.ax||0,s.ay||0,s.r*0.9,s.fragment?5:0,0,s.fragment||0, s.col[0]*s.d, s.col[1]*s.d, s.col[2]*s.d, s.d);
          sprAttr.push(s.x,s.y,s.ax||0,s.ay||0,s.r*0.9,s.fragment?5:0,0,s.fragment||0, s.curl*s.d, s.turb*s.d, s.rate*s.d, -s.linger*s.d);
          sprMaterial.push(s.x,s.y,s.ax||0,s.ay||0,s.r*0.9,s.fragment?5:0,0,s.fragment||0, (s.settle||0)*s.d, (s.terminalSettle??s.settle??0)*s.d, s.d, 0);
          if (s.heat>0) sprHeat.push(s.x,s.y,s.ax||0,s.ay||0,s.r*0.9,0,0,0, s.heat*s.d, 0, 0, 0);
        }
      }
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);
      drawSprites(this.vel.read,gw,gh,sc,sprVel.a,sprVel.n,true);
      if (warm.length){   // temperature-only splats (smoke draft): blend temperature alone, velocity and eddies are left alone
        sprVel.clear(); for (const s of warm) sprVel.push(s.x,s.y,s.ax||0,s.ay||0,s.r,0,0,0, 0,0,s.temp,s.vw);
        gl.colorMask(false,false,true,false); drawSprites(this.vel.read,gw,gh,sc,sprVel.a,sprVel.n,true); gl.colorMask(true,true,true,true);
      }
      gl.blendFunc(gl.ONE,gl.ONE);
      drawSprites(this.dye.read,gw,gh,sc,sprDye.a,sprDye.n,false);
      drawSprites(this.attr.read,gw,gh,sc,sprAttr.a,sprAttr.n,false);
      drawSprites(this.heat.read,gw,gh,sc,sprHeat.a,sprHeat.n,false);
      drawSprites(this.material.read,gw,gh,sc,sprMaterial.a,sprMaterial.n,2);
      gl.disable(gl.BLEND);
    }
    if(this.fineParticles.size){
      this.clearTex(this.particleForces);
      for(const p of this.fineParticles.values()){
        p.prepare(time-p.inst.frame/state.fps,dt);
        const F=p.settings();p.deposit(this.particleForces,time-p.inst.frame/state.fps+dt*.5,[F.particleCurl??p.inst.curl,F.particleTurb??p.inst.turb,0]);
      }
    }
    pass(P.curl,this.curl,gw,gh,{uVel:this.vel.read.tex,uTexel:texel,uObs:obs});
    pass(this.fineParticles.size?P.forcesParticles:P.forces,this.vel.write,gw,gh,{uParticleForces:this.particleForces?.tex??this.dye.read.tex,uVel:this.vel.read.tex,uCurl:this.curl.tex,uDye:this.dye.read.tex,uAttr:this.attr.read.tex,uTexel:texel,uObs:obs,uObsVel:this.obsVel,
      uDt:dt,uTime:time+this.layer*13.7,uFineTurb:S.fineTurb??1,uTurbK:0.2*gh,uAspect:[gw/gh,1],uBuoy:S.buoyancy*gh,uWindK:S.drag,uWind:[S.wind*gh,0],
      uSoftColliderDrag:state.matte?.soft?.drag??0});
    this.vel.swap();
    const project=()=>{
      pass(P.divergence,this.div,gw,gh,{uVel:this.vel.read.tex,uTexel:texel,uObs:obs,uObsVel:this.obsVel});
      let pressure;
      if(S.multigrid&&(!state.matte||(S.matteUse||'collide')==='holdout')){
        this.pressureSolver??=new PressureSolver([gw,gh,1]);pressure=this.pressureSolver.solve(this.div.tex,S.pressureCycles??1);
      }else{
        pass(P.scale,this.p.write,gw,gh,{uSrc:this.p.read.tex,uK:0.8,uTexel:texel,uObs:obs}); this.p.swap();
        for(let i=0;i<JACOBI;i++){pass(P.pressure,this.p.write,gw,gh,{uP:this.p.read.tex,uDiv:this.div.tex,uTexel:texel,uObs:obs});this.p.swap();}
        pressure=this.p.read.tex;
      }
      pass(P.gradient,this.vel.write,gw,gh,{uP:pressure,uVel:this.vel.read.tex,uTexel:texel,uObs:obs,uObsVel:this.obsVel});this.vel.swap();
    };
    if(!S.projectAfter)project();
    const kv=1, kt=S.cooling==null?Math.pow(.3,dt):Math.exp(-Math.max(0,S.cooling)*dt);
    const maxFall=Math.max(.55,...state.instances.filter(i=>i.enabled).map(i=>{
      const pr=PRESETS[i.preset],d=pr.dust;
      return Math.max(d?.bulkFall?.up??0,(d?.bulkFall?.gravity??0)/2,d?.settle??0,pr.grit?.settle??0)*(i.gravityMul??1);
    }));
    const one=[1,1,1,1], base={uDt:dt,uRk2:{i:S.rk2?1:0},uTexel:texel,uObs:obs,uDye:this.dye.read.tex,uMaterial:this.material.read.tex,uGridH:gh,uDragAvg:-Math.expm1(-2*dt)/(2*dt),uFallReach:Math.max(1,Math.ceil(maxFall*gh*dt)+1)};
    if (S.mc){   // MacCormack: a bounded correction restores billow detail lost to the plain step
      pass(P.advect,this.tA,gw,gh,{...base,uVel:this.vel.read.tex,uSrc:this.vel.read.tex,uKeep:one,uScalar:{i:0}});
      pass(P.mc,this.vel.write,gw,gh,{...base,uVel:this.vel.read.tex,uSrc:this.vel.read.tex,uFwd:this.tA.tex,uKeep:[kv,kv,kt,0],uMode:{i:0}}); this.vel.swap();
      if(S.projectAfter)project();
      pass(P.advect,this.tA,gw,gh,{...base,uVel:this.vel.read.tex,uSrc:this.dye.read.tex,uKeep:one,uScalar:{i:1}});
      pass(P.advect,this.tB,gw,gh,{...base,uVel:this.vel.read.tex,uSrc:this.attr.read.tex,uKeep:one,uScalar:{i:1}});
      const m={...base,uVel:this.vel.read.tex,uFwdD:this.tA.tex,uFwdA:this.tB.tex};
      pass(P.mc,this.dye.write,gw,gh,{...m,uSrc:this.dye.read.tex,uFwd:this.tA.tex,uMode:{i:1}});
      pass(P.mc,this.attr.write,gw,gh,{...m,uSrc:this.attr.read.tex,uFwd:this.tB.tex,uMode:{i:2}});
    } else {
      pass(P.advect,this.vel.write,gw,gh,{...base,uVel:this.vel.read.tex,uSrc:this.vel.read.tex,uKeep:[kv,kv,kt,0],uScalar:{i:0}}); this.vel.swap();
      if(S.projectAfter)project();
      const fu={...base,uVel:this.vel.read.tex,uDye:this.dye.read.tex,uAttr:this.attr.read.tex};
      pass(P.advectFade,this.dye.write,gw,gh,{...fu,uOut:{i:0}});
      pass(P.advectFade,this.attr.write,gw,gh,{...fu,uOut:{i:1}});
    }
    pass(P.material,this.material.write,gw,gh,{...base,uVel:this.vel.read.tex,uDyeNew:this.dye.write.tex});
    pass(P.heat,this.heat.write,gw,gh,{...base,uVel:this.vel.read.tex,uDye:this.dye.read.tex,uHeat:this.heat.read.tex,uDyeNew:this.dye.write.tex,uTime:time,uAspect:[gw/gh,1]});
    // flow noise: each field resets every FLOW_PERIOD of sim time, half a period apart (keyed to the substep, so deterministic)
    const nP=Math.max(2,Math.round(FLOW_PERIOD/dt)), rA=sub%nP===0, rB=(sub+(nP>>1))%nP===0;
    pass(P.flow,this.flow.write,gw,gh,{...base,uVel:this.vel.read.tex,uSrc:this.flow.read.tex,uH:1/gh,uReset:[rA?1:0,rB?1:0]});
    this.dye.swap(); this.attr.swap(); this.heat.swap(); this.material.swap(); this.flow.swap();
    for(const p of this.fineParticles.values())p.step(time-p.inst.frame/state.fps,dt);
  }
}

// Independent 2D flow sheets overlap in projection. One planar solve produces empty
// vortex centres and ribbons that a single shading pass cannot turn into smoke depth.
export class Sim extends Sheet{
  constructor(gw,gh){ super(gw,gh); this.depth=[]; }
  get sheets(){ return [this,...this.depth]; }
  syncDepth(){
    const count=[1,3,5].includes(state.scene.depth2d)?state.scene.depth2d:3;
    if(this.depth.length!==count-1){
      for(const layer of this.depth)layer.destroy();
      this.depth=Array.from({length:count-1},(_,n)=>new Sheet(this.gw,this.gh,n+1));
      super.invalidateFrom(-Infinity);
    }
    for(const sheet of this.sheets)sheet.syncParticles();
    const bytes=this.sheets.reduce((n,s)=>n+s.snapshotBytes,0),max=Math.max(0,Math.floor(160*1024*1024/bytes));   // KeyFX: smaller budget (one sim per explosion)
    for(const sheet of this.sheets){
      sheet.setBudget(max*sheet.snapshotBytes);
    }
  }
  goto(f){ this.syncDepth(); super.goto(f); for(const sheet of this.depth)sheet.goto(f); }
  invalidateFrom(f){ super.invalidateFrom(f); for(const sheet of this.depth||[])sheet.invalidateFrom(f); }
  destroy(){ super.destroy(); for(const sheet of this.depth)sheet.destroy(); }
}
