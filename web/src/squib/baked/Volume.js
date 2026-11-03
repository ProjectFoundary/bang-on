import {gl,program,pass,makeFBO,freeFBO,F16} from '../gl/context.js';
import {state} from '../state.js';
import {lin} from '../util.js';
import {camAt} from '../io/camera.js';
import {getSmokeCache,cacheClock,cacheUniforms} from './library.js';
import {gpuFrame} from './gpu.js';
import {FS_CACHE_LIGHT,FS_CACHE_RAY} from './shaders.js';
const programs=new Map();
const dot=(a,b)=>a.reduce((s,x,n)=>s+x*b[n],0);
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const norm=a=>{const l=Math.hypot(...a)||1;return a.map(x=>x/l);};
export class BakedVolume{
 static keyFor(inst,res){return JSON.stringify(['baked',getSmokeCache(inst)?.serial??0,inst.frame,inst.w,inst.wdir,inst.wscale,inst.scale,inst.seed,state.scene.wind,res]);}
 constructor(inst,res){
  const asset=getSmokeCache(inst),parametric=asset?.meta.densityEncoding==='gaussian2d',define=source=>parametric?source.replace('#version 300 es','#version 300 es\n#define GAUSSIAN_CACHE'):source;
  if(!programs.has(parametric))programs.set(parametric,{light:program(define(FS_CACHE_LIGHT)),ray:program(define(FS_CACHE_RAY))});this.P=programs.get(parametric);
  this.inst=inst;this.cached=true;this.key=BakedVolume.keyFor(inst,res);this.valid=false;this.frame=null;
  this.parts={debris:[],sparks:[]};this.fields=[];this.snaps=new Map();this.pool=[];this.snapshotBytes=0;this.lightFrame=null;
  const baseCam=camAt(inst.frame),offset=inst.w.map((x,k)=>x-baseCam.pos[k]),depth=Math.abs(dot(offset,baseCam.back));
  // placeInWorld stores distance along the click ray. Cached plate-height units
  // require camera depth, otherwise off-centre effects are oversized in 3D.
  this.unit=inst.wscale*inst.scale/(asset?.meta.baseScale??2)*Math.max(1e-4,depth/Math.max(1e-6,Math.hypot(...offset)));
  const up=norm(inst.wdir),right0=Math.abs(dot(baseCam.right,up))>.99?baseCam.up:baseCam.right,
   right=norm(right0.map((x,k)=>x-up[k]*dot(right0,up))),back=norm(cross(right,up)),yaw=((inst.seed%23)-(1000%23))*.012,c=Math.cos(yaw),s=Math.sin(yaw);
  this.basis=[right.map((x,k)=>x*c+back[k]*s),up,back.map((x,k)=>x*c-right[k]*s)];
  this.recoverCamera=this.basis.map(axis=>-dot(axis,offset)/this.unit);this.recover=!!asset?.meta.reconstructedVolume&&Math.abs(this.recoverCamera[2])>.2;
  const cache=getSmokeCache(inst),bounds=cache?.meta.bounds??{min:[-.4,0,-.4],size:[.8,1.5,.8]};this.localMin=bounds.min.slice();this.localSize=bounds.size.slice();
  if(this.recover){
   const corners=[];for(let n=0;n<8;n++){const p=bounds.min.map((x,k)=>x+((n>>k)&1)*bounds.size[k]),factor=1-p[2]/this.recoverCamera[2];corners.push([this.recoverCamera[0]+(p[0]-this.recoverCamera[0])*factor,this.recoverCamera[1]+(p[1]-this.recoverCamera[1])*factor,p[2]]);}
   this.localMin=bounds.min.map((_,k)=>Math.min(...corners.map(p=>p[k])));this.localSize=this.localMin.map((x,k)=>Math.max(...corners.map(p=>p[k]))-x);
  }
  const lean=(state.scene.wind??0)*(bounds.min[1]+bounds.size[1])/.075;this.localMin[0]+=Math.min(0,lean);this.localSize[0]+=Math.abs(lean);
  const vox=Math.max(...this.localSize)/Math.min(res,128),dim=this.localSize.map(x=>Math.max(8,Math.round(x/vox))),tx=Math.ceil(Math.sqrt(dim[2])),tiles=[tx,Math.ceil(dim[2]/tx)];
  this.coarse={dim,vox,tiles,w:dim[0]*tiles[0],h:dim[1]*tiles[1]};this.dim=this.localSize.map(x=>Math.round(x*256/Math.max(...this.localSize)));
  this.light=makeFBO(this.coarse.w,this.coarse.h,F16());
  const points=[];for(let n=0;n<8;n++){const p=this.localMin.map((x,k)=>x+((n>>k)&1)*this.localSize[k]);points.push(inst.w.map((x,k)=>x+this.unit*this.basis.reduce((sum,b,j)=>sum+b[k]*p[j],0)));}
  const mn=inst.w.map((_,k)=>Math.min(...points.map(p=>p[k]))),mx=inst.w.map((_,k)=>Math.max(...points.map(p=>p[k])));this.box={min:mn,size:mx.map((x,k)=>x-mn[k])};this.boxMin=mn;
 }
 setBudget(){}
 goto(frame){this.frame=frame;this.valid=!!getSmokeCache(this.inst)&&this.inst.intensity>0&&cacheClock(this.inst,frame).elapsed>=0;return this.valid;}
 invalidateFrom(){this.valid=false;this.lightFrame=null;}
 destroy(){freeFBO(this.light);}
 render(T,cam,L,lighting,exporting){
  const i=this.inst,cache=getSmokeCache(i);if(!cache)return;const clock=cacheClock(i,this.frame),pair=cache.pair('density',clock.time),a=gpuFrame(cache,pair.a,'density'),b=gpuFrame(cache,pair.b,'density'),C=this.coarse;
  const local=v=>this.basis.map(axis=>dot(axis,v)),cpos=local(cam.pos.map((x,k)=>(x-i.w[k])/this.unit)),light=local(L);
  const K=cache.meta.densityScale*cache.meta.baseSceneDensity/.022;
  const common={...cacheUniforms(i,this.frame),uDensityA:a.parametric?a.tex:{tex3d:a.tex},uDensityB:b.parametric?b.tex:{tex3d:b.tex},uParamK:K,uRecover:this.recover?1:0,uRecoverCamera:this.recoverCamera,uMinA:a.meta.min,uSizeA:a.meta.size,uMinB:b.meta.min,uSizeB:b.meta.size,uWeight:pair.weight,
   uCacheLod:Math.max(0,1-(state.cam.detail??1))*2};
  const key=JSON.stringify([this.frame,this.inst,state.scene,state.cam.detail,light,common.uWeight]);
  if(this.lightFrame!==key){
   pass(this.P.light,this.light,C.w,C.h,{...common,uDim:C.dim,uTiles:C.tiles,uGridMin:this.localMin,uL:light,uVox:C.vox,uK:K*.8});this.lightFrame=key;
  }
  gl.enable(gl.BLEND);gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);
  const maxDensity=Math.max(...a.meta.dim,...b.meta.dim),voxel=a.parametric?Math.max(a.meta.size[0]/a.meta.dim[0],a.meta.size[1]/a.meta.dim[1],b.meta.size[0]/b.meta.dim[0],b.meta.size[1]/b.meta.dim[1]):Math.max(...a.meta.size,...b.meta.size)/Math.max(1,maxDensity);
  pass(this.P.ray,T.S,T.w,T.h,{...common,uLight:this.light.tex,uLightDim:C.dim,uLightTiles:C.tiles,uLightVox:C.vox,uGridMin:this.localMin,
   uBoxMin:this.localMin,uBoxMax:this.localMin.map((x,k)=>x+this.localSize[k]),uRes:[T.w,T.h],uCam:cpos,uRight:local(cam.right),uUp:local(cam.up),uBack:local(cam.back),uTanH:cam.tanH,uTanV:cam.tanV,
   uStep:Math.max(.0005,voxel*(exporting?.55:.85)),uK:K,uL:light,uShadow:state.scene.shadow,uAmb:lighting.amb,uKey:lighting.key,uSky:lighting.sky,uAlbedo:lin(i.color).map(x=>x*(cache.meta.albedoScale??1))});
  gl.disable(gl.BLEND);
 }
}
