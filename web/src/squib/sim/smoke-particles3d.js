import {gl,program,pass,setUniforms,makeFBO,freeFBO,F16} from '../gl/context.js';
import {FS_SMOKE_PARTICLE_STEP,VS_SMOKE_PARTICLE_DEPOSIT,FS_SMOKE_PARTICLE_DEPOSIT,FS_SMOKE_PARTICLE_NORMALIZE} from '../gl/smoke-particles.js';
import {makeFineState,supportsFineParticles} from './fine-particles3d.js';
import {PRESETS} from '../presets.js';
import {state} from '../state.js';
import {SUBSTEPS,lin} from '../util.js';
const F32=()=>[gl.RGBA32F,gl.RGBA,gl.FLOAT];
let P,vao;
// Bang On can opt a volume into collision-aware shader variants. Keep the
// stock programs separate so loading an adapter never changes native Squib.
const adaptedPrograms=new WeakMap();
function programs(transform){
 const source=(name,text)=>transform?transform(name,text):text;
 const make=()=>({step:program(source('smokeParticleStep',FS_SMOKE_PARTICLE_STEP)),
  deposit:program(FS_SMOKE_PARTICLE_DEPOSIT,source('smokeParticleDeposit',VS_SMOKE_PARTICLE_DEPOSIT)),
  normalize:program(source('smokeParticleNormalize',FS_SMOKE_PARTICLE_NORMALIZE))});
 if(!transform)return P??=make();
 let result=adaptedPrograms.get(transform);if(!result){result=make();adaptedPrograms.set(transform,result);}return result;
}
export function usesSmokeParticles(inst){return !!(PRESETS[inst.preset].smoke&&!PRESETS[inst.preset].smoke.fire&&(state.cam.smokeParticles??PRESETS[inst.preset].smoke.volume?.particles)&&supportsFineParticles());}
export function smokeParticleSettings(inst){return PRESETS[inst.preset].smoke?.volume||{};}
// The existing render-only Detail control mixes conserved particle and grid
// extinction. Zero returns the transported grid, without changing simulation.
export function smokeParticleBlend(inst){return Math.max(0,Math.min(1,(state.cam.smokeParticleBlend??smokeParticleSettings(inst).particleBlend??1)*(state.cam.detail??1)));}
// The compact Gaussian splat's exact radial kernel, integrated numerically once.
// Its vertical marginal supplies the source's floor-clipped mass fraction.
const N=512,radial=new Float64Array(N+1),marginal=new Float64Array(N+1),cdf=new Float64Array(N+1);
const smooth=x=>{x=Math.max(0,Math.min(1,x));return x*x*(3-2*x);};
for(let i=N-1;i>=0;i--){const q=(i+.5)/N;radial[i]=radial[i+1]+Math.exp(-3*q)*(1-smooth((q-.65)/.35))/N;}
for(let i=0;i<=N;i++){const y=-1+2*i/N,q=y*y*N,j=Math.min(N-1,Math.floor(q)),t=q-j;marginal[i]=Math.PI*(radial[j]*(1-t)+radial[j+1]*t);if(i)cdf[i]=cdf[i-1]+(marginal[i-1]+marginal[i])/N;}
export const smokeKernelIntegral=cdf[N];
const clippedMass=t=>{if(t<=-1)return 1;if(t>=1)return 0;const q=(t+1)*N/2,j=Math.min(N-1,Math.floor(q)),f=q-j;return 1-(cdf[j]*(1-f)+cdf[j+1]*f)/smokeKernelIntegral;};

export class SmokeParticles3D{
 constructor(volume){
  this.volume=volume;this.inst=volume.inst;
  this.shaderTransform=typeof this.inst.volumeFXShader==='function'?this.inst.volumeFXShader:null;
  this.P=programs(this.shaderTransform);if(!vao)vao=gl.createVertexArray();
  const options=smokeParticleSettings(this.inst),requested=state.cam.smokeParticleCount??options.particleCount??262144,air=Math.max(...volume.coarse.dim);
  this.count=Math.max(4096,Math.min(1048576,Math.round(requested*(air/128)**3/512)*512));this.w=512;this.h=Math.ceil(this.count/this.w);
  this.substeps=volume.substeps??SUBSTEPS;this.life=Math.max(1,state.cam.smokeParticleLife??options.particleLife??16);this.birthCount=Math.max(1,Math.ceil(this.count/(this.life*state.fps*this.substeps)));
  this.read=makeFineState(this.w,this.h);this.write=makeFineState(this.w,this.h);
  this.position={read:this.read.position.only,write:this.write.position.only};this.mass={read:this.read.velocity,write:this.write.velocity};
  this.raw=makeFBO(volume.aw,volume.ah,F32(),gl.NEAREST);this.density=makeFBO(volume.aw,volume.ah,F16());this.reductions=[];
  let w=volume.aw,h=volume.ah;do{w=Math.max(1,Math.ceil(w/2));h=Math.max(1,Math.ceil(h/2));this.reductions.push(makeFBO(w,h,[gl.RG32F,gl.RG,gl.FLOAT],gl.NEAREST));}while(w>1||h>1);
 }
 swap(){[this.read,this.write]=[this.write,this.read];Object.assign(this.position,{read:this.read.position.only,write:this.write.position.only});Object.assign(this.mass,{read:this.read.velocity,write:this.write.velocity});}
 step(dt,splats,time,sub,carrier,density){
  const v=this.volume,source=splats.filter(s=>s.d>0).slice(0,8),positions=[],data=[],weights=[];
  for(const s of source){const p=s.p.map((x,k)=>(x-v.boxMin[k])/v.vox),r=s.r/v.vox,R=Math.max(2,r),fraction=clippedMass(-p[1]/R);positions.push(...p,R);weights.push(s.d*r**3*smokeKernelIntegral*fraction);data.push(0,s.rate,s.linger,0);}
  const total=weights.reduce((a,b)=>a+b,0);let sum=0;
  for(let n=0;n<weights.length;n++){sum+=weights[n];data[n*4]=total>0?sum/total:1;}
  while(positions.length<32){positions.push(0,0,0,1);data.push(1,0,0,0);}
  const stepIndex=sub-Math.floor(this.inst.frame*this.substeps),birthStart=((stepIndex*this.birthCount)%this.count+this.count)%this.count;
  gl.disable(gl.BLEND);
  pass(this.P.step,this.write.position,this.w,this.h,{...v.u(),uStateSize:{i2:[this.w,this.h]},uPosition:this.position.read.tex,uMass:this.mass.read.tex,uCarrier:carrier,uDensity:density,
   uCount:{i:this.count},uBirthStart:{i:birthStart},uBirthCount:{i:this.birthCount},uSourceCount:{i:total>0?source.length:0},uSeed:{i:this.inst.seed|0},uStepIndex:{i:stepIndex},
   uSource:{v4:positions},uSourceData:{v4:data},uBirthMass:total/this.birthCount,uAge:time-this.inst.frame/state.fps,uDt:dt,uLife:this.life,
   uBreakup:PRESETS[this.inst.preset].breakup3d??0,uBreakupK:v.vox/(this.inst.wscale*.014)});
  this.swap();
 }
 deposit(age){
  const v=this.volume,kernel=state.cam.smokeParticleKernel??smokeParticleSettings(this.inst).particleKernel??1;
  gl.bindFramebuffer(gl.FRAMEBUFFER,this.raw.fbo);gl.viewport(0,0,v.aw,v.ah);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT);
  const active=Math.min(this.count,Math.max(0,Math.ceil(Math.min(age,this.inst.duration/state.fps)*state.fps*this.substeps+1))*this.birthCount);
  if(!active)return;
  setUniforms(this.P.deposit,{...(this.shaderTransform?v.u():{}),uPosition:this.position.read.tex,uMass:this.mass.read.tex,uStateSize:{i2:[this.w,this.h]},uGridDim:v.dim,uTiles:v.tiles,uKernel:{i:kernel}});
  gl.enable(gl.BLEND);gl.blendFunc(gl.ONE,gl.ONE);gl.bindVertexArray(vao);gl.drawArrays(gl.POINTS,0,active*(kernel===2?27:8));gl.disable(gl.BLEND);gl.bindVertexArray(null);
 }
 render(age){
  const v=this.volume;this.deposit(age);let source=v.dye.read,w=v.aw,h=v.ah;
  for(let n=0;n<this.reductions.length;n++){
   const target=this.reductions[n];pass(v.P.opticalReduce,target,target.w,target.h,{...v.u(),uOriginal:source.tex,uModified:this.raw.tex,uInputSize:[w,h],uFirst:{i:n===0?1:0}});source=target;w=target.w;h=target.h;
  }
  pass(this.P.normalize,this.density,v.aw,v.ah,{...v.u(),uOriginal:v.dye.read.tex,uParticles:this.raw.tex,uTotal:source.tex,uColor:lin(this.inst.color),
   uBlend:smokeParticleBlend(this.inst)});
  return this.density.tex;
 }
 destroy(){
  gl.deleteFramebuffer(this.read.position.fbo);gl.deleteFramebuffer(this.write.position.fbo);
  freeFBO(this.raw);freeFBO(this.density);for(const f of this.reductions)freeFBO(f);
 }
}
