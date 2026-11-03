// Build only; exercise the real Svelte timeline with CUA. No app/model requests.
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {dirname} from 'node:path';
const require=createRequire(new URL('../package.json',import.meta.url)),{build}=require('esbuild'),{compile}=require('svelte/compiler');
const out=process.env.KEYFX_TIMELINE_FIXTURE||'/tmp/bang-effect-duration-fixture';mkdirSync(out,{recursive:true});
const source=fileURLToPath(new URL('../src/',import.meta.url));
const harness=`<script>
 import Timeline from '${source}Timeline.svelte';
 const initial=[{id:'blast',preset:'explosion',where:'fixture',keys:[{phase:'onset',frame:0,x:.5,y:.5},{phase:'aftermath',frame:20}],anchors:[{frame:0,x:.5,y:.5}],element:{duration_s:2,seed:42,colliders:['pillar']}},{id:'smoke',preset:'smoke_grenade',where:'fixture',keys:[{phase:'start',frame:20,x:.7,y:.6}],anchors:[{frame:20,x:.7,y:.6}],element:{duration_s:2,seed:43,colliders:[]}}];
 let effects=$state(JSON.parse(localStorage.getItem('bang-timeline-fixture')||JSON.stringify(initial))),frame=$state(10),selectedId=$state('blast'),saves=$state(0);
 const presets={explosion:{name:'Explosion',phases:[{phase:'onset'},{phase:'aftermath',persist:true}]},smoke_grenade:{name:'Smoke grenade',phases:[{phase:'start'},{phase:'fill',persist:true}]}};
 function save(){saves++;localStorage.setItem('bang-timeline-fixture',JSON.stringify(effects));}
</script>
<h1>Effect duration timeline</h1><p>Isolated UI fixture. The real timeline component; no project or model requests.</p>
<Timeline frames={85} {frame} {effects} {presets} {selectedId} playing={false} keyFrames={[]} fps={24} clipDuration={85/24} elementMode={true} onseek={f=>frame=f} onselect={id=>selectedId=id} onchange={save} onplay={()=>{}}/>
<p role="status">Saved changes: {saves} · Frame: {frame}</p>
<label>Fixture playhead <input type="number" min="0" max="84" bind:value={frame}/></label>
<button onclick={()=>{localStorage.removeItem('bang-timeline-fixture');location.reload();}}>Reset fixture</button>
<pre>{JSON.stringify(effects,null,2)}</pre>`;
writeFileSync(out+'/Harness.svelte',harness);
const bundle=await build({stdin:{contents:`import {mount} from 'svelte';import Harness from '${out}/Harness.svelte';mount(Harness,{target:document.querySelector('#app')});`,resolveDir:source,loader:'js'},bundle:true,write:false,format:'iife',conditions:['browser'],nodePaths:[fileURLToPath(new URL('../node_modules',import.meta.url))],plugins:[{name:'svelte',setup(build){build.onLoad({filter:/\.svelte$/},args=>({contents:compile(readFileSync(args.path,'utf8'),{filename:args.path,generate:'client',css:'injected'}).js.code,loader:'js',resolveDir:dirname(args.path)}));}}]});
writeFileSync(out+'/fixture.js',bundle.outputFiles[0].text);
writeFileSync(out+'/index.html','<!doctype html><html><meta charset="utf-8"><title>FX duration timeline fixture</title><style>:root{--panel:#202127;--line:#4b4d56;--muted:#bec1cb;--accent2:#edf86c}body{background:#141519;color:#eee;font:15px system-ui;margin:24px}#app{max-width:1050px}button,input{font:inherit;color:inherit;background:#30323a;border:1px solid #6f7480;border-radius:5px;padding:6px}.row{display:flex;align-items:center;gap:8px}.grow{flex:1}.small{font-size:12px}.muted{color:var(--muted)}pre{font-size:12px}</style><div id="app"></div><script src="fixture.js"></script></html>');
console.log(out);
