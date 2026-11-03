// Build only. Open the generated page with CUA; no browser or model requests here.
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {mkdirSync,writeFileSync} from 'node:fs';
const require=createRequire(new URL('../package.json',import.meta.url)),{build}=require('esbuild');
const out=process.env.KEYFX_FOREGROUND_FIXTURE||'/tmp/bang-foreground-holdout-fixture';mkdirSync(out,{recursive:true});
const bundle=await build({stdin:{resolveDir:fileURLToPath(new URL('../src/',import.meta.url)),loader:'ts',contents:`
import * as FX from './volumeFX';import {IDENTITY3} from './volumeGeometry';import {gl} from './squib/gl/context.js';
const status=document.querySelector('pre'),button=document.querySelector('button');
const assert=(v,m)=>{if(!v)throw Error(m);};
button.onclick=()=>{button.disabled=true;status.textContent='Running real GPU foreground tests…';
 try{
  const w=192,h=108,n=37,rows={},fields={},times=Array.from({length:n},(_,i)=>i/24);
  for(const [label,depth,role,cover] of [['unmasked',null,'holdout',255],['foreground',.5,'both',255],['background',50,'both',255],['partial',.5,'holdout',128],['collide-only',.5,'collide',255]]){
   const mattes=depth===null?[]:[{id:'pillar',width:w,height:h,frames:[new Uint8Array(w*h).fill(cover)],static:true,coordinates:'reference',depth,role,kind:'hard',thickness:.2}];
   FX.setVolumeFXScene({key:label,width:w,height:h,referenceFrame:0,fps:24,sourceTimes:times,collisionMode:'depth3d',depth:{width:1,height:1,values:[5],kind:'distance',scale:1,bias:0},camera:{focalPixels:168,focalY:168,homographies:times.map(()=>IDENTITY3)},mattes});
   const frame=FX.renderVolumeElement(label,'explosion',{w,h,n,x:96,y:74,start:0,end:36,size:.5,seed:42,quality:'draft',resolution:24,matteIds:mattes.map(m=>m.id)},24);
   const raw=frame.element.getContext('2d').getImageData(0,0,w,h).data,held=frame.composite.getContext('2d').getImageData(0,0,w,h).data;
   let rawAlpha=0,heldAlpha=0;for(let i=3;i<raw.length;i+=4){rawAlpha+=raw[i];heldAlpha+=held[i];}
   rows[label]={rawAlpha,heldAlpha,sourceDepth:frame.metadata.sourceDepth};fields[label]=FX.volumeFXDiagnostics(label).dye;
   const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;canvas.getContext('2d').drawImage(frame.composite,0,0);const figure=document.createElement('figure');figure.append(canvas);const caption=document.createElement('figcaption');caption.textContent=label;figure.append(caption);document.querySelector('#images').append(figure);
   FX.clearVolumeFX();
  }
  assert(rows.unmasked.rawAlpha>1000,'Empty FX fixture');
  assert(rows.foreground.heldAlpha===0,'Foreground object outside box was ignored');
  assert(rows.background.heldAlpha===rows.background.rawAlpha,'Background object incorrectly hides foreground FX');
  assert(rows['collide-only'].heldAlpha===rows['collide-only'].rawAlpha,'Collider-only object became a holdout');
  assert(Math.abs(rows.partial.heldAlpha/rows.partial.rawAlpha-127/255)<.02,'Partial optical edge hardened or applied twice');
  let physicsDelta=0;for(const name of ['foreground','background','partial','collide-only'])for(let i=0;i<fields.unmasked.length;i++)physicsDelta=Math.max(physicsDelta,Math.abs(fields.unmasked[i]-fields[name][i]));
  assert(physicsDelta===0,'An out-of-box optical mask altered physical fluid density');assert(gl.getError()===0,'WebGL error');
  const edges={};
  for(const dx of [0,5]){
   const pixels=new Uint8Array(w*h);for(let y=0;y<h;y++)pixels.fill(255,y*w,y*w+95);
   const camera=times.map((_,i)=>i===0?IDENTITY3:[1,0,dx,0,1,0,0,0,1]);
   FX.setVolumeFXScene({key:'edge-'+dx,width:w,height:h,referenceFrame:0,fps:24,sourceTimes:times,collisionMode:'depth3d',depth:{width:1,height:1,values:[5],kind:'distance',scale:1,bias:0},camera:{focalPixels:168,focalY:168,homographies:camera},mattes:[{id:'edge',width:w,height:h,frames:[pixels],static:true,coordinates:'reference',depth:.5,role:'both',kind:'hard',thickness:.2}]});
   const frame=FX.renderVolumeElement('edge-'+dx,'explosion',{w,h,n,x:96,y:74,start:0,end:36,size:.5,seed:42,quality:'draft',resolution:24,matteIds:['edge']},24);
   const raw=frame.element.getContext('2d').getImageData(0,0,w,h).data,held=frame.composite.getContext('2d').getImageData(0,0,w,h).data;
   let mismatches=0,leak=0,excess=0;for(let y=0;y<h;y++)for(let x=0;x<w;x++){
    const i=4*(y*w+x)+3,masked=x>=dx&&x<95+dx,expected=masked?0:raw[i];
    if(Math.abs(expected-held[i])>1)mismatches++;if(masked)leak+=held[i];else excess+=Math.max(0,raw[i]-held[i]);
   }
   assert(frame.metadata.screenHoldoutIds.includes('edge'),'Foreground edge did not use pixel coverage');
   assert(mismatches===0&&leak===0&&excess===0,'Pixel edge is offset, coarse or double tracked: '+JSON.stringify({dx,mismatches,leak,excess}));
   edges[dx]={mismatches,leak,excess,screenHoldoutIds:frame.metadata.screenHoldoutIds};FX.clearVolumeFX();
  }
  assert(gl.getError()===0,'WebGL error after pixel tests');
  status.textContent=JSON.stringify({pass:true,rows,edges,physicsDelta,glError:0},null,2);
 }catch(error){status.textContent=JSON.stringify({pass:false,error:String(error)},null,2);}finally{button.disabled=false;}
};
`},bundle:true,write:false,format:'iife'});
writeFileSync(out+'/fixture.js',bundle.outputFiles[0].text);
writeFileSync(out+'/index.html','<!doctype html><html><meta charset="utf-8"><title>Foreground holdout GPU check</title><style>body{background:#222;color:#eee;font:16px system-ui;margin:24px}button{padding:12px}#images{display:flex;flex-wrap:wrap}figure{margin:8px}canvas{background:#555;width:288px;image-rendering:auto}pre{white-space:pre-wrap}</style><h1>Foreground holdout GPU check</h1><p>Isolated generated fixture. No plate, project edits or model calls.</p><button>Run checks</button><pre>Ready</pre><div id="images"></div><script src="fixture.js"></script></html>');
console.log(out);
