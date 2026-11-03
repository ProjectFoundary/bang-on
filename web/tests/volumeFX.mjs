// Real GPU finite-depth collision + camera/matte geometry regression.
// Isolated headless browser, generated fixtures only, no service/model requests.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {writeFileSync} from 'node:fs';
const local=createRequire(new URL('../package.json',import.meta.url));
const runtime=createRequire(process.env.KEYFX_NODE_PACKAGE||new URL('../package.json',import.meta.url));
const {build}=local('esbuild'),{chromium}=runtime('playwright');
const bundle=await build({stdin:{contents:`
 import * as geometry from './volumeGeometry';import * as fx from './volumeFX';
 import {Sim3D} from './squib/sim/Sim3D.js';import {state,app} from './squib/state.js';
 import {PRESETS} from './squib/presets.js';import {gl,makeTex} from './squib/gl/context.js';
 window.K={...geometry,...fx,Sim3D,state,app,PRESETS,gl,makeTex};
`,resolveDir:fileURLToPath(new URL('../src/',import.meta.url)),loader:'ts'},bundle:true,write:false,format:'iife'});
const browser=await chromium.launch({executablePath:process.env.KEYFX_CHROME||undefined,headless:true,args:process.platform==='darwin'?['--use-angle=metal']:[]});
try{
 const page=await browser.newPage();page.setDefaultTimeout(240000);const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.route('**/*',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><body></body>'}));
 await page.goto('http://volume-fixture.local');await page.addScriptTag({content:bundle.outputFiles[0].text});
 const geometry=await page.evaluate(()=>{
  const K=window.K,w=200,h=100,mask=new Uint8Array(w*h),moved=new Uint8Array(w*h);
  for(let y=30;y<70;y++)for(let x=70;x<100;x++){mask[y*w+x]=255;moved[y*w+x+6]=255;}
  const scene={key:'fixture',width:w,height:h,referenceFrame:0,fps:24,sourceTimes:[0,1/24],depth:{width:2,height:2,values:[5,5,5,5],kind:'distance',scale:1,bias:0},camera:{focalPixels:100,focalY:100,homographies:[K.IDENTITY3,K.IDENTITY3]},mattes:[{id:'actor',width:w,height:h,frames:[mask,moved],role:'both',kind:'hard',thickness:.3}]};
  K.validateVolumeScene(scene);const p=K.unproject(scene,85,45),uv=K.project(scene,p);
  const g={dim:[24,12,24],tiles:[5,5],boxMin:[-3,-1.5,-6.5],vox:.25,aw:120,ah:60};
  const hard=K.voxelizeScene(scene,g,1),softScene={...scene,key:'soft',mattes:[{...scene.mattes[0],kind:'soft',softRadius:8}]},soft=K.voxelizeScene(softScene,g,1);
  const cameraOnly={...softScene,key:'camera-only',camera:{...scene.camera,homographies:[K.IDENTITY3,[1,0,6,0,1,0,0,0,1]]}},cam=K.voxelizeScene(cameraOnly,g,1);
  let vx=0,cameraVelocity=0,softDrag=0;for(let i=0;i<soft.motion.length;i+=4){vx=Math.max(vx,Math.abs(soft.motion[i]));cameraVelocity=Math.max(cameraVelocity,Math.abs(cam.motion[i]));softDrag=Math.max(softDrag,soft.motion[i+3]);}
  const depths=new Set();for(let z=0;z<24;z++)for(let y=0;y<12;y++)for(let x=0;x<24;x++)if(hard.solid[K.gridIndex(g,x,y,z)])depths.add(z);
  const front=K.fallbackVisibility(scene,0,85,45,4),back=K.fallbackVisibility(scene,0,85,45,6);
  const bodyMask=new Uint8Array(16*8),bodyMoved=new Uint8Array(16*8);for(let y=1;y<7;y++)for(let x=2;x<5;x++){bodyMask[y*16+x]=255;bodyMoved[y*16+x+8]=255;}
  const body={id:'moving-depth',width:16,height:8,frames:[bodyMask,bodyMoved],role:'both',kind:'soft',thickness:.2},bodyScene={...scene,key:'moving-depth',width:16,height:8,mattes:[body],depth:{width:16,height:8,values:Array.from({length:16*8},(_,i)=>i%16<8?3:8),kind:'distance',scale:1,bias:0}};
  const bodyDepth=K.matteDepth(bodyScene,body,11,4),backgroundDepth=K.depthAt(bodyScene,11,4),emitter=K.emitterDepth({...scene,mattes:[{...scene.mattes[0],static:true,depthOffset:-.3}]},0,85,45,.2,0);
  return {uv,world:p,hardCells:hard.solid.filter(x=>x>0).length,occupiedZ:[...depths],softSolid:soft.solid.some(x=>x>0),softDrag,vx,cameraVelocity,front,back,bodyDepth,backgroundDepth,emitter};
 });
 console.log(JSON.stringify({geometry}));assert.deepEqual(geometry.uv,[85,45]);assert(geometry.hardCells>0);assert(geometry.occupiedZ.length<6);assert.equal(geometry.softSolid,false);assert(geometry.softDrag>0);assert(geometry.vx>0);assert(geometry.cameraVelocity<1e-4);assert.equal(geometry.front,1);assert.equal(geometry.back,0);
 assert.equal(geometry.bodyDepth,3);assert.equal(geometry.backgroundDepth,8);assert(geometry.emitter<4.7);
 console.log('Geometry: finite depth, genuine soft volume influence, camera cancellation and front/back visibility pass.');
 const rendering=await page.evaluate(()=>{
  const K=window.K,scene={key:'render-fixture',width:320,height:180,referenceFrame:0,fps:30,sourceTimes:Array.from({length:45},(_,i)=>i/30),depth:{width:2,height:2,values:[5,5,5,5],kind:'distance',scale:1,bias:0},camera:{focalPixels:280,focalY:280,homographies:Array.from({length:45},()=>K.IDENTITY3)},mattes:[]};
  K.setVolumeFXScene(scene);const o={w:320,h:180,n:45,x:160,y:100,start:0,end:44,size:.6,seed:42,quality:'draft',resolution:24};
  const result=K.renderVolumeElement('smoke','smoke_white',o,10),alpha=result.alpha.getContext('2d').getImageData(0,0,320,180).data;
  const first=result.element.toDataURL(),pixels=result.element.getContext('2d').getImageData(0,0,320,180).data,field=K.volumeFXDiagnostics('smoke');K.renderVolumeElement('smoke','smoke_white',o,4);const again=K.renderVolumeElement('smoke','smoke_white',o,10).element.toDataURL(),pixels2=result.element.getContext('2d').getImageData(0,0,320,180).data,field2=K.volumeFXDiagnostics('smoke');
  let pixelDelta=0,fieldDelta=0;for(let i=0;i<pixels.length;i++)pixelDelta=Math.max(pixelDelta,Math.abs(pixels[i]-pixels2[i]));for(let i=0;i<field.dye.length;i++)fieldDelta=Math.max(fieldDelta,Math.abs(field.dye[i]-field2.dye[i]));
  const sum=alpha.reduce((s,v,i)=>s+(i%4===0?v:0),0),error=K.gl.getError();K.clearVolumeFX();
  K.setVolumeFXScene(scene);const kinds={};for(const kind of ['explosion','wall_hit','dirt_hit','fire']){const r=K.renderVolumeElement(kind,kind,{...o,resolution:kind==='fire'?64:16},10);const pixels=r.alpha.getContext('2d').getImageData(0,0,320,180).data;kinds[kind]=pixels.reduce((a,v,i)=>a+(i%4===0?v:0),0);}K.clearVolumeFX();
  K.setVolumeFXScene(scene);const fireOptions={...o,quality:'high',resolution:undefined,start:12,onsetSeconds:.4,durationSeconds:2};
  const fireAlpha=f=>{const r=K.renderVolumeElement('continuous-fire','fire',fireOptions,f);return {sum:r.alpha.getContext('2d').getImageData(0,0,320,180).data.reduce((s,v,i)=>s+(i%4===0?v:0),0),metadata:r.metadata};};
  const fireBefore=fireAlpha(5),fireEarly=fireAlpha(24),fireLater=fireAlpha(42);K.clearVolumeFX();
  const wall=new Uint8Array(320*180).fill(255);K.setVolumeFXScene({...scene,key:'empty-holdout',mattes:[{id:'wall',width:320,height:180,frames:[wall],static:true,coordinates:'reference',role:'holdout',kind:'hard',thickness:.3}]});
  const empty=K.renderVolumeElement('empty','smoke_white',{...o,onsetSeconds:1,matteIds:['wall']},0).composite.getContext('2d').getImageData(0,0,320,180).data;let emptyAlpha=0;for(let i=3;i<empty.length;i+=4)emptyAlpha=Math.max(emptyAlpha,empty[i]);K.clearVolumeFX();
  K.setVolumeFXScene({...scene,key:'surface-wall',mattes:[{id:'wall',width:320,height:180,frames:[wall],static:true,coordinates:'reference',role:'both',kind:'hard',thickness:.3}]});
  const surface=K.renderVolumeElement('surface','explosion',{...o,depthOffset:0,matteIds:['wall']},10),surfacePixels=surface.alpha.getContext('2d').getImageData(0,0,320,180).data,surfaceAlpha=surfacePixels.reduce((a,v,i)=>a+(i%4===0?v:0),0),surfaceField=K.volumeFXDiagnostics('surface');K.clearVolumeFX();
  return {alphaSum:sum,metadata:result.metadata,scrubSerializationExact:first===again,pixelDelta,fieldDelta,frames:[field.frame,field2.frame],kinds,fireBefore,fireEarly,fireLater,emptyHoldoutAlpha:emptyAlpha,surfaceAlpha,surfaceDepth:surface.metadata.sourceDepth,surfaceCore:surfaceField.coreDensity,error};
 });
 console.log(JSON.stringify({rendering}));assert(rendering.alphaSum>0);assert.equal(rendering.metadata.simulationFrame,8);assert.equal(rendering.pixelDelta,0);assert.equal(rendering.fieldDelta,0);assert.equal(rendering.error,0);assert.equal(rendering.emptyHoldoutAlpha,0);for(const mass of Object.values(rendering.kinds))assert(mass>0);assert(rendering.surfaceAlpha>0);assert(rendering.surfaceDepth<5);assert.equal(rendering.surfaceCore,0);
 console.log('Renderer: isolated RGB/alpha, native30fps->24Hz timestamp mapping and deterministic scrub pass.');
 assert.equal(rendering.fireBefore.sum,0);assert(rendering.fireEarly.sum>0);assert(rendering.fireLater.sum>0);assert.equal(rendering.fireLater.metadata.flowResolution,128);assert.equal(rendering.fireLater.metadata.profile,'squib-groundfire-v1');assert.equal(rendering.fireLater.metadata.hasFire,true);assert.equal(rendering.fireLater.metadata.bounds,'authored-groundfire');
 const transport=await page.evaluate(()=>{
  const K=window.K,{state,app,gl}=K;state.fps=24;Object.assign(state.scene,{buoyancy:0,drag:.05,wind:0});
  state.cam={mode:'3d',res:32,groundY:-100,adaptive:false,multigrid:false,mc:true,rk2:true,projectAfter:true,substeps:4,detail:0,tune:{jacobi:32,curl:0,turb:0}};
  const results={};
  for(const finite of [false,true]){
   const source=[1.5,1.8,1.35],inst={...K.PRESETS.explosion.defaults,id:19,preset:'explosion',enabled:true,intensity:1,heat:1,gravityMul:1,speedMul:1,fineGrain:1,full:true,area:.8,sizeMul:1,depth:.7,spreadMul:1,lift:0,facing:0,haze:.35,scale:1,seed:42,frame:0,wscale:3,w:source,wdir:[0,-1,0],debrisMul:0,sparksMul:0,flash:false,volumeFXBox:{min:[0,0,0],size:[3,3,3]}};
   state.instances=[inst];const sim=K.createVolumeSimulator();app.sim3d=sim;sim.sync();const v=sim.vols.get(19),bytes=new Uint8Array(v.aw*v.ah),zero=new Float32Array(v.aw*v.ah*4),hold=new Uint8Array(bytes.length);
   const at=(x,y,z)=>(Math.floor(z/v.tiles[0])*v.dim[1]+y)*v.aw+(z%v.tiles[0])*v.dim[0]+x;
   for(let z=0;z<v.dim[2];z++)for(let y=0;y<v.dim[1];y++)for(let x=0;x<v.dim[0];x++){const yy=(y+.5)*v.vox,zz=(z+.5)*v.vox;if(yy>=1.4&&yy<=1.55&&(!finite||zz<=1.5))bytes[at(x,y,z)]=255;}
   const textures=[];for(const [data,ifmt,fmt,type]of [[bytes,gl.R8,gl.RED,gl.UNSIGNED_BYTE],[hold,gl.R8,gl.RED,gl.UNSIGNED_BYTE],[zero,gl.RGBA32F,gl.RGBA,gl.FLOAT]]){const t=K.makeTex(v.aw,v.ah,ifmt,fmt,type,gl.NEAREST);gl.bindTexture(gl.TEXTURE_2D,t);gl.texSubImage2D(gl.TEXTURE_2D,0,0,0,v.aw,v.ah,fmt,type,data);textures.push(t);}
   const u=v.u.bind(v);v.u=(coarse=false)=>({...u(coarse),uVFXSolid:textures[0],uVFXHoldout:textures[1],uVFXMotion:textures[2],uVFXMaskDim:v.dim,uVFXMaskTiles:v.tiles,uVFXMaskBoxMin:v.boxMin,uVFXMaskVox:v.vox,uVFXEnabled:1,uVFXRay:0,uVFXSource:source.map(q=>q/v.vox)});
   const fluid=v.fluid.bind(v);v.fluid=(dt,list,time,sub)=>fluid(dt,time>=0&&time<.125?[{p:source,r:.14,v:[0,-3,3],temp:0,vw:.95,col:[.2,.2,.2],d:1.2,heat:2,curl:0,turb:0,rate:.05,linger:5}]:[],time,sub);
   sim.goto(24);const read=()=>{const a=new Float32Array(v.aw*v.ah*4);gl.bindFramebuffer(gl.FRAMEBUFFER,v.dye.read.fbo);gl.readPixels(0,0,v.aw,v.ah,gl.RGBA,gl.FLOAT,a);return a;};const dye=read();let core=0,below=0,total=0;
   for(let z=0;z<v.dim[2];z++)for(let y=0;y<v.dim[1];y++)for(let x=0;x<v.dim[0];x++){const k=at(x,y,z),rho=dye[k*4+3];if(!Number.isFinite(rho))throw Error('Nonfinite density');if(bytes[k])core=Math.max(core,rho);if((y+.5)*v.vox<1.3)below+=rho;total+=rho;}
   sim.goto(12);sim.goto(24);const again=read();let scrubMax=0;for(let i=0;i<dye.length;i++)scrubMax=Math.max(scrubMax,Math.abs(dye[i]-again[i]));
   results[finite?'finite':'sealed']={core,below,total,scrubMax};sim.destroy();textures.forEach(t=>gl.deleteTexture(t));
  }return results;
 });
 assert.equal(transport.finite.core,0);assert.equal(transport.sealed.core,0);assert(transport.finite.below>.001);assert(transport.sealed.below<1e-6);assert(transport.finite.scrubMax<1e-5);assert.deepEqual(errors,[]);
 console.log('GPU transport: finite shelf wraps into its lee, sealed depth extrusion blocks; solid density zero; scrub deterministic.');
 const report={pass:true,geometry,rendering,transport};writeFileSync(process.env.KEYFX_VOLUME_REPORT||'/tmp/bang-volume-test.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser.close();}
