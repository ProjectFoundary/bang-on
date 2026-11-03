// Build only; run the page through CUA. No browser automation/model requests.
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {mkdirSync,writeFileSync,existsSync} from 'node:fs';
const local=createRequire(new URL('../package.json',import.meta.url)),{build}=local('esbuild');
const output=process.env.KEYFX_SMOKE_DISPERSION_FIXTURE||'/tmp/bang-smoke-dispersion-fixture';mkdirSync(output,{recursive:true});
const bundle=await build({stdin:{resolveDir:fileURLToPath(new URL('../src/',import.meta.url)),loader:'ts',contents:`
import * as FX from './volumeFX';import {IDENTITY3,gridIndex} from './volumeGeometry';import {volumeOptions} from './elementOptions';import {gl} from './squib/gl/context.js';
const button=document.querySelector('button'),status=document.querySelector('pre');
const assert=(v,m)=>{if(!v)throw Error(m);},delta=(a,b)=>{if(a.length!==b.length)return Infinity;let d=0;for(let i=0;i<a.length;i++)d=Math.max(d,Math.abs(a[i]-b[i]));return d;};
function moments(d){const g=d.grid,first=[0,0,0],second=[0,0,0];let mass=0,square=0,peak=0;
 for(let z=0;z<g.dim[2];z++)for(let y=0;y<g.dim[1];y++)for(let x=0;x<g.dim[0];x++){
  const rho=d.dye[gridIndex(g,x,y,z)*4+3];assert(Number.isFinite(rho)&&rho>=0,'Invalid smoke density');mass+=rho;square+=rho*rho;peak=Math.max(peak,rho);
  for(let a=0;a<3;a++){const q=g.boxMin[a]+([x,y,z][a]+.5)*g.vox;first[a]+=rho*q;second[a]+=rho*q*q;}
 }
 assert(mass>0,'Empty smoke');const mean=first.map(q=>q/mass),variance=second.map((q,a)=>q/mass-mean[a]**2);
 return {mass:mass*g.vox**3,mean,variance,crosswindVariance:variance[1]+variance[2],effectiveVolume:mass*mass/(square+1e-12)*g.vox**3,peak,grid:g.dim};
}
button.onclick=async()=>{button.disabled=true;status.textContent='Running GPU smoke dispersion checks…';
 try{
  const w=240,h=135,n=73,times=Array.from({length:n},(_,i)=>i/24),scene={key:'smoke-dispersion',width:w,height:h,referenceFrame:0,fps:24,sourceTimes:times,collisionMode:'matte2d',
   depth:{width:1,height:1,values:[5],kind:'distance',scale:1,bias:0},camera:{focalPixels:210,focalY:210,homographies:times.map(()=>IDENTITY3)},mattes:[]};
  const project={frames:n,native_profile:{fps:24,timestamps:times},settings:{seed:42}},effect={id:'smoke',preset:'smoke_grenade',keys:[{phase:'start',frame:0}],anchors:[{frame:0,x:.45,y:.7}],size:.7,strength:.5,angle:90,element:{duration_s:3,wind_dispersion:1}},mapped=volumeOptions(project,effect,60,new Map(),w,h);
  assert(mapped.windDispersion===0,'Old ignored smoke setting must remain off');effect.element.smoke_dispersion=1.5;
  assert(volumeOptions(project,effect,60,new Map(),w,h).windDispersion===1.5,'Explicit smoke dispersion mapping lost');
  const options={...mapped,resolution:32,quality:'draft',windVector:[.35,0,0],windResponse:2},rows={},early={};FX.setVolumeFXScene(scene);
  for(const value of [0,.1,1,2]){
   const id='smoke-'+value,o={...options,windDispersion:value};FX.renderVolumeElement(id,'smoke_grenade',o,8);early[value]=FX.volumeFXDiagnostics(id);
   FX.renderVolumeElement(id,'smoke_grenade',o,60);const physical=FX.volumeFXDiagnostics(id),late=moments(physical);
   FX.renderVolumeElement(id,'smoke_grenade',o,12);FX.renderVolumeElement(id,'smoke_grenade',o,60);const replay=FX.volumeFXDiagnostics(id);
   assert(delta(physical.dye,replay.dye)===0&&delta(physical.velocity,replay.velocity)===0,'Smoke scrub changed physics');
   assert(delta(physical.windWorld,early[0].windWorld)===0,'Dispersion changed wind direction/strength');rows[value]={...late,windWorld:physical.windWorld,dispersion:physical.windDispersion,exactPhysicsReplay:true};
   FX.pruneVolumeFX(new Set());
  }
  assert(delta(early[.1].dye,early[2].dye)===0,'Dispersion changed early pre-mixing puff on equal grids');
  assert(JSON.stringify(rows[.1].grid)===JSON.stringify(rows[2].grid),'Nonzero dispersion comparison grids differ');
  assert(rows[2].crosswindVariance>rows[.1].crosswindVariance*1.03,'Dispersion did not broaden centred crosswind density');
  assert(rows[2].effectiveVolume>rows[.1].effectiveVolume*1.03,'Dispersion did not increase occupied volume');
  assert(rows[2].peak<rows[.1].peak,'Dispersion did not thin smoke peak');
  const calm=[];for(const value of [0,2]){const id='calm-'+value;FX.renderVolumeElement(id,'smoke_grenade',{...options,windVector:[0,0,0],windDispersion:value},60);calm.push(FX.volumeFXDiagnostics(id));FX.pruneVolumeFX(new Set());}
  assert(delta(calm[0].dye,calm[1].dye)===0&&delta(calm[0].velocity,calm[1].velocity)===0,'Dispersion changed calm smoke');
  const calmMoments=moments(calm[0]),reverseId='reverse';FX.renderVolumeElement(reverseId,'smoke_grenade',{...options,windVector:[-.35,0,0],windDispersion:1},60);const reversed=moments(FX.volumeFXDiagnostics(reverseId));
  assert(rows[1].mean[0]>calmMoments.mean[0]+.1&&reversed.mean[0]<calmMoments.mean[0]-.1,'Directional wind transport failed');FX.pruneVolumeFX(new Set());
  let legacyDefaultExact=null;const before=window.BeforeSmokeDispersion;
  if(before){before.setVolumeFXScene(scene);FX.renderVolumeElement('default','smoke_grenade',options,60);before.renderVolumeElement('default','smoke_grenade',{...options,windDispersion:1},60);
   const old=before.volumeFXDiagnostics('default'),current=FX.volumeFXDiagnostics('default');legacyDefaultExact=delta(old.dye,current.dye)===0&&delta(old.velocity,current.velocity)===0;
   assert(legacyDefaultExact,'Pre-change smoke fields changed at the default');before.clearVolumeFX();FX.pruneVolumeFX(new Set());}
  const whiteEffect={...effect,preset:'smoke_white',element:{...effect.element,smoke_dispersion:1}};assert(volumeOptions(project,whiteEffect,60,new Map(),w,h).windDispersion===1,'Legacy white mapping lost');
  FX.renderVolumeElement('white','smoke_white',{...options,windDispersion:1},60);const white=moments(FX.volumeFXDiagnostics('white'));
  assert(Math.abs(white.mass-rows[1].mass)<1e-6,'Legacy white does not use the same grenade dispersion');
  assert(gl.getError()===0,'WebGL error');FX.clearVolumeFX();status.textContent=JSON.stringify({pass:true,rows,calm:calmMoments,reverse:reversed,legacyDefaultExact,legacyWhiteMatches:true},null,2);
 }catch(error){status.textContent=JSON.stringify({pass:false,error:String(error)},null,2);FX.clearVolumeFX();}finally{button.disabled=false;}
};
`},bundle:true,write:false,format:'iife'});
writeFileSync(output+'/fixture.js',bundle.outputFiles[0].text);
writeFileSync(output+'/index.html','<!doctype html><meta charset="utf-8"><title>Smoke wind dispersion GPU</title><style>body{font:14px system-ui;background:#211e2a;color:#eee;padding:24px}button{padding:12px}pre{white-space:pre-wrap}</style><h1>Smoke wind dispersion GPU</h1><button>Run smoke dispersion</button><pre>Ready. Synthetic GPU checks only.</pre>'+(existsSync(output+'/baseline.js')?'<script src="baseline.js"></script>':'')+'<script src="fixture.js"></script>');
console.log(output);
