// Synthetic WebGL2 acceptance: spreading must change centred smoke variance,
// not merely its centroid. No photography, application or provider requests.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {mkdirSync,writeFileSync} from 'node:fs';
const local=createRequire(new URL('../package.json',import.meta.url));
const runtime=createRequire(process.env.KEYFX_NODE_PACKAGE||new URL('../package.json',import.meta.url));
const {build}=local('esbuild'),{chromium}=runtime('playwright');
const output=process.env.KEYFX_DISPERSION_OUTPUT||'/tmp/bang-wind-dispersion';mkdirSync(output,{recursive:true});
const bundle=await build({stdin:{contents:`
 import * as fx from './volumeFX';import * as geometry from './volumeGeometry';
 import {state,app} from './squib/state.js';import {PRESETS} from './squib/presets.js';
 import {gl,makeTex} from './squib/gl/context.js';window.K={...fx,...geometry,state,app,PRESETS,gl,makeTex};
`,resolveDir:fileURLToPath(new URL('../src/',import.meta.url)),loader:'ts'},bundle:true,write:false,format:'iife'});
const browser=await chromium.launch({executablePath:process.env.KEYFX_CHROME||undefined,headless:true,args:process.platform==='darwin'?['--use-angle=metal']:[]});
try{
 const page=await browser.newPage();page.setDefaultTimeout(240000);const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await page.route('**/*',r=>r.fulfill({contentType:'text/html',body:'<!doctype html><body></body>'}));
 await page.goto('http://synthetic-dispersion.local');await page.addScriptTag({content:bundle.outputFiles[0].text});
 const modes=await page.evaluate(()=>{
  const K=window.K,w=240,h=135,mask=new Uint8Array(w*h);for(let y=0;y<h;y++)for(let x=120;x<130;x++)mask[y*w+x]=255;
  const base={key:'modes',width:w,height:h,referenceFrame:0,fps:24,depth:{width:1,height:1,values:[5],kind:'distance',scale:1,bias:0},camera:{focalPixels:210,focalY:210,homographies:[K.IDENTITY3]},
   mattes:[{id:'bar',width:w,height:h,frames:[mask],static:true,coordinates:'reference',role:'both',kind:'hard',thickness:.25}]};
  const grid={dim:[32,32,32],tiles:[6,6],boxMin:[-1.5,-1.5,-6.5],vox:3/32,aw:192,ah:192},geometry={};
  for(const mode of ['matte2d','depth3d']){
   const s={...base,collisionMode:mode},f=K.voxelizeScene(s,grid,0),occupied=[];
   for(let z=0;z<32;z++){let count=0;for(let y=0;y<32;y++)for(let x=0;x<32;x++)count+=f.solid[K.gridIndex(grid,x,y,z)]>0;occupied.push(count);}
   geometry[mode]={occupied,front:K.fallbackVisibility(s,0,125,70,2),behind:K.fallbackVisibility(s,0,125,70,8),sourceDepth:K.emitterDepth(s,0,100,70,.2,2)};
  }
  const {state,app,gl}=K;state.fps=24;Object.assign(state.scene,{buoyancy:0,drag:0,wind:0});
  state.cam={mode:'3d',res:32,groundY:-100,adaptive:false,multigrid:false,mc:true,rk2:true,projectAfter:true,substeps:4,detail:0,tune:{jacobi:32,curl:0,turb:0}};
  const transport={};
  for(const mode of ['matte2d','depth3d']){
   const source=[-.65,0,-5.1],inst={...K.PRESETS.explosion.defaults,id:19,preset:'explosion',enabled:true,intensity:1,heat:1,gravityMul:1,speedMul:1,fineGrain:1,full:true,area:.8,sizeMul:1,depth:.7,spreadMul:1,lift:0,facing:0,haze:.35,scale:1,seed:42,frame:0,wscale:3,w:source,wdir:[1,0,0],debrisMul:0,sparksMul:0,flash:false,volumeFXBox:{min:grid.boxMin,size:[3,3,3]}};
   state.instances=[inst];const sim=K.createVolumeSimulator();app.sim3d=sim;sim.sync();const v=sim.vols.get(19),g={dim:v.dim,tiles:v.tiles,boxMin:v.boxMin,vox:v.vox,aw:v.aw,ah:v.ah};
   const f=K.voxelizeScene({...base,collisionMode:mode},g,0),textures=[];
   for(const [data,ifmt,fmt,type]of [[f.solid,gl.R8,gl.RED,gl.UNSIGNED_BYTE],[f.holdout,gl.R8,gl.RED,gl.UNSIGNED_BYTE],[f.motion,gl.RGBA32F,gl.RGBA,gl.FLOAT]]){
    const texture=K.makeTex(v.aw,v.ah,ifmt,fmt,type,gl.NEAREST);gl.bindTexture(gl.TEXTURE_2D,texture);gl.texSubImage2D(gl.TEXTURE_2D,0,0,0,v.aw,v.ah,fmt,type,data);textures.push(texture);
   }
   const u=v.u.bind(v);v.u=(coarse=false)=>({...u(coarse),uVFXSolid:textures[0],uVFXHoldout:textures[1],uVFXMotion:textures[2],uVFXMaskDim:v.dim,uVFXMaskTiles:v.tiles,uVFXMaskBoxMin:v.boxMin,uVFXMaskVox:v.vox,
    uVFXEnabled:1,uVFXRay:0,uVFXSource:source.map((q,a)=>(q-v.boxMin[a])/v.vox),uVFXWind:[2.5,0,2.5],uVFXWindK:2,uVFXWindDispersion:0});
   const fluid=v.fluid.bind(v);v.fluid=(dt,list,time,sub)=>fluid(dt,time>=0&&time<.125?[{p:source,r:.14,v:[2.5,0,2.5],temp:0,vw:.95,col:[.2,.2,.2],d:1.2,heat:0,curl:0,turb:0,rate:0,linger:5}]:[],time,sub);
   sim.goto(24);const dye=new Float32Array(v.aw*v.ah*4);gl.bindFramebuffer(gl.FRAMEBUFFER,v.dye.read.fbo);gl.readPixels(0,0,v.aw,v.ah,gl.RGBA,gl.FLOAT,dye);let core=0,right=0,total=0;
   for(let z=0;z<v.dim[2];z++)for(let y=0;y<v.dim[1];y++)for(let x=0;x<v.dim[0];x++){const k=K.gridIndex(g,x,y,z),rho=dye[k*4+3];if(!Number.isFinite(rho))throw Error('Nonfinite density');if(f.solid[k])core=Math.max(core,rho);if(v.boxMin[0]+(x+.5)*v.vox>.4)right+=rho;total+=rho;}
   transport[mode]={core,right,total};sim.destroy();textures.forEach(t=>gl.deleteTexture(t));
  }
  const opticalScene={...base,key:'optical2d',collisionMode:'matte2d',mattes:[{...base.mattes[0],id:'wash',role:'holdout',frames:[new Uint8Array(w*h).fill(128)]}]};
  K.setVolumeFXScene(opticalScene);const result=K.renderVolumeElement('optical2d','smoke_grenade',{w,h,n:25,x:100,y:80,start:0,end:24,durationSeconds:1,size:.5,seed:42,resolution:24,quality:'draft',matteIds:['wash']},12);
  const raw=result.element.getContext('2d').getImageData(0,0,w,h).data,held=result.composite.getContext('2d').getImageData(0,0,w,h).data,visibility=result.visibility.getContext('2d').getImageData(0,0,w,h).data;
  let opticalDelta=0,visibilityDelta=0,alpha=0;for(let i=0;i<w*h;i++){opticalDelta=Math.max(opticalDelta,Math.abs(held[i*4+3]-Math.round(raw[i*4+3]*127/255)));visibilityDelta=Math.max(visibilityDelta,Math.abs(visibility[i*4]-127));alpha+=raw[i*4+3];}
  const optical={opticalDelta,visibilityDelta,alpha};K.clearVolumeFX();return {geometry,transport,optical,glError:gl.getError()};
 });
 console.log(JSON.stringify({modes}));
 assert(modes.geometry.matte2d.occupied.every(count=>count===modes.geometry.matte2d.occupied[0]&&count>0));
 assert(modes.geometry.depth3d.occupied.filter(count=>count>0).length<8);
 assert.equal(modes.geometry.matte2d.front,0);assert.equal(modes.geometry.matte2d.behind,0);assert.equal(modes.geometry.matte2d.sourceDepth,5);assert.equal(modes.geometry.depth3d.front,1);
 assert.equal(modes.transport.matte2d.core,0);assert.equal(modes.transport.depth3d.core,0);assert(modes.transport.depth3d.right>.001);assert(modes.transport.matte2d.right<1e-6);assert.equal(modes.glError,0);
 assert(modes.optical.alpha>0);assert.equal(modes.optical.opticalDelta,0);assert.equal(modes.optical.visibilityDelta,0);
 console.log('2D/3D collision: straight infiniteZ barrier blocks; finite depth lets gas wrap; raw2D holdout applies at all depths.');
 const dispersion=await page.evaluate(()=>{
  const K=window.K,w=320,h=180,n=73,times=Array.from({length:n},(_,i)=>i/24),scene={key:'dispersion',width:w,height:h,referenceFrame:0,fps:24,sourceTimes:times,collisionMode:'depth3d',
   depth:{width:1,height:1,values:[5],kind:'distance',scale:1,bias:0},camera:{focalPixels:280,focalY:280,homographies:times.map(()=>K.IDENTITY3)},mattes:[]};
  const options={w,h,n,x:160,y:130,start:0,end:72,durationSeconds:3,size:.7,seed:42,quality:'high',resolution:64,windVector:[.6,0,0],windResponse:2};
  const delta=(a,b)=>{let max=0;if(a.length!==b.length)throw Error('Comparison grids differ');for(let i=0;i<a.length;i++)max=Math.max(max,Math.abs(a[i]-b[i]));return max;};
  const moments=d=>{
   const g=d.grid,first=[0,0,0],second=[0,0,0];let mass=0,square=0,peak=0,heat=0;
   for(let z=0;z<g.dim[2];z++)for(let y=0;y<g.dim[1];y++)for(let x=0;x<g.dim[0];x++){
    const k=K.gridIndex(g,x,y,z),rho=d.dye[k*4+3];if(!Number.isFinite(rho))throw Error('Nonfinite density');mass+=rho;square+=rho*rho;peak=Math.max(peak,rho);if(d.heat)heat+=d.heat[k*4];
    for(let a=0;a<3;a++){const q=g.boxMin[a]+([x,y,z][a]+.5)*g.vox;first[a]+=rho*q;second[a]+=rho*q*q;}
   }
   const mean=first.map(q=>q/mass),variance=second.map((q,a)=>q/mass-mean[a]**2);
   return {mass:mass*g.vox**3,mean,variance,crosswindVariance:variance[1]+variance[2],effectiveVolume:mass*mass/(square+1e-12)*g.vox**3,peak,heat:heat*g.vox**3,grid:g.dim};
  };
  K.setVolumeFXScene(scene);const rows={},earlyFields={};
  for(const value of [0,1,2]){
   const id=`wind-${value}`,o={...options,windDispersion:value};K.renderVolumeElement(id,'explosion',o,8);const early=K.volumeFXDiagnostics(id,'preview',true);earlyFields[value]=early;
   K.renderVolumeElement(id,'explosion',o,24);const grace=K.volumeFXDiagnostics(id,'preview',true);let minGrace=0;
   for(let i=0;i<grace.dye.length;i+=4)if(grace.dye[i+3]>.01)minGrace=Math.min(minGrace,grace.attributes[i+3]/grace.dye[i+3]);
   const middle=K.renderVolumeElement(id,'explosion',o,36),png=middle.rgb.toDataURL();const lateResult=K.renderVolumeElement(id,'explosion',o,60),latePNG=lateResult.rgb.toDataURL();const late=K.volumeFXDiagnostics(id,'preview',true);
   K.renderVolumeElement(id,'explosion',o,24);K.renderVolumeElement(id,'explosion',o,60);const scrub=K.volumeFXDiagnostics(id,'preview',true);
   rows[value]={early:moments(early),late:moments(late),minGrace,png,latePNG,scrubDye:delta(late.dye,scrub.dye),scrubHeat:delta(late.heat,scrub.heat),debris:late.debris,sparks:late.sparks};
  }
  const earlyDyeDelta=delta(earlyFields[0].dye,earlyFields[2].dye),earlyHeatDelta=delta(earlyFields[0].heat,earlyFields[2].heat);
  const calm=[];for(const value of [0,2]){const id=`calm-${value}`;K.renderVolumeElement(id,'explosion',{...options,windVector:[0,0,0],windDispersion:value},60);calm.push(K.volumeFXDiagnostics(id,'preview',true));}
  const noWindDyeDelta=delta(calm[0].dye,calm[1].dye),noWindHeatDelta=delta(calm[0].heat,calm[1].heat),glError=K.gl.getError();K.clearVolumeFX();
  return {rows,earlyDyeDelta,earlyHeatDelta,noWindDyeDelta,noWindHeatDelta,glError};
 });
 console.log(JSON.stringify({modes,dispersion:{...dispersion,rows:Object.fromEntries(Object.entries(dispersion.rows).map(([key,row])=>[key,{...row,png:undefined,latePNG:undefined}]))}}));
 assert.equal(dispersion.earlyDyeDelta,0);assert.equal(dispersion.earlyHeatDelta,0);assert.equal(dispersion.noWindDyeDelta,0);assert.equal(dispersion.noWindHeatDelta,0);
 assert(dispersion.rows[1].late.crosswindVariance>dispersion.rows[0].late.crosswindVariance*1.1);assert(dispersion.rows[2].late.crosswindVariance>dispersion.rows[1].late.crosswindVariance*1.05);
 assert(dispersion.rows[1].late.effectiveVolume>dispersion.rows[0].late.effectiveVolume*1.1);assert(dispersion.rows[1].late.peak<dispersion.rows[0].late.peak);
 for(const [name,row]of Object.entries(dispersion.rows)){assert(row.early.mass>0);assert(row.late.mass>0);assert(row.minGrace<-.1);assert.equal(row.scrubDye,0);assert.equal(row.scrubHeat,0);assert.equal(row.debris,0);assert.equal(row.sparks,0);writeFileSync(`${output}/dispersion-${name}-frame36.png`,Buffer.from(row.png.split(',')[1],'base64'));writeFileSync(`${output}/dispersion-${name}-frame60.png`,Buffer.from(row.latePNG.split(',')[1],'base64'));delete row.png;delete row.latePNG;}
 assert.equal(dispersion.glError,0);assert.deepEqual(errors,[]);const report={pass:true,modes,dispersion};writeFileSync(`${output}/report.json`,JSON.stringify(report,null,2));
 console.log('Wind dispersion: centred crosswind variance and occupied volume increase; early fire heat and no-wind fields remain bitwise identical; scrub is exact.');
}finally{await browser.close();}
