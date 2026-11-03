// Actual HQ plume renders, kept local. Open through CUA and press Run.
// Download contains RGB inputs for the real Python paint/blend pipeline.
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {mkdirSync,writeFileSync} from 'node:fs';
const require=createRequire(new URL('../package.json',import.meta.url)),{build}=require('esbuild');
const output=process.env.KEYFX_RAKING_FIXTURE||'/tmp/bang-smoke-raking-fixture';mkdirSync(output,{recursive:true});
const bundle=await build({stdin:{resolveDir:fileURLToPath(new URL('../src/',import.meta.url)),loader:'ts',contents:`
import * as FX from './volumeFX';import {IDENTITY3} from './volumeGeometry';import {volumeOptions} from './elementOptions';import {gl} from './squib/gl/context.js';import {zipSync,strToU8} from 'fflate';
const status=document.querySelector('pre'),run=document.querySelector('#run'),download=document.querySelector('#download'),gallery=document.querySelector('#gallery');
const assert=(v,m)=>{if(!v)throw Error(m);},same=(a,b)=>a.length===b.length&&a.every((v,i)=>v===b[i]);
const pixels=c=>new Uint8ClampedArray(c.getContext('2d').getImageData(0,0,c.width,c.height).data);
const difference=(a,b)=>a.reduce((s,v,i)=>s+Math.abs(v-b[i]),0)/a.length;
const png=async c=>new Uint8Array(await (await fetch(c.toDataURL('image/png'))).arrayBuffer());
const query=new URLSearchParams(location.search),resolution=Number(query.get('resolution')||64),rimTest=query.has('rim');let archive;
run.onclick=async()=>{run.disabled=true;download.disabled=true;gallery.replaceChildren();status.textContent='Simulating the evolved HQ plume and testing light directions…';
 try{
  assert([64,128].includes(resolution),'Choose 64 or 128 grid resolution');
  const w=384,h=216,n=rimTest?48:24,count=rimTest?21:11,sample=rimTest?10:5,times=Array.from({length:n},(_,i)=>i/24),files={},rows=[];
  const scene={key:'raking-hq',width:w,height:h,referenceFrame:0,fps:24,sourceTimes:times,collisionMode:'matte2d',
   depth:{width:1,height:1,values:[5],kind:'distance',scale:1,bias:0},camera:{focalPixels:340,focalY:340,homographies:times.map(()=>IDENTITY3)},mattes:[]};
  const project={frames:n,native_profile:{fps:24,timestamps:times},settings:{seed:42}};
  const effect={id:'hq-raking',preset:'smoke_plume_hq',keys:[{phase:'start',frame:-200}],anchors:[{frame:-200,x:.5,y:.82}],tracking:'camera',size:.7,element:{duration_s:1,colliders:[]}};
  const cases=rimTest?[
   {id:'default',name:'Back-left · normal fill',rotation:105,elevation:-10,intensity:1,fill:1,rim:0},
   {id:'lowfill',name:'Back-left · 3% fill',rotation:105,elevation:-10,intensity:1,fill:.03,rim:0},
   {id:'rim',name:'Left rim recipe · 3% fill',rotation:105,elevation:-10,intensity:1,fill:.03,rim:1},
   {id:'right-rim',name:'Mirrored right rim · 3% fill',rotation:15,elevation:-10,intensity:1,fill:.03,rim:1}
  ]:[
   {id:'default',name:'Default · rotation 0°, elevation 60°',rotation:0,elevation:60,intensity:1},
   {id:'front',name:'Front · rotation −120°, elevation 5°',rotation:-120,elevation:5,intensity:4},
   {id:'right',name:'Right rake · rotation −30°, elevation 5°',rotation:-30,elevation:5,intensity:4},
   {id:'left',name:'Left rake · rotation 150°, elevation 5°',rotation:150,elevation:5,intensity:4},
   {id:'back',name:'Back rim · rotation 60°, elevation 5°',rotation:60,elevation:5,intensity:4},
   {id:'top',name:'Overhead · rotation −30°, elevation 90°',rotation:-30,elevation:90,intensity:4}
  ];
  FX.setVolumeFXScene(scene);let baselineAlpha,baselinePhysical;const sampleRGB={};
  for(let frame=0;frame<count;frame++){
   for(const look of cases){
    const e={...effect,element:{...effect.element,light_rotation:look.rotation,light_elevation:look.elevation,light_intensity:look.intensity,fill_intensity:look.fill??1,rim_width:16,rim_strength:look.rim??0}};
    const options={...volumeOptions(project,e,frame,new Map(),w,h),resolution,quality:'high'};
    const rendered=FX.renderVolumeElement(effect.id,effect.preset,options,frame,'raking');
    files[look.id+'/'+String(frame).padStart(5,'0')+'.png']=await png(rendered.rgb);
    if(look.id==='default')files['alpha/'+String(frame).padStart(5,'0')+'.png']=await png(rendered.alpha);
    if(frame===sample){
     const rgb=pixels(rendered.rgb),alpha=pixels(rendered.alpha),physical=FX.volumeFXDiagnostics(effect.id,'raking');sampleRGB[look.id]=rgb;
     if(!baselineAlpha){baselineAlpha=alpha;baselinePhysical=physical;}
     assert(same(alpha,baselineAlpha),look.id+' lighting changed alpha');
     assert(same(physical.dye,baselinePhysical.dye)&&same(physical.velocity,baselinePhysical.velocity),look.id+' lighting changed the fluid state');
     assert(same(physical.particles.positions,baselinePhysical.particles.positions)&&same(physical.particles.mass,baselinePhysical.particles.mass),look.id+' lighting changed particles');
     const expectedAz=(120+look.rotation)*Math.PI/180,el=look.elevation*Math.PI/180,L=physical.lightDirection;
     const expected=[Math.sin(expectedAz)*Math.cos(el),Math.sin(el),Math.cos(expectedAz)*Math.cos(el)];
     assert(L.every((v,i)=>Math.abs(v-expected[i])<1e-9),look.id+' shader direction differs from controls');
     if(rimTest){assert(L[1]<0,'Negative elevation did not reach shader');assert(rendered.metadata.fillIntensity===look.fill,'Fill setting did not reach renderer');}
     if(look.id==='right'||look.id==='left')assert(Math.abs(L[0])>.99&&Math.abs(L[1])<.09&&Math.abs(L[2])<1e-9,look.id+' is not a shallow lateral light');
     const capture=FX.renderVolumeElement(effect.id,effect.preset,options,frame,'capture');
     assert(difference(rgb,pixels(capture.rgb))<.1,look.id+' capture differs from preview');
     const article=document.createElement('article'),title=document.createElement('h2'),img=document.createElement('img');title.textContent=look.name+' · '+look.intensity+'×';img.src=rendered.rgb.toDataURL('image/png');article.append(title,img);gallery.append(article);
     rows.push({...look,azimuth:rendered.metadata.lightAzimuth,direction:L,alphaUnchanged:true,fluidUnchanged:true,particlesUnchanged:true,captureMatchesPreview:true});
    }
   }
   status.textContent='Captured frame '+frame+' / '+(count-1);await new Promise(resolve=>setTimeout(resolve,0));
  }
  if(rimTest){
   assert(difference(sampleRGB.default,sampleRGB.lowfill)>.2,'Fill did not change lighting');
   assert(difference(sampleRGB.lowfill,sampleRGB.rim)<.01,'Guide-only rim changed physical render');
   assert(difference(sampleRGB.rim,sampleRGB['right-rim'])>.2,'Mirrored rim direction did not change lighting');
  }else{
   assert(difference(sampleRGB.left,sampleRGB.right)>.2,'Opposite raking lights did not produce different shading');
   assert(difference(sampleRGB.right,sampleRGB.top)>.2,'Raking and overhead lighting are not visibly distinct');
   assert(difference(sampleRGB.front,sampleRGB.back)>.2,'Front and back lighting are not distinct');
  }
  assert(gl.getError()===0,'WebGL error');
  const report={pass:true,rimTest,width:w,height:h,resolution,quality:'high',onset:-200,sourceFPS:24,sampleFrame:sample,frames:count,rows};
  files['report.json']=strToU8(JSON.stringify(report,null,2));archive=zipSync(files);status.textContent=JSON.stringify(report,null,2);download.disabled=false;
 }catch(error){status.textContent=JSON.stringify({pass:false,error:String(error)},null,2);}finally{FX.clearVolumeFX();run.disabled=false;}
};
download.onclick=()=>{const a=document.createElement('a'),url=URL.createObjectURL(new Blob([archive],{type:'application/zip'}));a.href=url;a.download=(rimTest?'hq-smoke-rim-test-':'hq-smoke-raking-test-')+resolution+'.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
`},bundle:true,write:false,format:'iife'});
writeFileSync(output+'/fixture.js',bundle.outputFiles[0].text);
writeFileSync(output+'/index.html',`<!doctype html><meta charset="utf-8"><title>HQ smoke raking light test</title><style>body{font:14px system-ui;background:#211e2a;color:#eee;padding:24px}button{padding:12px;margin-right:10px}#gallery{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}article{background:#111;padding:12px}h2{font-size:14px}img{width:100%;image-rendering:auto}pre{white-space:pre-wrap}</style><h1>HQ smoke · raking light test</h1><p>Same evolved plume, changing only light. Local GPU test · no model submission. Images below are rendered inputs; exported PNGs are processed by the real painted-guide pipeline.</p><button id="run">Run raking lighting test</button><button id="download" disabled>Download captured inputs</button><div id="gallery"></div><pre>Ready.</pre><script src="fixture.js"></script>`);
console.log(output);
