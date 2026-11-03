// Bounded, real GPU acceptance for the authored Squib plume and separate grenade.
// Synthetic depth/mattes only; no app service, source plate, provider or network.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {mkdirSync,writeFileSync} from 'node:fs';
const local=createRequire(new URL('../package.json',import.meta.url));
const runtime=createRequire(process.env.KEYFX_NODE_PACKAGE||new URL('../package.json',import.meta.url));
const {build}=local('esbuild'),{chromium}=runtime('playwright');
const out=process.env.KEYFX_SMOKE_GPU_OUTPUT||'/tmp/bang-smoke-presets-gpu';mkdirSync(out,{recursive:true});
const bundle=await build({stdin:{contents:`
 import * as fx from './volumeFX';import * as geometry from './volumeGeometry';
 import {PRESETS} from './squib/presets.js';import {gl} from './squib/gl/context.js';
 window.K={...fx,...geometry,PRESETS,gl};
`,resolveDir:fileURLToPath(new URL('../src/',import.meta.url)),loader:'ts'},bundle:true,write:false,format:'iife'});
const browser=await chromium.launch({executablePath:process.env.KEYFX_CHROME||undefined,headless:true,args:process.platform==='darwin'?['--use-angle=metal']:[]});
try{
 const page=await browser.newPage();page.setDefaultTimeout(240000);const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.route('**/*',r=>r.fulfill({contentType:'text/html',body:'<!doctype html><body></body>'}));
 await page.goto('http://synthetic-smoke.local');await page.addScriptTag({content:bundle.outputFiles[0].text});
 const defaults=await page.evaluate(()=>{
  const K=window.K,original=JSON.stringify(K.PRESETS.plume),w=128,h=72;
  const scene={key:'hq-default',width:w,height:h,referenceFrame:0,fps:24,sourceTimes:Array.from({length:50},(_,i)=>i/24),
   depth:{width:1,height:1,values:[5],kind:'distance',scale:1,bias:0},camera:{focalPixels:112,focalY:112,homographies:Array.from({length:50},()=>K.IDENTITY3)},mattes:[]};
  K.setVolumeFXScene(scene);
  const options={w,h,n:50,x:64,y:44,start:0,end:49,durationSeconds:2,size:.8,seed:42,quality:'high'};
  const result=K.renderVolumeElement('default-plume','smoke_plume_hq',options,1),field=K.volumeFXDiagnostics('default-plume');
  const alias=K.PRESETS.bang_volume_smoke_plume_hq;
  const report={metadata:result.metadata,positiveDensity:field.totalDensity,liveParticles:field.particles?.live??0,
   noFire:!alias.fire&&!alias.smoke.fire,authoredPresetExact:JSON.stringify(alias)===original,stockUnchanged:JSON.stringify(K.PRESETS.plume)===original,glError:K.gl.getError()};
  K.clearVolumeFX();return report;
 });
 assert.equal(defaults.metadata.profile,'squib-plume-hq-r18-v1');assert.equal(defaults.metadata.emission,'authored-squib');
 assert.equal(defaults.metadata.flowResolution,128);assert.equal(defaults.metadata.particleCount,524288);assert.equal(defaults.metadata.requestedParticleCount,524288);
 assert(Math.abs(defaults.metadata.densityScale-1.4)<1e-9);assert.equal(defaults.metadata.substeps,3);assert.equal(defaults.metadata.renderDetail,.8);
 assert(Math.abs(defaults.metadata.particleBlend-.64)<1e-9);assert.equal(defaults.metadata.cachedDetail,true);assert.equal(defaults.metadata.persistentFine,true);
 assert.equal(defaults.metadata.sourceColour,'#969696');assert.equal(defaults.metadata.bounds,'authored-plume');assert.equal(defaults.metadata.multigrid,true);
 assert(Math.abs(defaults.metadata.rayStep-.56)<1e-9);assert(defaults.positiveDensity>0);assert(defaults.liveParticles>0);
 assert(defaults.noFire&&defaults.authoredPresetExact&&defaults.stockUnchanged);assert.equal(defaults.glError,0);
 console.log('Hero128 plume: actual524288 particles, authored source and fine1.4 density grid pass.');
 const collision=await page.evaluate(()=>{
  const K=window.K,w=192,h=108,wall=new Uint8Array(w*h).fill(255),original=JSON.stringify(K.PRESETS.plume);
  const scene={key:'hq-wall',width:w,height:h,referenceFrame:0,fps:24,sourceTimes:Array.from({length:50},(_,i)=>i/24),
   depth:{width:1,height:1,values:[5],kind:'distance',scale:1,bias:0},camera:{focalPixels:168,focalY:168,homographies:Array.from({length:50},()=>K.IDENTITY3)},
   mattes:[{id:'wall',width:w,height:h,frames:[wall],static:true,coordinates:'reference',role:'both',kind:'hard',thickness:.3}]};
  K.setVolumeFXScene(scene);const o={w,h,n:50,x:96,y:66,start:0,end:49,durationSeconds:2,size:.8,seed:42,quality:'high',resolution:24,matteIds:['wall']};
  const image=canvas=>new Uint8ClampedArray(canvas.getContext('2d').getImageData(0,0,w,h).data);
  const delta=(a,b)=>{let result=0;for(let i=0;i<a.length;i++)result=Math.max(result,Math.abs(a[i]-b[i]));return result;};
  const plume=K.renderVolumeElement('plume','smoke_plume_hq',o,6),pixels=image(plume.element),first=K.volumeFXDiagnostics('plume');
  const cells=K.voxelizeScene(scene,first.grid,6).solid.filter(v=>v>0).length;
  const alphaSum=pixels.reduce((sum,v,i)=>sum+(i%4===3?v:0),0),plumePNG=plume.rgb.toDataURL();
  K.renderVolumeElement('plume','smoke_plume_hq',o,2);
  const again=K.renderVolumeElement('plume','smoke_plume_hq',o,6),second=K.volumeFXDiagnostics('plume');
  const scrub={dye:delta(first.dye,second.dye),positions:delta(first.particles.positions,second.particles.positions),mass:delta(first.particles.mass,second.particles.mass),pixels:delta(pixels,image(again.element))};
  const grenade=K.renderVolumeElement('grenade','smoke_grenade',o,6),gas=K.volumeFXDiagnostics('grenade'),grenadePNG=grenade.rgb.toDataURL();
  const grenadePixels=image(grenade.element),grenadeAlpha=grenadePixels.reduce((sum,v,i)=>sum+(i%4===3?v:0),0);
  const aliases={distinct:K.PRESETS.bang_volume_smoke_plume_hq!==K.PRESETS.bang_volume_smoke_grenade,
   plumeUnchanged:JSON.stringify(K.PRESETS.bang_volume_smoke_plume_hq)===original,
   stockUnchanged:JSON.stringify(K.PRESETS.plume)===original,
   grenadeColour:K.PRESETS.bang_volume_smoke_grenade.defaults.color,
   nativePlumeParticles:K.PRESETS.bang_volume_smoke_plume_hq.smoke.volume.particles,
   grenadeParticles:K.PRESETS.bang_volume_smoke_grenade.smoke.volume.particles};
  const report={metadata:plume.metadata,solidCells:cells,coreDensity:first.coreDensity,coreVelocity:first.coreVelocity,
   particles:{count:first.particles.count,live:first.particles.live,blocked:first.particles.blocked,opticalCore:first.particles.opticalCore},
   alphaSum,scrub,grenade:{metadata:grenade.metadata,alphaSum:grenadeAlpha,totalDensity:gas.totalDensity,coreDensity:gas.coreDensity},
   renderedDifference:delta(pixels,grenadePixels),aliases,glError:K.gl.getError(),plumePNG,grenadePNG};
  K.clearVolumeFX();return report;
 });
 assert(collision.solidCells>0);assert(Math.abs(collision.metadata.densityScale-1.4)<1e-9);assert.equal(collision.metadata.multigrid,false);
 assert.equal(collision.coreDensity,0);assert.equal(collision.coreVelocity,0);assert(collision.particles.live>0);assert.equal(collision.particles.blocked,0);assert.equal(collision.particles.opticalCore,0);
 assert(collision.alphaSum>0);assert.equal(collision.scrub.dye,0);assert.equal(collision.scrub.positions,0);assert.equal(collision.scrub.mass,0);assert.equal(collision.scrub.pixels,0);
 assert.equal(collision.grenade.metadata.profile,'bang-smoke-grenade-v1');assert.equal(collision.grenade.metadata.emission,'expanding-grenade');assert(collision.grenade.alphaSum>0);assert(collision.grenade.totalDensity>0);assert.equal(collision.grenade.coreDensity,0);
 assert(collision.renderedDifference>0);assert(collision.aliases.distinct&&collision.aliases.plumeUnchanged&&collision.aliases.stockUnchanged);
 assert.equal(collision.aliases.grenadeColour,'#d9dadb');assert.equal(collision.aliases.nativePlumeParticles,true);assert.equal(collision.aliases.grenadeParticles,false);assert.equal(collision.glError,0);assert.deepEqual(errors,[]);
 for(const [name,key]of [['plume','plumePNG'],['grenade','grenadePNG']]){writeFileSync(`${out}/${name}.png`,Buffer.from(collision[key].split(',')[1],'base64'));delete collision[key];}
 const previews=await page.evaluate(()=>{
  const K=window.K,w=384,h=216,scene={key:'smoke-comparison',width:w,height:h,referenceFrame:0,fps:24,sourceTimes:Array.from({length:50},(_,i)=>i/24),
   depth:{width:1,height:1,values:[5],kind:'distance',scale:1,bias:0},camera:{focalPixels:336,focalY:336,homographies:Array.from({length:50},()=>K.IDENTITY3)},mattes:[]};
  K.setVolumeFXScene(scene);const results={},o={w,h,n:50,x:192,y:160,start:0,end:49,durationSeconds:2,size:.8,seed:42,quality:'high',resolution:64};
  for(const [name,kind]of [['plume','smoke_plume_hq'],['grenade','smoke_grenade']]){
   const r=K.renderVolumeElement(name,kind,o,18),pixels=r.rgb.getContext('2d').getImageData(0,0,w,h).data;
   let nonblack=0;for(let i=0;i<pixels.length;i+=4)if(pixels[i]||pixels[i+1]||pixels[i+2])nonblack++;
   results[name]={png:r.rgb.toDataURL(),nonblack,metadata:r.metadata};
  }
  K.clearVolumeFX();return results;
 });
 for(const [name,row]of Object.entries(previews)){assert(row.nonblack>0);writeFileSync(`${out}/${name}-frame18.png`,Buffer.from(row.png.split(',')[1],'base64'));delete row.png;}
 let hero;
 if(process.env.KEYFX_SMOKE_HERO==='1'){
  hero=await page.evaluate(()=>{
   const K=window.K,w=480,h=270,scene={key:'smoke-hero',width:w,height:h,referenceFrame:0,fps:24,sourceTimes:Array.from({length:121},(_,i)=>i/24),
    depth:{width:1,height:1,values:[5],kind:'distance',scale:1,bias:0},camera:{focalPixels:420,focalY:420,homographies:Array.from({length:121},()=>K.IDENTITY3)},mattes:[]};
   K.setVolumeFXScene(scene);const rows={},base={w,h,n:121,x:240,y:215,start:0,end:120,durationSeconds:5,size:.8,seed:42,quality:'high'};
   for(const [name,kind,resolution]of [['plume','smoke_plume_hq',128],['grenade','smoke_grenade',64]]){
    const started=performance.now(),r=K.renderVolumeElement(name,kind,{...base,resolution},48),pixels=r.rgb.getContext('2d').getImageData(0,0,w,h).data;
    let nonblack=0;for(let i=0;i<pixels.length;i+=4)if(pixels[i]||pixels[i+1]||pixels[i+2])nonblack++;
    rows[name]={png:r.rgb.toDataURL(),seconds:(performance.now()-started)/1000,nonblack,metadata:r.metadata};
   }
   const error=K.gl.getError();K.clearVolumeFX();return {frame:48,time:2,width:w,height:h,rows,error};
  });
  assert.equal(hero.error,0);for(const [name,row]of Object.entries(hero.rows)){assert(row.nonblack>0);writeFileSync(`${out}/${name}-hero.png`,Buffer.from(row.png.split(',')[1],'base64'));delete row.png;}
 }
 assert.deepEqual(errors,[]);
 const report={pass:true,defaults,collision,previews,hero,errors};writeFileSync(`${out}/report.json`,JSON.stringify(report,null,2));
 console.log(JSON.stringify(report));console.log('Smoke presets GPU acceptance passed: multires collision, particles, optical density and scrub state.');
}finally{await browser.close();}
