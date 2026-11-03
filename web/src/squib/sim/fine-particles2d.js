import {gl,program,freeFBO,pass,setUniforms,makeFBO} from '../gl/context.js';
import {FS_FINE_PARTICLE_INIT,FS_FINE_PARTICLE_STEP,VS_FINE_PARTICLE_DEPOSIT_2D,FS_FINE_PARTICLE_DEPOSIT} from '../gl/fine-particles.js';
import {makeFineState,supportsFineParticles} from './fine-particles3d.js';
import {fineCloud,fineSettings,fineParticleEnvelope,fineParticleFadeUniforms} from './fines.js';
import {fineCohorts,fineBirthCohorts} from './fine-cohorts.js';
import {PRESETS} from '../presets.js';
import {state} from '../state.js';
import {lin} from '../util.js';

const F32=()=>[gl.RGBA32F,gl.RGBA,gl.FLOAT];
let P,vao;
export const usesSheetParticles=inst=>{const F=fineSettings(PRESETS[inst.preset].dust?.fines,false);return !!(F.analytic&&F.particles&&supportsFineParticles());};
export class FineParticles2D{
  constructor(sheet,inst){
    this.sheet=sheet;this.inst=inst;
    if(!P){P={init:program(FS_FINE_PARTICLE_INIT),step:program(FS_FINE_PARTICLE_STEP),deposit:program(FS_FINE_PARTICLE_DEPOSIT,VS_FINE_PARTICLE_DEPOSIT_2D)};vao=gl.createVertexArray();}
    this.count=Math.max(256,Math.min(65536,Math.round(this.settings().particleCount??PRESETS[inst.preset].fineGrains?.count??14000)));
    this.w=128;this.h=Math.ceil(this.count/this.w);this.read=makeFineState(this.w,this.h);this.write=makeFineState(this.w,this.h);this.plane=[1,0];
    this.position={read:this.read.position.only,write:this.write.position.only};this.velocity={read:this.read.velocity,write:this.write.velocity};
    this.snapshotBytes=this.w*this.h*32;
  }
  settings(){return fineSettings(PRESETS[this.inst.preset].dust.fines,false);}
  get unit(){return state.plate.h*this.inst.scale;}
  airUniforms(age=0){return {uParticleOrigin:[this.inst.x,this.inst.y,0],uParticlePlane:this.plane,uParticleUnit:this.unit,
    uParticleAir:this.sheet.vel.read.tex,uParticleAirDim:[this.sheet.gw,this.sheet.gh,1],uParticleAirTiles:[1,1],uParticleAirMin:[0,0,0],uParticleAirVox:1,
    uParticleEddy:[this.settings().particleStir??0,this.settings().particleEddyScale??.05,age,(this.inst.seed%97)*.317],uParticleSheet:{i:1},uParticlePlate:[state.plate.w,state.plate.h]};}
  init(){
    const F=this.settings(),cohorts=fineCohorts(this.inst,F.cohorts??5),births=F.birthWidth<1?fineBirthCohorts(this.inst,cohorts):cohorts;
    gl.disable(gl.BLEND);pass(P.init,this.read.position,this.w,this.h,{
      uStateSize:[this.w,this.h],uCount:{i:this.count},uSeed:{i:this.inst.seed|0},uId:{i:this.inst.id},uFineCount:{i:cohorts.length},uCohorts:{v4:cohorts.flat()},uBirthCohorts:{v4:births.flat()},
      uFallModes:F.particleFallModes??[0,0,0,0],uFastSpread:F.particleFastSpread??1,uFastFallSpread:F.particleFastFallSpread??3,uSrc:F.src,uSpread:F.spread,uFallKick:F.fallKick??.25,uSettle:F.settle??.02,uGravity:this.inst.gravityMul??1,uMeanFall:.8,uBirthBreakup:Math.max(0,Math.min(1,F.particleBirthBreakup??0)),
      uResidual:Math.max(.2,Math.min(1,F.cohortWidth??1)),uFallResidual:Math.max(.1,Math.min(1,F.cohortFallWidth??F.cohortWidth??1)),uBirth:Math.max(.2,Math.min(1,F.birthWidth??1))});
  }
  prepare(age,dt){if(age<=1e-9&&age+dt>1e-9)this.init();}
  step(age,dt){
    const F=this.settings();pass(P.step,this.write.position,this.w,this.h,{...this.airUniforms(age+dt*.5),
      uStateSize:[this.w,this.h],uCount:{i:this.count},uPosition:this.position.read.tex,uVelocity:this.velocity.read.tex,uAge:age,uDt:dt,
      uDiffusion:Math.max(0,F.particleDiffusion??0),uNoiseStep:{i:Math.round(age/dt)},uNoiseSeed:{i:this.inst.seed|0},
      uLateFall:[Number.isFinite(F.particleLateSettle)?1:0,F.particleLateSettle??0],uFallModes:F.particleFallModes??[0,0,0,0],uFastTau:Math.max(.01,F.particleFastTau??.4),uFastSettle:F.particleFastSettle??F.settle??.02,uTauX:Math.max(.01,F.particleTauX??F.tau),uTauY:Math.max(.01,F.particleTauY??.65),uSettle:F.settle??.02,uGravity:this.inst.gravityMul??1,
      uParticleGround:this.inst.floor?this.inst.y:1e20,uKillBounds:{i:F.particleBounds===false?0:1},uParticleBoxMin:[0,0,-1e10],uParticleBoxMax:[state.plate.w,state.plate.h,1e10]});
    [this.read,this.write]=[this.write,this.read];
    Object.assign(this.position,{read:this.read.position.only,write:this.write.position.only});Object.assign(this.velocity,{read:this.read.velocity,write:this.write.velocity});
  }
  mass(age){const authored=fineParticleEnvelope(this.inst,this.settings(),age,false);if(authored!==null)return authored;const f=fineCloud(this.inst,this.settings(),age,state.plate.h,false,[1,0,0],false).field;return f?f.uFinePeak*2*Math.PI*f.uFineSigma[0]*f.uFineSigma[1]:0;}
  deposit(target,age,color=lin(this.inst.color)){
    const mass=this.mass(age);if(!(mass>0))return;
    const s=this.sheet,kernel=this.settings().particleKernel===1?1:2;
    gl.bindFramebuffer(gl.FRAMEBUFFER,target.fbo);gl.viewport(0,0,target.w,target.h);
    setUniforms(P.deposit,{...fineParticleFadeUniforms(this.inst,this.settings(),age),uPosition:this.position.read.tex,uVelocity:this.velocity.read.tex,uStateSize:[this.w,this.h],uGridDim:[s.gw,s.gh],
      uOrigin:[this.inst.x,this.inst.y],uPlate:[state.plate.w,state.plate.h],uUnit:this.unit,uMass:mass/this.count*(this.unit*s.gh/state.plate.h)**2,
      uColor:color,uKernel:{i:kernel}});
    gl.enable(gl.BLEND);gl.blendFunc(gl.ONE,gl.ONE);gl.bindVertexArray(vao);gl.drawArrays(gl.POINTS,0,this.count*(kernel===2?9:4));gl.disable(gl.BLEND);gl.bindVertexArray(null);
  }
  snapshot(){return {position:makeFBO(this.w,this.h,F32(),gl.NEAREST),velocity:makeFBO(this.w,this.h,F32(),gl.NEAREST)};}
  destroy(){
    for(const s of [this.read,this.write]){freeFBO(s.position);freeFBO(s.velocity);}
  }
}
