// Build-only real Viewer harness. Parent exercises it through CUA, never CLI browser automation.
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {dirname} from 'node:path';
import {execFileSync} from 'node:child_process';
const require=createRequire(new URL('../package.json',import.meta.url)),{build}=require('esbuild'),{compile}=require('svelte/compiler');
const out=process.env.KEYFX_VIEWER_CACHE_FIXTURE||'/tmp/bang-viewer-cache-fixture',source=fileURLToPath(new URL('../src/',import.meta.url));mkdirSync(out,{recursive:true});
for(let i=0;i<8;i++)writeFileSync(out+`/plate${i}.svg`,`<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540"><rect width="960" height="540" fill="rgb(${30+i*8},${40+i*8},${50+i*8})"/><rect x="650" width="50" height="540" fill="#72767b"/><text x="30" y="70" fill="white" font-size="40">Synthetic source frame ${i}</text></svg>`);
execFileSync(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-c',`from PIL import Image, ImageDraw
import sys
for i in range(8):
 im=Image.new('RGB',(960,540),(30+i*8,40+i*8,50+i*8));d=ImageDraw.Draw(im);d.rectangle((650,0,700,540),fill=(114,118,123));d.text((30,35),'Synthetic source frame '+str(i),fill='white');im.save(sys.argv[1]+'/plate'+str(i)+'.png')`,out]);
const harness=`<script>
 import {onMount,tick} from 'svelte';import Viewer from '${source}Viewer.svelte';
 let frame=$state(0),calls=$state(0),loaded=$state(0),working=$state(false),quality=$state('high'),selectedId=$state('boom'),cache=$state(null),afterFirst=$state(null),afterSecond=$state(null);
 let project=$state({id:'cache-fixture',mode:'plate',frames:8,source:{file:'synthetic',width:960,height:540},native_profile:{width:960,height:540,fps:24,timestamps:Array.from({length:8},(_,i)=>i/24)},settings:{pipeline:'ltx_elements'},geometry:{reference_frame:0,collision_mode:'matte2d',depth:null,mattes:[]},effects:[{id:'boom',preset:'explosion',keys:[{phase:'onset',frame:0}],anchors:[{frame:0,x:.5,y:.5}],size:1,tracking:'camera',element:{duration_s:1,opacity:1,gain:1,colliders:[]}}],flashes:[]});
 const presets={explosion:{name:'Explosion',phases:[{phase:'onset'}],direction:{kind:'none'}}};
 const path=new Float32Array(Array.from({length:16},(_,i)=>i%2?270:480)),paths=new Map([['boom',path]]);
 onMount(()=>{const fn=()=>calls++;window.addEventListener('viewer-fx-render',fn);return()=>window.removeEventListener('viewer-fx-render',fn);});
 async function loop(){working=true;for(let i=0;i<8;i++){frame=i;await tick();await new Promise(requestAnimationFrame);}await tick();if(afterFirst===null)afterFirst=calls;else afterSecond=calls;working=false;}
</script>
<h1>Viewer cache · real component</h1><p>Synthetic local frames and renderer counter. No plate, provider or model requests.</p>
<div class="controls"><button disabled={loaded<8||working} onclick={loop}>Play full loop</button><button disabled={working} onclick={()=>selectedId=selectedId?null:'boom'}>Toggle selection handles</button><button disabled={working} onclick={()=>project.effects[0].element.opacity=project.effects[0].element.opacity===1?.5:1}>Change opacity</button><button disabled={working} onclick={()=>quality=quality==='high'?'draft':'high'}>Change quality</button><button disabled={working} onclick={()=>project.geometry.mattes=[{id:'new',colliderBlur:8}]}>Change collider</button></div>
<Viewer {project} {presets} {frame} {selectedId} armed={null} tracker={{}} {paths} {quality} showHandles={true} onplace={()=>{}} onchange={()=>{}} onselect={v=>selectedId=v} onframes={v=>loaded=v} oncache={v=>cache=v}/>
<p role="status">Loaded: {loaded}/8 · Expensive renderer calls: {calls} · First loop: {afterFirst??'pending'} · Second loop: {afterSecond??'pending'}</p><pre id="report">{JSON.stringify({frame,calls,afterFirst,afterSecond,quality,opacity:project.effects[0].element.opacity,cache},null,2)}</pre>`;
writeFileSync(out+'/Harness.svelte',harness);
const mocks={
 'volumeFX.ts':`export function setVolumeFXScene(){}export function pruneVolumeFX(){}export function renderVolumeElement(id,kind,o,frame){window.dispatchEvent(new Event('viewer-fx-render'));const c=document.createElement('canvas');c.width=o.w;c.height=o.h;const g=c.getContext('2d');g.fillStyle='rgba(255,95,24,.8)';g.beginPath();g.arc(o.x,o.y,55+frame*7,0,Math.PI*2);g.fill();return {composite:c};}`,
 'volumeScene.ts':`export const isElementProject=p=>p.settings.pipeline==='ltx_elements';export const nativeFPS=p=>p.native_profile?.fps??24;export const nativeAspect=p=>(p.native_profile?.width??960)/(p.native_profile?.height??540);export const sceneKey=p=>JSON.stringify([p.id,p.geometry,p.effects.map(e=>[e.keys,e.element.duration_s,e.element.colliders])]);export async function loadVolumeScene(p){return {key:sceneKey(p)}}`,
 'mockup.ts':`export const W=960,H=540;export const dirVec=()=>[0,-1];export const endOf=()=>7,onsetOf=()=>0;export function renderLayers(){}`,
 'api.ts':`export const api={frameUrl:(id,f)=>'/plate'+f+'.png',stampUrl:()=>'/plate0.png'};`,
 'squibfx.ts':`export function pruneSquib(){}`,
 'collider.ts':`export const colliderKey=()=>'';export async function loadCollider(){return null;}`,
};
const bundle=await build({stdin:{contents:`import {mount} from 'svelte';import Harness from '${out}/Harness.svelte';mount(Harness,{target:document.querySelector('#app')});`,resolveDir:source,loader:'js'},bundle:true,write:false,format:'iife',conditions:['browser'],nodePaths:[source+'../node_modules'],plugins:[{name:'isolated-boundaries',setup(b){b.onLoad({filter:/\/src\/(volumeFX|volumeScene|mockup|api|squibfx|collider)\.ts$/},args=>({contents:mocks[args.path.split('/').at(-1)],loader:'ts'}));b.onLoad({filter:/\.svelte$/},args=>({contents:compile(readFileSync(args.path,'utf8'),{filename:args.path,generate:'client',css:'injected'}).js.code,loader:'js',resolveDir:dirname(args.path)}));}}]});
writeFileSync(out+'/fixture.js',bundle.outputFiles[0].text);
writeFileSync(out+'/index.html','<!doctype html><html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'self\';script-src \'self\';style-src \'self\' \'unsafe-inline\';img-src \'self\' blob: data:;connect-src \'self\'"><title>Viewer cache fixture</title><style>body{background:#211f26;color:#eee;font:16px system-ui;margin:20px}#app{max-width:1000px}button{padding:8px;margin:4px;background:#333;color:white;border:1px solid #999;border-radius:5px}.controls{margin:12px 0}.viewer{max-width:900px}pre{font-size:12px}</style><div id="app"></div><script src="fixture.js"></script></html>');
console.log(out);
