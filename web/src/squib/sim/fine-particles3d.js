import { gl,program,makeFBO,makeFBO2,freeFBO,pass,setUniforms } from '../gl/context.js';
import { FS_FINE_PARTICLE_INIT,FS_FINE_PARTICLE_STEP,VS_FINE_PARTICLE_DEPOSIT,FS_FINE_PARTICLE_DEPOSIT,FS_FINE_PARTICLE_COPY } from '../gl/fine-particles.js';
import { PRESETS } from '../presets.js';
import { state } from '../state.js';
import { camAt } from '../io/camera.js';
import { fineCloud,fineSettings,fineParticleEnvelope,fineParticleFadeUniforms } from './fines.js';
import { fineCohorts,fineBirthCohorts } from './fine-cohorts.js';
import { lin } from '../util.js';

const F32=()=>[gl.RGBA32F,gl.RGBA,gl.FLOAT];
let shaders,vao,floatBlend;
export const supportsFineParticles=()=>floatBlend??=!!gl.getExtension('EXT_float_blend');
function programs(){
  if(!shaders){
    shaders={init:program(FS_FINE_PARTICLE_INIT),step:program(FS_FINE_PARTICLE_STEP),deposit:program(FS_FINE_PARTICLE_DEPOSIT,VS_FINE_PARTICLE_DEPOSIT),copy:program(FS_FINE_PARTICLE_COPY)};
    vao=gl.createVertexArray();
  }
  return shaders;
}
export function makeFineState(w,h){
  const [position,velocity]=makeFBO2(w,h,F32());
  for(const f of [position,velocity]){gl.bindTexture(gl.TEXTURE_2D,f.tex);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);}
  return {position,velocity};
}

export class FineParticles3D{
  constructor(volume){
    if(!supportsFineParticles())throw Error('Particle density needs EXT_float_blend');
    this.volume=volume;this.inst=volume.inst;this.P=programs();
    const F=this.settings(),gridScale=F.particleCountByGrid?Math.min(1,(Math.max(...volume.coarse.dim)/128)**3):1;
    this.count=Math.max(256,Math.min(65536,Math.round((F.particleCount??PRESETS[this.inst.preset].fineGrains?.count??14000)*gridScale)));
    this.w=128;this.h=Math.ceil(this.count/this.w);
    this.read=makeFineState(this.w,this.h);this.write=makeFineState(this.w,this.h);
    this.position={read:this.read.position.only,write:this.write.position.only};this.velocity={read:this.read.velocity,write:this.write.velocity};
    const C=volume.coarse;
    this.forceDensity=makeFBO(C.w,C.h,F32(),gl.NEAREST);
    this.renderDensity=C.w===volume.aw&&C.h===volume.ah?this.forceDensity:makeFBO(volume.aw,volume.ah,F32(),gl.NEAREST);
    this.updatePlane();
  }
  settings(){return fineSettings(PRESETS[this.inst.preset].dust.fines,true);}
  updatePlane(){
    const right=camAt(this.inst.frame).right,length=Math.hypot(right[0],right[2]);
    this.plane=length>1e-6?[right[0]/length,right[2]/length]:[1,0];
  }
  get unit(){return this.inst.wscale*this.inst.scale;}
  airUniforms(age=0){const v=this.volume,C=v.coarse;return{
    uParticleOrigin:this.inst.w,uParticlePlane:this.plane,uParticleUnit:this.unit,
    uParticleAir:v.vel.read.tex,uParticleAirDim:C.dim,uParticleAirTiles:C.tiles,uParticleAirMin:v.boxMin,uParticleAirVox:C.vox,
    uParticleEddy:[this.settings().particleStir??0,this.settings().particleEddyScale??.05,age,(this.inst.seed%97)*.317],uParticleSheet:{i:0},uParticlePlate:[state.plate.w,state.plate.h]};}
  init(){
    gl.disable(gl.BLEND);
    this.updatePlane();const F=this.settings(),groups=fineCohorts(this.inst,F.cohorts??5),births=F.birthWidth<1?fineBirthCohorts(this.inst,groups):groups;
    pass(this.P.init,this.read.position,this.w,this.h,{
      uStateSize:[this.w,this.h],uCount:{i:this.count},uSeed:{i:this.inst.seed|0},uId:{i:this.inst.id},uFineCount:{i:groups.length},uCohorts:{v4:groups.flat()},uBirthCohorts:{v4:births.flat()},
      uFallModes:F.particleFallModes??[0,0,0,0],uFastSpread:F.particleFastSpread??1,uFastFallSpread:F.particleFastFallSpread??3,uSrc:F.src,uSpread:F.spread*1.25,uFallKick:F.fallKick??.25,uSettle:F.settle??.02,uGravity:this.inst.gravityMul??1,uMeanFall:.75,uBirthBreakup:Math.max(0,Math.min(1,F.particleBirthBreakup??0)),
      uResidual:Math.max(.2,Math.min(1,F.cohortWidth??1)),uFallResidual:Math.max(.1,Math.min(1,F.cohortFallWidth??F.cohortWidth??1)),uBirth:Math.max(.2,Math.min(1,F.birthWidth??1))});
  }
  swap(){
    [this.read,this.write]=[this.write,this.read];
    Object.assign(this.position,{read:this.read.position.only,write:this.write.position.only});
    Object.assign(this.velocity,{read:this.read.velocity,write:this.write.velocity});
  }
  prepare(age,dt){if(age<=1e-9&&age+dt>1e-9)this.init();}
  step(age,dt){
    const F=this.settings(),v=this.volume,margin=v.coarse.vox*2;
    pass(this.P.step,this.write.position,this.w,this.h,{...this.airUniforms(age+dt*.5),
      uStateSize:[this.w,this.h],uCount:{i:this.count},uPosition:this.position.read.tex,uVelocity:this.velocity.read.tex,uAge:age,uDt:dt,
      uDiffusion:Math.max(0,F.particleDiffusion??0),uNoiseStep:{i:Math.round(age/dt)},uNoiseSeed:{i:this.inst.seed|0},
      uLateFall:[Number.isFinite(F.particleLateSettle)?1:0,F.particleLateSettle??0],uFallModes:F.particleFallModes??[0,0,0,0],uFastTau:Math.max(.01,F.particleFastTau??.4),uFastSettle:F.particleFastSettle??F.settle??.02,uTauX:Math.max(.01,F.particleTauX??F.tau),uTauY:Math.max(.01,F.particleTauY??.65),uSettle:F.settle??.02,uGravity:this.inst.gravityMul??1,
      uParticleGround:Math.max(state.cam.groundY,this.inst.floor?this.inst.w[1]:-1e20),uKillBounds:{i:F.particleBounds===false?0:1},
      uParticleBoxMin:v.boxMin.map(x=>x-margin),uParticleBoxMax:v.boxMin.map((x,k)=>x+v.box.size[k]+margin)});
    this.swap();
  }
  mass(age){
    const F=this.settings(),authored=fineParticleEnvelope(this.inst,F,age,true);if(authored!==null)return authored;
    const cloud=fineCloud(this.inst,F,age,this.inst.wscale,true,[this.plane[0],0,this.plane[1]],false),f=cloud.field;
    return f?f.uFinePeak*Math.pow(2*Math.PI,1.5)*f.uFineSigma.reduce((a,b)=>a*b,1):0;
  }
  deposit(target,grid,age,kernel=this.settings().particleKernel===1?1:2){
    gl.bindFramebuffer(gl.FRAMEBUFFER,target.fbo);gl.viewport(0,0,target.w,target.h);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT);
    const mass=this.mass(age);if(!(mass>0))return;
    setUniforms(this.P.deposit,{...fineParticleFadeUniforms(this.inst,this.settings(),age),
      uPosition:this.position.read.tex,uVelocity:this.velocity.read.tex,uStateSize:[this.w,this.h],
      uGridDim:grid.dim,uGridTiles:grid.tiles,uGridMin:this.volume.boxMin,uGridVox:grid.vox,
      uParticleOrigin:this.inst.w,uParticlePlane:this.plane,uParticleUnit:this.unit,uColor:lin(this.inst.color),
      uMass:mass/this.count*(this.unit/grid.vox)**3,uKernel:{i:kernel}});
    gl.enable(gl.BLEND);gl.blendFunc(gl.ONE,gl.ONE);gl.bindVertexArray(vao);
    gl.drawArrays(gl.POINTS,0,this.count*(kernel===2?27:8));gl.disable(gl.BLEND);gl.bindVertexArray(null);
  }
  depositForce(age){this.deposit(this.forceDensity,this.volume.coarse,age);}
  render(target,age){
    const v=this.volume;this.deposit(this.renderDensity,{dim:v.dim,tiles:v.tiles,vox:v.vox},age,(this.settings().particleRenderKernel??this.settings().particleKernel)===1?1:2);
    pass(this.P.copy,target,v.aw,v.ah,{uSource:this.renderDensity.tex});
  }
  destroy(){
    // The volume owns the position/velocity textures and their single-target
    // FBOs through its snapshotted fields; these are the additional MRT handles.
    gl.deleteFramebuffer(this.read.position.fbo);gl.deleteFramebuffer(this.write.position.fbo);
    freeFBO(this.forceDensity);if(this.renderDensity!==this.forceDensity)freeFBO(this.renderDensity);
  }
}
