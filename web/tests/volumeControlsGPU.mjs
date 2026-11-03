// Actual WebGL2 coverage/transport acceptance. Generated fixtures only: this
// browser never contacts the application, photography, or a model provider.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {mkdirSync,writeFileSync} from 'node:fs';
const local=createRequire(new URL('../package.json',import.meta.url));
const runtime=createRequire(process.env.KEYFX_NODE_PACKAGE||new URL('../package.json',import.meta.url));
const {build}=local('esbuild'),{chromium}=runtime('playwright');
const output=process.env.KEYFX_CONTROLS_OUTPUT||'/tmp/bang-volume-controls';mkdirSync(output,{recursive:true});
const bundle=await build({stdin:{contents:`
 import * as fx from './volumeFX';import * as geometry from './volumeGeometry';
 import {volumeOptions} from './elementOptions';import {Sim3D} from './squib/sim/Sim3D.js';
 import {gl} from './squib/gl/context.js';import {FS3} from './squib/gl/shaders3d.js';window.K={...fx,...geometry,volumeOptions,Sim3D,gl,FS3};
`,resolveDir:fileURLToPath(new URL('../src/',import.meta.url)),loader:'ts'},bundle:true,write:false,format:'iife'});
const browser=await chromium.launch({executablePath:process.env.KEYFX_CHROME||undefined,headless:true,args:process.platform==='darwin'?['--use-angle=metal']:[]});
try{
 const page=await browser.newPage();page.setDefaultTimeout(240000);const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.route('**/*',r=>r.fulfill({contentType:'text/html',body:'<!doctype html><body></body>'}));
 await page.goto('http://synthetic-volume-controls.local');await page.addScriptTag({content:bundle.outputFiles[0].text});
 // Compile stock programs before the first adapter instance, exercising the
 // ordering which shared/global shader mutation cannot support correctly.
 await page.evaluate(()=>{window.stockVolume=new window.K.Sim3D();window.stockShader=window.K.FS3.forces;});
 const blur=await page.evaluate(()=>{
  const K=window.K,w=240,h=135,mask=new Uint8Array(w*h);
  for(let y=35;y<105;y++)for(let x=80;x<160;x++)mask[y*w+x]=255;
  const m={id:'shelf',width:w,height:h,frames:[mask],static:true,coordinates:'reference',role:'both',kind:'hard',thickness:.35,colliderBlur:0};
  const scene={key:'blur',width:w,height:h,referenceFrame:0,fps:24,depth:{width:w,height:h,values:Array.from(mask,a=>a?4:9),kind:'distance',scale:1,bias:0},camera:{focalPixels:210,focalY:210,homographies:[K.IDENTITY3]},mattes:[m]};
  const grid={dim:[48,32,24],tiles:[5,5],boxMin:[-3,-2,-6],vox:.125,aw:240,ah:160};
  const count=f=>({solid:f.solid.filter(v=>v).length,porous:Array.from(f.motion).filter((v,i)=>i%4===3&&v>.001).length});
  const planes={},fields={};for(const sigma of [0,32,128]){
   m.colliderBlur=sigma;K.validateVolumeScene(scene);const p=K.colliderPlane(scene,m,0),f=K.voxelizeScene(scene,grid,0),c=document.createElement('canvas');c.width=p.width;c.height=p.height;
   const ctx=c.getContext('2d'),pixels=ctx.createImageData(c.width,c.height);for(let i=0;i<p.values.length;i++){pixels.data[i*4]=pixels.data[i*4+1]=pixels.data[i*4+2]=p.values[i]*255;pixels.data[i*4+3]=255;}ctx.putImageData(pixels,0,0);
   planes[sigma]={png:c.toDataURL(),outside:K.sample(p.values,p.width,p.height,72,70),fringeDepth:K.sample(p.depths,p.width,p.height,72,70),maximum:Math.max(...p.values)};fields[sigma]={...count(f),holdout:f.holdout};
  }
  const same=(a,b)=>a.every((v,i)=>v===b[i]),rawHoldoutUnchanged=same(fields[0].holdout,fields[32].holdout)&&same(fields[0].holdout,fields[128].holdout);
  for(const f of Object.values(fields))delete f.holdout;
  return {planes,fields,rawHoldoutUnchanged};
 });
 assert(blur.fields[0].solid>0);assert.equal(blur.fields[0].porous,0);assert(blur.fields[32].solid<blur.fields[0].solid);assert(blur.fields[32].porous>0);
 assert(blur.fields[128].solid<blur.fields[32].solid);assert(blur.fields[128].porous>blur.fields[32].porous);assert(blur.rawHoldoutUnchanged);
 assert.equal(blur.planes[0].outside,0);assert(blur.planes[32].outside>0);assert(Math.abs(blur.planes[32].fringeDepth-4)<.001);
 for(const [sigma,row]of Object.entries(blur.planes)){writeFileSync(`${output}/collider-${sigma}.png`,Buffer.from(row.png.split(',')[1],'base64'));delete row.png;}
 console.log('Collider blur: solid core softens into a real porous fringe, foreground depth extends correctly, raw holdout unchanged at sigma0/32/128.');
 const wind=await page.evaluate(()=>{
  const K=window.K,w=320,h=180,n=73,times=Array.from({length:n},(_,i)=>i/24),scene={key:'wind',width:w,height:h,referenceFrame:0,fps:24,sourceTimes:times,
   depth:{width:1,height:1,values:[5],kind:'distance',scale:1,bias:0},camera:{focalPixels:280,focalY:280,homographies:times.map(()=>K.IDENTITY3)},mattes:[]};
  const project={frames:n,fps:24,native_profile:{fps_num:24,fps_den:1,timestamps:times},settings:{seed:42}};
  const effect={id:'blast',preset:'explosion',tracking:'camera',keys:[{phase:'onset',frame:0}],anchors:[{frame:0,x:.5,y:.72}],size:.5,element:{duration_s:3,colliders:[]}};
  const centroid=d=>{const g=d.grid,sum=[0,0,0];let mass=0;for(let z=0;z<g.dim[2];z++)for(let y=0;y<g.dim[1];y++)for(let x=0;x<g.dim[0];x++){
   const rho=d.dye[K.gridIndex(g,x,y,z)*4+3];if(!Number.isFinite(rho))throw Error('Nonfinite density');mass+=rho;for(let a=0;a<3;a++)sum[a]+=rho*(g.boxMin[a]+([x,y,z][a]+.5)*g.vox);
  }return {mass,world:sum.map(q=>q/mass)};};
  K.setVolumeFXScene(scene);const rows={};
  for(const [name,strength,angle]of [['none',0,90],['right',1,90],['left',1,270],['up',1,0]]){
   const mapped=K.volumeOptions(project,{...effect,angle,strength},0,new Map(),w,h),o={...mapped,resolution:48,quality:'draft'};
   const early=K.renderVolumeElement(name,'explosion',o,12),earlyPNG=early.rgb.toDataURL(),a=centroid(K.volumeFXDiagnostics(name));
   const late=K.renderVolumeElement(name,'explosion',o,60),d=K.volumeFXDiagnostics(name),b=centroid(d);
   K.renderVolumeElement(name,'explosion',o,24);K.renderVolumeElement(name,'explosion',o,60);const again=K.volumeFXDiagnostics(name);
   let scrub=0;for(let i=0;i<d.dye.length;i++)scrub=Math.max(scrub,Math.abs(d.dye[i]-again.dye[i]));
   rows[name]={early:a,late:b,windVector:mapped.windVector,windWorld:d.windWorld,response:d.windResponse,debris:d.debris,sparks:d.sparks,scrub,earlyPNG};
  }
  const native=new K.Sim3D(),uniformNames=['uVFXEnabled','uVFXMaskVox','uVFXRay','uVFXCamera','uVFXWindK'];
  const nativeUniforms=Object.fromEntries(uniformNames.map(name=>[name,Object.values(native.P).some(p=>p.loc(name)!==null)]));
  const stockProgramsUnchanged=native.P===window.stockVolume.P,stockShaderUnchanged=K.FS3.forces===window.stockShader;
  native.destroy();window.stockVolume.destroy();const glError=K.gl.getError();K.clearVolumeFX();return {rows,nativeUniforms,stockProgramsUnchanged,stockShaderUnchanged,glError};
 });
 const wr=wind.rows;for(const row of Object.values(wr)){assert(row.early.mass>0);assert(row.late.mass>0);assert.equal(row.debris,0);assert.equal(row.sparks,0);assert.equal(row.scrub,0);}
 assert(wr.right.late.world[0]>wr.none.late.world[0]+.5);assert(wr.left.late.world[0]<wr.none.late.world[0]-.5);assert(wr.up.late.world[1]>wr.none.late.world[1]+.5);
 assert(wr.right.late.world[0]-wr.right.early.world[0]>.5);assert(Math.abs(wr.up.windVector[0])<1e-8);assert(wr.up.windVector[1]>0);assert(wr.right.windVector[0]>0);assert(Math.abs(wr.right.windVector[1])<1e-8);
 assert(Object.values(wind.nativeUniforms).every(present=>!present));assert(wind.stockProgramsUnchanged&&wind.stockShaderUnchanged);assert.equal(wind.glError,0);
 for(const [name,row]of Object.entries(wr)){writeFileSync(`${output}/wind-${name}-frame12.png`,Buffer.from(row.earlyPNG.split(',')[1],'base64'));delete row.earlyPNG;}
 console.log('Explosion wind: actual late smoke density drifts left/right/up, emitted afterburn is over, zero debris/sparks, exact scrub and no leaked native wind uniform.');
 const tracking=await page.evaluate(()=>{
  const K=window.K,w=240,h=135,n=49,times=Array.from({length:n},(_,i)=>i/24),camera=times.map((_,f)=>[1,0,f*.4,0,1,0,0,0,1]);
  const scene={key:'tracking',width:w,height:h,referenceFrame:0,fps:24,sourceTimes:times,depth:{width:1,height:1,values:[5],kind:'distance',scale:1,bias:0},camera:{focalPixels:210,focalY:210,homographies:camera},mattes:[]};
  const o={w,h,n,x:100,y:85,start:0,end:48,durationSeconds:2,size:.35,seed:42,quality:'draft',resolution:32};
  const centroid=d=>{const g=d.grid;let xsum=0,mass=0;for(let z=0;z<g.dim[2];z++)for(let y=0;y<g.dim[1];y++)for(let x=0;x<g.dim[0];x++){
   const rho=d.dye[K.gridIndex(g,x,y,z)*4+3];xsum+=rho*(g.boxMin[0]+(x+.5)*g.vox);mass+=rho;
  }return {mass,x:xsum/mass};};
  const rows={};K.setVolumeFXScene(scene);
  for(const mode of ['camera','features','screen']){
   const path=new Float32Array(n*2);for(let f=0;f<n;f++){path[f*2]=(100+(mode==='features'?f*1.2:0))/w*960;path[f*2+1]=85/h*540;}
   const options={...o,tracking:mode,path:mode==='camera'?undefined:path},r=K.renderVolumeElement(mode,'smoke_grenade',options,48),d=K.volumeFXDiagnostics(mode);
   const uv=K.projectH(camera[48],...K.project(scene,d.sourceWorld)),first=new Float32Array(d.dye);
   K.renderVolumeElement(mode,'smoke_grenade',options,12);K.renderVolumeElement(mode,'smoke_grenade',options,48);const again=K.volumeFXDiagnostics(mode);
   let scrub=0;for(let i=0;i<first.length;i++)scrub=Math.max(scrub,Math.abs(first[i]-again.dye[i]));
   rows[mode]={uv,sourceWorld:d.sourceWorld,field:centroid(d),scrub,metadata:r.metadata,png:r.rgb.toDataURL()};
  }
  const glError=K.gl.getError();K.clearVolumeFX();return {rows,glError};
 });
 assert(Math.abs(tracking.rows.camera.uv[0]-119.2)<1e-5);assert(Math.abs(tracking.rows.features.uv[0]-157.6)<1e-4);assert(Math.abs(tracking.rows.screen.uv[0]-100)<1e-5);
 assert(tracking.rows.features.field.x>tracking.rows.camera.field.x+.2);assert(tracking.rows.screen.field.x<tracking.rows.camera.field.x-.05);
 for(const [name,row]of Object.entries(tracking.rows)){assert(row.field.mass>0);assert.equal(row.scrub,0);writeFileSync(`${output}/tracking-${name}.png`,Buffer.from(row.png.split(',')[1],'base64'));delete row.png;}
 assert.equal(tracking.glError,0);assert.deepEqual(errors,[]);
 const report={pass:true,blur,wind,tracking};writeFileSync(`${output}/report.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 console.log('Source tracking: scene-fixed, feature-following and screen-pinned emitters have the expected projected positions and physically different gas fields.');
}finally{await browser.close();}
