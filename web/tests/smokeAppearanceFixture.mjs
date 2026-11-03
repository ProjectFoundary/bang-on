// Build a standalone GPU regression page. Open it through the supported CUA
// browser and press Run; this script performs no browser automation itself.
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {mkdirSync,writeFileSync} from 'node:fs';
const local=createRequire(new URL('../package.json',import.meta.url)),{build}=local('esbuild');
const output=process.env.KEYFX_APPEARANCE_FIXTURE||'/tmp/bang-smoke-appearance-fixture';mkdirSync(output,{recursive:true});
const bundle=await build({stdin:{resolveDir:fileURLToPath(new URL('../src/',import.meta.url)),loader:'ts',contents:`
import * as FX from './volumeFX';import {IDENTITY3} from './volumeGeometry';import {volumeOptions} from './elementOptions';import {gl} from './squib/gl/context.js';
const status=document.querySelector('pre'),button=document.querySelector('button');
const assert=(value,message)=>{if(!value)throw Error(message);};
const same=(a,b)=>a.length===b.length&&a.every((v,i)=>v===b[i]);
const pixels=c=>new Uint8ClampedArray(c.getContext('2d').getImageData(0,0,c.width,c.height).data);
const sum=(values,channel=0)=>values.reduce((total,v,i)=>total+(i%4===channel?v:0),0);
button.onclick=async()=>{button.disabled=true;status.textContent='Running actual GPU smoke renders…';
 try{
  const w=192,h=108,n=49,times=Array.from({length:n},(_,i)=>i/24),mask=new Uint8Array(w*h);
  for(let y=0;y<h;y++)for(let x=130;x<w;x++)mask[y*w+x]=255;
  const scene={key:'smoke-appearance',width:w,height:h,referenceFrame:0,fps:24,sourceTimes:times,collisionMode:'matte2d',
   depth:{width:1,height:1,values:[5],kind:'distance',scale:1,bias:0},camera:{focalPixels:170,focalY:170,homographies:times.map(()=>IDENTITY3)},
   mattes:[{id:'shelf',width:w,height:h,frames:[mask],static:true,kind:'hard',role:'holdout',thickness:.1}]};
  const project={frames:n,native_profile:{fps:24,timestamps:times},settings:{seed:42}},rows=[];
  FX.setVolumeFXScene(scene);
  for(const kind of ['smoke_grenade','smoke_white','smoke_plume','smoke_plume_hq']){
   const effect={id:kind,preset:kind,keys:[{phase:'start',frame:0}],anchors:[{frame:0,x:.48,y:.78}],tracking:'camera',size:.6,element:{duration_s:2,colliders:['shelf']}};
   const options={...volumeOptions(project,effect,36,new Map(),w,h),resolution:32,quality:'draft'},owner='appearance-test';
   const render=look=>FX.renderVolumeElement(kind,kind,{...options,...look},36,owner);
   const baseline=render({}),baseRGB=pixels(baseline.rgb),baseAlpha=pixels(baseline.alpha),baseVisibility=pixels(baseline.visibility),physical=FX.volumeFXDiagnostics(kind,owner);
   const normal=render({density:1,brightness:1});assert(same(baseRGB,pixels(normal.rgb))&&same(baseAlpha,pixels(normal.alpha)),kind+' neutral look changed defaults');
   const thin=render({density:.25,brightness:1}),thinAlpha=sum(pixels(thin.alpha));
   const dense=render({density:3,brightness:1}),denseAlpha=sum(pixels(dense.alpha));
   assert(thinAlpha<sum(baseAlpha)&&denseAlpha>sum(baseAlpha),kind+' density did not change optical thickness');
   const dim=render({density:1,brightness:.25}),dimRGB=sum(pixels(dim.rgb));assert(same(baseAlpha,pixels(dim.alpha)),kind+' brightness changed alpha');
   const bright=render({density:1,brightness:2}),brightRGB=sum(pixels(bright.rgb));
   assert(dimRGB<sum(baseRGB)&&brightRGB>sum(baseRGB),kind+' brightness did not change lighting');
   assert(same(baseAlpha,pixels(bright.alpha))&&same(baseVisibility,pixels(bright.visibility)),kind+' brightness changed holdout/alpha');
   const keyOff=render({lightIntensity:0}),keyOffRGB=sum(pixels(keyOff.rgb));
   assert(same(baseAlpha,pixels(keyOff.alpha)),kind+' light intensity changed alpha');
   const keyStrong=render({lightIntensity:4}),keyStrongRGB=sum(pixels(keyStrong.rgb));
   assert(keyOffRGB<sum(baseRGB)&&keyStrongRGB>sum(baseRGB),kind+' light intensity did not change directional lighting');
   assert(same(baseAlpha,pixels(keyStrong.alpha))&&same(baseVisibility,pixels(keyStrong.visibility)),kind+' light intensity changed holdout/alpha');
   const keyStrongPixels=pixels(keyStrong.rgb),rotated=render({lightIntensity:4,lightRotation:90});
   assert(!same(keyStrongPixels,pixels(rotated.rgb)),kind+' light rotation did not change directional lighting');
   assert(same(baseAlpha,pixels(rotated.alpha)),kind+' light rotation changed alpha');
   const zero=render({density:0,brightness:1});assert(sum(pixels(zero.alpha))===0,kind+' zero density was not transparent');
   const restored=render({density:1,brightness:1}),restoredRGB=pixels(restored.rgb),after=FX.volumeFXDiagnostics(kind,owner);
   const maxDiff=(a,b)=>a.reduce((v,x,i)=>Math.max(v,Math.abs(x-b[i])),0);
   // Canvas/WebGL premultiplication roundtrips can differ by one RGB8 code.
   assert(maxDiff(baseRGB,restoredRGB)<=1&&same(baseAlpha,pixels(restored.alpha)),kind+' restoring controls exceeds RGB8 rounding');
   assert(same(physical.dye,after.dye)&&same(physical.velocity,after.velocity),kind+' appearance changed fluid state');
   if(physical.particles)assert(same(physical.particles.positions,after.particles.positions)&&same(physical.particles.mass,after.particles.mass),kind+' appearance changed particles');
   FX.renderVolumeElement(kind,kind,{...options,density:1,brightness:1},12,owner);
   const replay=render({density:1,brightness:1}),replayRGB=pixels(replay.rgb),replayPhysical=FX.volumeFXDiagnostics(kind,owner);
   const maxDifference=(a,b)=>a.reduce((v,x,i)=>Math.max(v,Math.abs(x-b[i])),0);
   assert(maxDifference(baseRGB,replayRGB)<=1&&same(physical.dye,replayPhysical.dye)&&same(physical.velocity,replayPhysical.velocity),kind+' scrub differs '+JSON.stringify({rgb:maxDifference(baseRGB,replayRGB),dye:maxDifference(physical.dye,replayPhysical.dye),velocity:maxDifference(physical.velocity,replayPhysical.velocity)}));
   const captured=FX.renderVolumeElement(kind,kind,{...options,density:1,brightness:2},36,'capture');
   assert(maxDifference(pixels(captured.rgb),pixels(render({density:1,brightness:2}).rgb))<=1,kind+' guide and preview RGB disagree');
   assert(captured.metadata.smokeBrightness===2&&captured.metadata.smokeDensity===1,kind+' guide metadata is missing');
   const lightLook={lightIntensity:4,lightRotation:90,lightElevation:25};
   const litCapture=FX.renderVolumeElement(kind,kind,{...options,...lightLook},36,'capture');
   assert(maxDifference(pixels(litCapture.rgb),pixels(render(lightLook).rgb))<=1,kind+' lighting guide and preview disagree');
   assert(litCapture.metadata.lightIntensity===4&&litCapture.metadata.lightElevation===25,kind+' lighting guide metadata is missing');
   const litPhysical=FX.volumeFXDiagnostics(kind,owner);
   assert(same(physical.dye,litPhysical.dye)&&same(physical.velocity,litPhysical.velocity),kind+' lighting changed fluid state');
   rows.push({kind,thinAlpha,normalAlpha:sum(baseAlpha),denseAlpha,dimRGB,normalRGB:sum(baseRGB),brightRGB,keyOffRGB,keyStrongRGB,rotationChangesLighting:true,physicsUnchanged:true,particles:physical.particles?.count??0,exactScrub:true,guideMatchesPreview:true});
   FX.pruneVolumeFX(new Set(),owner);FX.pruneVolumeFX(new Set(),'capture');
  }
  assert(gl.getError()===0,'WebGL error');FX.clearVolumeFX();status.textContent=JSON.stringify({pass:true,rows},null,2);
 }catch(error){status.textContent=JSON.stringify({pass:false,error:String(error)},null,2);FX.clearVolumeFX();}finally{button.disabled=false;}
};
`},bundle:true,write:false,format:'iife'});
writeFileSync(output+'/fixture.js',bundle.outputFiles[0].text);
writeFileSync(output+'/index.html','<!doctype html><meta charset="utf-8"><title>Smoke appearance GPU regression</title><style>body{font:14px system-ui;background:#211e2a;color:#eee;padding:24px}button{padding:12px}pre{white-space:pre-wrap}</style><h1>Smoke appearance GPU regression</h1><button>Run smoke controls</button><pre>Ready. No source footage, network model calls or generation.</pre><script src="fixture.js"></script>');
console.log(output);
