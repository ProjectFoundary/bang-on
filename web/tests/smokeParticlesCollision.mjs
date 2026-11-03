// Actual GPU transport of authored smoke cohorts through opt-in finite masks.
// Generated textures only; no source footage, service or model requests.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {writeFileSync} from 'node:fs';
const local=createRequire(new URL('../package.json',import.meta.url));
const runtime=createRequire(process.env.KEYFX_NODE_PACKAGE||new URL('../package.json',import.meta.url));
const {build}=local('esbuild'),{chromium}=runtime('playwright');
const bundle=await build({stdin:{contents:`
 import * as context from './squib/gl/context.js';
 import {SmokeParticles3D} from './squib/sim/smoke-particles3d.js';
 import {supportsFineParticles} from './squib/sim/fine-particles3d.js';
 import {FS3} from './squib/gl/shaders3d.js';
 import {volumeShader} from './volumeShaders';import {state} from './squib/state.js';import {PRESETS} from './squib/presets.js';
 window.K={...context,SmokeParticles3D,supportsFineParticles,FS3,volumeShader,state,PRESETS};
`,resolveDir:fileURLToPath(new URL('../src/',import.meta.url)),loader:'ts'},bundle:true,write:false,format:'iife'});
const browser=await chromium.launch({executablePath:process.env.KEYFX_CHROME||undefined,headless:true,args:process.platform==='darwin'?['--use-angle=metal']:[]});
try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.route('**/*',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><body></body>'}));
 await page.goto('http://smoke-particle-fixture.local');await page.addScriptTag({content:bundle.outputFiles[0].text});
 const result=await page.evaluate(()=>{
  const K=window.K,{gl}=K;K.state.fps=24;K.state.cam={smokeParticleCount:4096,smokeParticleKernel:1,detail:1};
  if(!K.supportsFineParticles())throw Error('Fixture requires EXT_float_blend');
  const dim=[16,8,8],tiles=[4,2],w=64,h=16,maskDim=[8,4,4],maskTiles=[4,1],mw=32,mh=4;
  const F32=()=>[gl.RGBA32F,gl.RGBA,gl.FLOAT],at=(x,y,z)=>(Math.floor(z/4)*8+y)*w+(z%4)*16+x;
  const maskAt=(x,y,z)=>y*mw+z*8+x,read=f=>{const a=new Float32Array(f.w*f.h*4);gl.bindFramebuffer(gl.FRAMEBUFFER,f.fbo);gl.readPixels(0,0,f.w,f.h,gl.RGBA,gl.FLOAT,a);return a;};
  const texture=(fw,fh,values,mask=false)=>{const f=K.makeFBO(fw,fh,mask?[gl.R8,gl.RED,gl.UNSIGNED_BYTE]:F32(),gl.NEAREST);gl.bindTexture(gl.TEXTURE_2D,f.tex);gl.texSubImage2D(gl.TEXTURE_2D,0,0,0,fw,fh,mask?gl.RED:gl.RGBA,mask?gl.UNSIGNED_BYTE:gl.FLOAT,values);return f;};
  const carrierValues=new Float32Array(w*h*4),densityValues=new Float32Array(w*h*4);
  for(let i=0;i<w*h;i++){carrierValues[i*4]=8;densityValues[i*4+3]=.2;}
  const carrier=texture(w,h,carrierValues),density=texture(w,h,densityValues),reduce=K.program(K.volumeShader('opticalReduce',K.FS3.opticalReduce));
  const cases={};let adaptedProgram=null,stockProgram=null;
  for(const name of ['free','wall','blockedBirth','stock']){
   const block=name!=='free',source=name==='blockedBirth'?[11,4,4]:[3,4,4],mask=new Uint8Array(mw*mh);
   if(block)for(let z=0;z<4;z++)for(let y=0;y<4;y++)for(let x=4;x<(name==='blockedBirth'?7:5);x++)mask[maskAt(x,y,z)]=255;
   const solid=texture(mw,mh,mask,true),inst={preset:'plume',frame:0,duration:72,seed:42,color:'#969696',wscale:10};
   if(name!=='stock')inst.volumeFXShader=K.volumeShader;
   const v={inst,coarse:{dim},substeps:3,dim,tiles,boxMin:[0,0,0],vox:1,aw:w,ah:h,dye:{read:density},P:{opticalReduce:reduce},
    u:()=>({uDim:dim,uTiles:tiles,uBoxMin:[0,0,0],uVox:1,uGround:-100,uEdgeStep:1,uVFXSolid:solid.tex,uVFXEnabled:1,
     uVFXMaskDim:maskDim,uVFXMaskTiles:maskTiles,uVFXMaskBoxMin:[0,0,0],uVFXMaskVox:2,uVFXSource:source})};
   const p=new K.SmokeParticles3D(v);if(name==='free')adaptedProgram=p.P;if(name==='wall'&&p.P!==adaptedProgram)throw Error('Stable hooks should reuse their programs');if(name==='stock')stockProgram=p.P;
   const splat={p:source,r:.8,d:.5,rate:.1,linger:1},dt=1/72;
   for(let sub=0;sub<90;sub++)p.step(dt,[splat],sub*dt,sub,carrier.tex,density.tex);
   p.render(90*dt);const position=read(p.position.read),mass=read(p.mass.read),deposited=read(p.raw),normalized=read(p.density);
   let living=0,maxX=-1,solidMass=0,beyondMass=0,solidDensity=0;
   for(let i=0;i<p.count;i++)if(mass[i*4]>1e-12){living++;maxX=Math.max(maxX,position[i*4]);}
   for(let z=0;z<8;z++)for(let y=0;y<8;y++)for(let x=0;x<16;x++){
    const k=at(x,y,z),occupied=block&&x>=8&&x<(name==='blockedBirth'?14:10);
    if(occupied){solidMass=Math.max(solidMass,deposited[k*4+3]);solidDensity=Math.max(solidDensity,normalized[k*4+3]);}
    if(x>=10)beyondMass+=deposited[k*4+3];
   }
   cases[name]={living,maxX,solidMass,solidDensity,beyondMass};
   p.destroy();for(const side of [p.read,p.write]){K.freeFBO(side.position.only);K.freeFBO(side.velocity);}K.freeFBO(solid);
  }
  K.freeFBO(carrier);K.freeFBO(density);return {cases,programsIsolated:adaptedProgram!==stockProgram,error:gl.getError()};
 });
 assert.deepEqual(errors,[]);assert.equal(result.error,0);assert.equal(result.programsIsolated,true);
 assert(result.cases.free.maxX>10,'Free cohorts must actually advect across the wall region');
 assert(result.cases.wall.living>0&&result.cases.wall.maxX<8,'Swept cohorts must stop before a finite hard slab');
 assert.equal(result.cases.wall.solidMass,0);assert.equal(result.cases.wall.beyondMass,0);assert.equal(result.cases.wall.solidDensity,0);
 assert.equal(result.cases.blockedBirth.living,0,'A blocked source must not spawn through the obstacle');
 assert(result.cases.stock.maxX>10,'Opt-in adapter must leave stock Squib transport unchanged');
 const report={pass:true,...result};writeFileSync(process.env.KEYFX_SMOKE_PARTICLE_REPORT||'/tmp/bang-smoke-particle-collision.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser.close();}
