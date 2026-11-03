// Independent optical masks: synthetic geometry, real GPU capture and mocked
// HTTP mask loading only. No application, source plate or provider requests.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {deflateSync} from 'node:zlib';
import {writeFileSync} from 'node:fs';
const local=createRequire(new URL('../package.json',import.meta.url));
const runtime=createRequire(process.env.KEYFX_NODE_PACKAGE||new URL('../package.json',import.meta.url));
const {build}=local('esbuild'),{chromium}=runtime('playwright');
const bundle=await build({stdin:{contents:`
 import * as geometry from './volumeGeometry';import * as fx from './volumeFX';import * as scene from './volumeScene';
 import {gl} from './squib/gl/context.js';window.K={...geometry,...fx,...scene,gl};
`,resolveDir:fileURLToPath(new URL('../src/',import.meta.url)),loader:'ts'},bundle:true,write:false,format:'iife'});
const crcTable=Array.from({length:256},(_,n)=>{for(let i=0;i<8;i++)n=n&1?0xedb88320^(n>>>1):n>>>1;return n>>>0;});
const crc=bytes=>{let n=0xffffffff;for(const b of bytes)n=crcTable[(n^b)&255]^(n>>>8);return (n^0xffffffff)>>>0;};
const pngs=new Map();
function grayPNG(width,height,value){
 const key=`${width}:${height}:${value}`;if(pngs.has(key))return pngs.get(key);
 const chunk=(name,data)=>{const kind=Buffer.from(name),length=Buffer.alloc(4),check=Buffer.alloc(4);length.writeUInt32BE(data.length);check.writeUInt32BE(crc(Buffer.concat([kind,data])));return Buffer.concat([length,kind,data,check]);};
 const ihdr=Buffer.alloc(13);ihdr.writeUInt32BE(width,0);ihdr.writeUInt32BE(height,4);ihdr[8]=8;ihdr[9]=0;
 const rows=Buffer.alloc((width+1)*height,value);for(let y=0;y<height;y++)rows[y*(width+1)]=0;
 const png=Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',ihdr),chunk('IDAT',deflateSync(rows)),chunk('IEND',Buffer.alloc(0))]);pngs.set(key,png);return png;
}
const browser=await chromium.launch({executablePath:process.env.KEYFX_CHROME||undefined,headless:true,args:process.platform==='darwin'?['--use-angle=metal']:[]});
try{
 const page=await browser.newPage();page.setDefaultTimeout(240000);const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 const requests=[],versions={current:'v1'};
 await page.route('**/*',async route=>{
  const url=new URL(route.request().url());requests.push(url.pathname+url.search);
  if(url.pathname.endsWith('/geometry/scene')){
   const isStatic=url.pathname.includes('/fixture-static/'),n=isStatic?25:600,fps=isStatic?24:120,version=versions.current;
   return route.fulfill({contentType:'application/json',body:JSON.stringify({key:version,width:960,height:540,referenceFrame:0,fps,sourceTimes:Array.from({length:n},(_,i)=>i/fps),collisionMode:'matte2d',
    depth:{width:1,height:1,values:[1],kind:'inverse',scale:4,bias:.05},camera:{focalPixels:900,homographies:Array.from({length:n},()=>[1,0,0,0,1,0,0,0,1])},
    mattes:[{id:'person',width:1920,height:1080,static:isStatic,coordinates:isStatic?'reference':'source',role:'both',kind:'soft',thickness:.35,holdoutPost:true,
     maskUrl:'/synthetic/physical/{frame}.png',holdoutWidth:1920,holdoutHeight:1080,holdoutMaskUrl:`/api/projects/fixture/people-alpha/${version}/frames/{nativeFrame}.png`}]} )});
  }
  if(url.pathname.startsWith('/synthetic/physical/'))return route.fulfill({contentType:'image/png',body:grayPNG(256,144,64)});
  if(url.pathname.includes('/people-alpha/')){
   const edge=Number(url.searchParams.get('max_edge')),frame=Number(url.pathname.match(/(\d+)\.png$/)[1]),value=(url.pathname.includes('/v2/')?160:96)+frame%31;
   return route.fulfill({contentType:'image/png',body:grayPNG(edge,Math.round(edge*1080/1920),value)});
  }
  return route.fulfill({contentType:'text/html',body:'<!doctype html><body></body>'});
 });
 await page.goto('http://independent-holdout.local');await page.addScriptTag({content:bundle.outputFiles[0].text});
 const geometry=await page.evaluate(()=>{
  const K=window.K,w=16,h=8,raw=new Uint8Array(4*2);for(let y=0;y<2;y++)for(let x=2;x<4;x++)raw[y*4+x]=255;
  const optical=new Uint8Array(w*h);for(let y=0;y<h;y++)for(let x=9;x<14;x++)optical[y*w+x]=255;optical[3*w+2]=128;
  const matte={id:'person',width:4,height:2,frames:[raw],static:true,coordinates:'reference',role:'both',kind:'hard',thickness:.3};
  const scene={key:'separate',width:w,height:h,referenceFrame:0,fps:24,collisionMode:'matte2d',depth:{width:w,height:h,values:Array.from({length:w*h},(_,i)=>i%w<8?3:7),kind:'distance',scale:1,bias:0},camera:{focalPixels:8,focalY:8,homographies:[K.IDENTITY3,K.IDENTITY3]},mattes:[matte]};
  const grid={dim:[32,16,24],tiles:[5,5],boxMin:[-5,-2.5,-8],vox:.3125,aw:160,ah:80};
  const base=K.voxelizeScene(scene,grid,0),hq={...matte,holdoutWidth:w,holdoutHeight:h,holdoutFrames:new Map([[0,optical]])},hqScene={...scene,mattes:[hq]},refined=K.voxelizeScene(hqScene,grid,0),post=K.voxelizeScene({...hqScene,mattes:[{...hq,holdoutPost:true}]},grid,0);
  const delta=(a,b)=>{let max=0;for(let i=0;i<a.length;i++)max=Math.max(max,Math.abs(a[i]-b[i]));return max;};
  const rawHair=K.matteCoverage(scene,matte,0,2,3),hqHair=K.holdoutCoverage(hqScene,hq,0,2,3),blurredHair=K.holdoutCoverage(hqScene,{...hq,colliderBlur:128},0,2,3);
  const dynamic={...hq,static:false,coordinates:'source',frames:new Map([[0,raw],[1,raw]]),holdoutFrames:new Map([[1,optical]])};
  const cameraScene={...scene,camera:{...scene.camera,homographies:[K.IDENTITY3,[1,0,2,0,1,0,0,0,1]]},mattes:[dynamic]};
  const mapped=K.holdoutCoverage(cameraScene,dynamic,1,0,3),missing=K.holdoutCoverage(cameraScene,dynamic,2,2,3);
  const staticRawHQ={...dynamic,static:true,coordinates:'reference'},mappedStaticRaw=K.holdoutCoverage(cameraScene,staticRawHQ,1,0,3);
  const postActor={...hq,holdoutPost:true},other={...matte,id:'other',role:'holdout',frames:[new Uint8Array(8).fill(64)]},both={...scene,mattes:[postActor,other]};
  const all=K.fallbackVisibility(both,0,2,3,10),excluded=K.fallbackVisibility(both,0,2,3,10,'exclude'),only=K.fallbackVisibility(both,0,2,3,10,'only');
  const depthScene={...both,collisionMode:'depth3d'},stablePostDepth={...depthScene,mattes:[{...postActor,holdoutDepth:6,depthOffset:1}]};
  const moving={...matte,static:false,coordinates:'source'},leftOnly=new Uint8Array(w*h);for(let y=0;y<h;y++)for(let x=0;x<8;x++)leftOnly[y*w+x]=255;
  const movingHQ={...moving,holdoutWidth:w,holdoutHeight:h,holdoutFrames:[leftOnly]},movingRawScene={...scene,mattes:[moving]},movingHQScene={...scene,mattes:[movingHQ]};
  K.validateVolumeScene(hqScene);let invalid=false;try{K.validateVolumeScene({...hqScene,mattes:[{...hq,holdoutWidth:17}]});}catch{invalid=true;}
  return {rawHair,hqHair,blurredHair,mapped,mappedStaticRaw,missing,solidDelta:delta(base.solid,refined.solid),motionDelta:delta(base.motion,refined.motion),holdoutDelta:delta(base.holdout,refined.holdout),
   postSolidDelta:delta(base.solid,post.solid),postMotionDelta:delta(base.motion,post.motion),postHoldoutMax:Math.max(...post.holdout),depthBefore:K.matteDepth(scene,matte,2,3),depthAfter:K.matteDepth(hqScene,hq,2,3),all,excluded,only,
   sourceInFront:K.fallbackVisibility(depthScene,0,2,3,2,'only'),sourceBehind:K.fallbackVisibility(depthScene,0,2,3,10,'only'),stableDepthFront:K.fallbackVisibility(stablePostDepth,0,2,3,6.5,'only'),stableDepthBack:K.fallbackVisibility(stablePostDepth,0,2,3,7.5,'only'),
   emitterRaw:K.emitterDepth({...scene,collisionMode:'depth3d'},0,12,3,.2),emitterPost:K.emitterDepth({...scene,collisionMode:'depth3d',mattes:[{...hq,holdoutPost:true,holdoutDepth:6.8}]},0,12,3,.2),movingDepthRaw:K.matteDepth(movingRawScene,moving,2,3),movingDepthHQ:K.matteDepth(movingHQScene,movingHQ,2,3),invalid};
 });
 assert.equal(geometry.rawHair,0);assert.equal(geometry.hqHair,128/255);assert.equal(geometry.blurredHair,geometry.hqHair);assert.equal(geometry.mapped,128/255);assert.equal(geometry.mappedStaticRaw,128/255);assert.equal(geometry.missing,0);
 assert.equal(geometry.solidDelta,0);assert.equal(geometry.motionDelta,0);assert(geometry.holdoutDelta>0);assert.equal(geometry.postSolidDelta,0);assert.equal(geometry.postMotionDelta,0);assert.equal(geometry.postHoldoutMax,0);assert.equal(geometry.depthAfter,geometry.depthBefore);
 assert(Math.abs(geometry.all-geometry.excluded*geometry.only)<1e-12);assert.equal(geometry.sourceInFront,1);assert.equal(geometry.sourceBehind,127/255);assert.equal(geometry.stableDepthFront,1);assert.equal(geometry.stableDepthBack,127/255);assert.equal(geometry.emitterRaw,geometry.emitterPost);assert.equal(geometry.movingDepthRaw,7);assert.equal(geometry.movingDepthHQ,7);assert(geometry.invalid);
 const gpu=await page.evaluate(()=>{
  const K=window.K,w=192,h=108,n=25,scene={key:'post-gpu',width:w,height:h,referenceFrame:0,fps:24,collisionMode:'matte2d',depth:{width:1,height:1,values:[5],kind:'distance',scale:1,bias:0},camera:{focalPixels:168,focalY:168,homographies:Array.from({length:n},()=>K.IDENTITY3)},
   mattes:[{id:'actor',width:12,height:7,frames:[new Uint8Array(12*7).fill(64)],static:true,coordinates:'reference',role:'both',kind:'soft',thickness:.35,holdoutPost:true,holdoutWidth:w,holdoutHeight:h,holdoutFrames:new Map(Array.from({length:n},(_,f)=>[f,new Uint8Array(w*h).fill(128)]))},
    {id:'other',width:12,height:7,frames:[new Uint8Array(12*7).fill(64)],static:true,coordinates:'reference',role:'holdout',kind:'hard',thickness:.35}]};
  K.setVolumeFXScene(scene);const options={w,h,n,x:96,y:75,start:0,end:24,durationSeconds:1,size:.5,seed:42,resolution:24,quality:'draft',matteIds:['actor','other']};
  const image=c=>c.getContext('2d').getImageData(0,0,w,h).data;
  const preview=K.renderVolumeElement('fx','smoke_grenade',options,12,'preview'),a=K.volumeFXDiagnostics('fx','preview'),previewRaw=new Uint8ClampedArray(image(preview.element)),previewHeld=new Uint8ClampedArray(image(preview.composite)),pv=new Uint8ClampedArray(image(preview.visibility));
  const capture=K.renderVolumeElement('fx','smoke_grenade',options,12,'capture'),b=K.volumeFXDiagnostics('fx','capture'),cv=image(capture.visibility),captureHeld=image(capture.composite);
  let densityDelta=0,previewError=0,captureError=0,alphaSum=0,visibilityPreviewError=0,visibilityCaptureError=0;
  for(let i=0;i<a.dye.length;i++)densityDelta=Math.max(densityDelta,Math.abs(a.dye[i]-b.dye[i]));
  for(let i=0;i<w*h;i++){
   const rawAlpha=previewRaw[i*4+3];alphaSum+=rawAlpha;const x=i%w,y=Math.floor(i/w);
   // The raw proxy is resampled bilinearly, so keep clear of its border.
   if(x<9||x>w-10||y<9||y>h-10)continue;
   previewError=Math.max(previewError,Math.abs(previewHeld[i*4+3]-Math.round(rawAlpha*191/255*127/255)));
   captureError=Math.max(captureError,Math.abs(captureHeld[i*4+3]-Math.round(rawAlpha*191/255)));
   visibilityPreviewError=Math.max(visibilityPreviewError,Math.abs(pv[i*4]-95));visibilityCaptureError=Math.max(visibilityCaptureError,Math.abs(cv[i*4]-191));
  }
  const captureMetadata=capture.metadata,previewMetadata=preview.metadata;K.clearVolumeFX();
  const actor={...scene.mattes[0]};delete actor.holdoutFrames;K.setVolumeFXScene({...scene,key:'revert',mattes:[actor,scene.mattes[1]]});
  const reverted=K.renderVolumeElement('revert','smoke_grenade',options,12),revertVisibility=image(reverted.visibility)[(54*w+96)*4];K.clearVolumeFX();
  K.setVolumeFXScene({...scene,key:'depth-preview',collisionMode:'depth3d'});const depth=K.renderVolumeElement('depth','smoke_grenade',options,12),depthMetadata=depth.metadata;K.clearVolumeFX();
  return {densityDelta,previewError,captureError,visibilityPreviewError,visibilityCaptureError,alphaSum,captureMetadata,previewMetadata,revertVisibility,depthMetadata,glError:K.gl.getError()};
 });
 assert(gpu.alphaSum>0);assert.equal(gpu.densityDelta,0);assert.equal(gpu.previewError,0);assert.equal(gpu.captureError,0);assert.equal(gpu.visibilityPreviewError,0);assert.equal(gpu.visibilityCaptureError,0);assert.equal(gpu.revertVisibility,143);
 assert.deepEqual(gpu.captureMetadata.excludedPostHoldoutIds,['actor']);assert.match(gpu.depthMetadata.postHoldoutVisibility,/Approximate/);assert.equal(gpu.glError,0);
 const first=await page.evaluate(async()=>{
  const K=window.K,n=600,project={id:'fixture',frames:n,native_profile:{fps:120,width:1920,height:1080,timestamps:Array.from({length:n},(_,i)=>i/120)},geometry:{collision_mode:'matte2d',mattes:[{id:'person',holdout:{take_id:'v1'}}]},effects:[{id:'fx',preset:'smoke_grenade',keys:[{phase:'start',frame:0}],element:{duration_s:5,colliders:['person']}}],settings:{}};
  window.projectFixture=project;window.firstScene=await K.loadVolumeScene(project,960,540);const m=window.firstScene.mattes[0];return {frames:m.frames.size,holdoutFrames:m.holdoutFrames.size,width:m.width,height:m.height,holdoutWidth:m.holdoutWidth,holdoutHeight:m.holdoutHeight,frame19:m.holdoutFrames.get(19)[0],cache:K.volumeOpticalCacheStats()};
 });
 versions.current='v2';
 const second=await page.evaluate(async()=>{
  const K=window.K,p={...window.projectFixture,geometry:{...window.projectFixture.geometry,mattes:[{id:'person',holdout:{take_id:'v2'}}]}};window.secondScene=await K.loadVolumeScene(p,960,540);const m=window.secondScene.mattes[0];return {frames:m.holdoutFrames.size,frame19:m.holdoutFrames.get(19)[0],previousRetained:window.firstScene.mattes[0].holdoutFrames.size,cache:K.volumeOpticalCacheStats()};
 });
 versions.current='v1';
 const reload=await page.evaluate(async()=>{const K=window.K,scene=await K.loadVolumeScene(window.projectFixture,960,540),m=scene.mattes[0];return {same:scene===window.firstScene,frames:m.holdoutFrames.size,frame19:m.holdoutFrames.get(19)[0],cache:K.volumeOpticalCacheStats()};});
 const staticHQ=await page.evaluate(async()=>{
  const K=window.K,n=25,p={...window.projectFixture,id:'fixture-static',frames:n,native_profile:{fps:24,width:1920,height:1080,timestamps:Array.from({length:n},(_,i)=>i/24)}};
  const scene=await K.loadVolumeScene(p,192,108),m=scene.mattes[0];return {rawFrames:m.frames.size,opticalFrames:m.holdoutFrames.size,coordinates:m.coordinates,holdoutCoordinates:m.holdoutCoordinates,frame19:m.holdoutFrames.get(19)[0]};
 });
 assert.equal(first.frames,600);assert.equal(first.holdoutFrames,600);assert.equal(first.width,256);assert(first.holdoutWidth>first.width);assert(first.holdoutWidth<=1024);assert.equal(first.frame19,115);
 assert.equal(second.frames,600);assert.equal(second.frame19,179);assert(second.previousRetained<600);assert.equal(reload.same,true);assert.equal(reload.frames,600);assert.equal(reload.frame19,115);
 for(const row of [first,second,reload]){assert(row.cache.bytes<=row.cache.limit);assert.equal(row.cache.pending,0);}
 assert.equal(staticHQ.rawFrames,1);assert.equal(staticHQ.opticalFrames,25);assert.equal(staticHQ.coordinates,'reference');assert.equal(staticHQ.holdoutCoordinates,'source');assert.equal(staticHQ.frame19,115);
 assert(requests.some(url=>url.includes('/people-alpha/v1/')&&url.includes('max_edge=')));assert(requests.some(url=>url.includes('/people-alpha/v2/')));assert.deepEqual(errors,[]);
 const report={pass:true,geometry,gpu,loader:{first,second,reload,staticHQ,requests:requests.length}};writeFileSync(process.env.KEYFX_HOLDOUT_REPORT||'/tmp/bang-holdout-integration.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 console.log('Independent HQ holdout passes: raw physics unchanged; native-frame optical masks; capture excludes post alpha; preview applies once; versioned bounded proxy cache.');
}finally{await browser.close();}
