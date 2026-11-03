// No browser, plate, provider or native project writes.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {performance} from 'node:perf_hooks';
const require=createRequire(new URL('../package.json',import.meta.url)),{build}=require('esbuild');
const bundle=await build({entryPoints:[fileURLToPath(new URL('../src/viewerCache.ts',import.meta.url))],bundle:true,write:false,format:'esm'});
const {ViewerFrameCache,viewerVisualKey,viewerPlateKey,ViewerPlaybackClock,VIEWER_CACHE_BYTES}=await import('data:text/javascript;base64,'+Buffer.from(bundle.outputFiles[0].text).toString('base64'));
const disposed=[],cache=new ViewerFrameCache(12,item=>disposed.push(item));
assert(cache.configure('one'));assert(!cache.configure('one'));
cache.put(0,'zero',4);cache.put(1,'one',4);cache.put(2,'two',4);assert.equal(cache.get(0),'zero');
cache.put(3,'three',4);assert.equal(cache.get(1),undefined,'LRU evicts least-recently-read frame.');assert.deepEqual(disposed,['one']);
cache.put(0,'replacement',4);assert.deepEqual(disposed,['one','zero']);assert.equal(cache.stats().bytes,12);
assert(!cache.put(10,'oversize',13));assert(disposed.includes('oversize'));assert.equal(cache.stats().bytes,12);
assert(!cache.put(11,'invalid',NaN));assert(disposed.includes('invalid'));
assert(cache.configure('new quality'));assert.equal(cache.stats().frames,0);assert.equal(cache.stats().bytes,0);assert.equal(new Set(disposed).size,disposed.length);
cache.put(4,'cleanup',4);cache.clear();assert.equal(disposed.at(-1),'cleanup');assert.equal(cache.stats().frames,0);

const p={id:'shot',frames:85,source:{file:'original.mov',width:2558,height:1462},native_profile:{timestamps:[0,.04],colour:{transfer:'bt709'}},settings:{pipeline:'ltx_elements'},effects:[{id:'boom',preset:'explosion',anchors:[{frame:0,x:.7,y:.5}],keys:[{phase:'onset',frame:0}],element:{duration_s:3.5,opacity:1,density:1,colliders:['pole']}}],geometry:{collision_mode:'depth3d',depth:{sha256:'one'},mattes:[{id:'pole',colliderBlur:0}]},flashes:[{id:'f',frame:2,origin:{x:.2,y:.2},tip:{x:.3,y:.2}}],renders:[]};
const presets={explosion:{name:'Explosion',phases:[{phase:'onset',stamp:'fire.png'}]}},paths=new Map([['boom',new Float32Array([1,2,3,4])]]);
const key=(project=p,ps=presets,path=paths,quality='high',solo=null,vol='geometry',collider=null,revision=0)=>viewerVisualKey(project,ps,path,quality,solo,vol,collider,revision);
const original=key(),clone=value=>JSON.parse(JSON.stringify(value));
assert.equal(key({...p,renders:[{status:'done',approved:true}]}),original,'Approval/status polling is not a visual edit.');
assert.equal(key(p,presets,new Map([['boom',new Float32Array([1,2,3,4])]])),original,'Equivalent path allocations retain warm imagery.');
for(const mutate of [v=>v.effects[0].element.opacity=.5,v=>v.effects[0].element.density=2,v=>v.effects[0].anchors[0].x=.8,v=>v.effects[0].keys[0].frame=1,v=>v.geometry.mattes[0].colliderBlur=8,v=>v.geometry.depth.sha256='two',v=>v.geometry.collision_mode='matte2d',v=>v.flashes[0].origin.x=.3,v=>v.native_profile.timestamps[1]=.05,v=>v.native_profile.colour.transfer='smpte2084',v=>v.settings.pipeline='h3',v=>v.source.file='other.mov',v=>v.lighting='night']){
 const changed=clone(p);mutate(changed);assert.notEqual(key(changed),original,'Visual edit retained an obsolete rendered frame.');
}
assert.notEqual(key(p,presets,paths,'draft'),original);assert.notEqual(key(p,presets,paths,'high','boom'),original);
assert.notEqual(key(p,presets,paths,'high',null,'changed'),original);assert.notEqual(key(p,presets,paths,'high',null,'geometry','matte'),original);assert.notEqual(key(p,presets,paths,'high',null,'geometry',null,1),original);
const changedPresets=clone(presets);changedPresets.explosion.phases[0].stamp='new.png';assert.notEqual(key(p,changedPresets),original);
paths.get('boom')[1]=999;assert.notEqual(key(),original,'Tracking content edits must invalidate even when array identity is stable.');
assert.notEqual(viewerPlateKey({...p,frames:86}),viewerPlateKey(p));

// The current85-frame960x540 viewport fits the default bounded memory budget.
const viewport=new ViewerFrameCache(VIEWER_CACHE_BYTES),bytes=960*540*4;viewport.configure('real viewport');let renderCalls=0;
for(let pass=0;pass<2;pass++)for(let frame=0;frame<85;frame++)if(viewport.get(frame)===undefined){renderCalls++;viewport.put(frame,frame,bytes);}
assert.equal(renderCalls,85,'Second traversal must bypass all85 expensive renders.');assert.equal(viewport.stats().frames,85);assert.equal(viewport.stats().bytes,176256000);
for(let frame=85;frame<200;frame++)viewport.put(frame,frame,bytes);
assert(viewport.stats().bytes<=VIEWER_CACHE_BYTES);assert.equal(viewport.stats().frames,129);

const cold=new ViewerPlaybackClock(0,0,60);
assert.equal(cold.next(100,0,85,60,undefined,85/60,true),1,'Cold rendering does not skip six frames after100ms.');
assert.equal(cold.next(200,1,85,60,undefined,85/60,true),2);
const loading=new ViewerPlaybackClock(0,0,24);
assert.equal(loading.next(2000,0,85,24,undefined,85/24,true,false),0,'Do not skip a frame while plate/geometry is loading.');
assert.equal(loading.next(2010,0,85,24,undefined,85/24,true,true),0);assert.equal(loading.next(2043,0,85,24,undefined,85/24,true,true),1);
const pts=[10,10.1,10.22,10.3],vfr=new ViewerPlaybackClock(0,0,24,pts);
assert.equal(vfr.next(50,0,4,24,pts,.45,true),0);assert.equal(vfr.next(110,0,4,24,pts,.45,true),1);
assert.equal(vfr.next(200,1,4,24,pts,.45,true),1);assert.equal(vfr.next(250,1,4,24,pts,.45,true),2);
assert.equal(vfr.next(300,2,4,24,pts,.45,false),2);assert.equal(vfr.next(340,2,4,24,pts,.45,false),3,'Warm playback follows source PTS, not24Hz rounding.');
const warm60=new ViewerPlaybackClock(0,0,60);assert.equal(warm60.next(167,0,85,60,undefined,85/60,false),10);
const end=new ViewerPlaybackClock(0,3,24,pts);assert.equal(end.next(100,3,4,24,pts,.45,true),3);assert.equal(end.next(151,3,4,24,pts,.45,true),0);
const started=performance.now();for(let i=0;i<100000;i++)viewport.get(100+i%100);
console.log(JSON.stringify({pass:true,secondPassExpensiveRenders:0,cachedViewportFrames:85,estimatedViewportBytes:176256000,budget:VIEWER_CACHE_BYTES,capacity:129,lookup100kMs:Math.round((performance.now()-started)*100)/100}));
console.log('Viewer cache passes: bounded LRU/release, full visual invalidation, stable polling/paths, cold no-skip warmup, native/VFR replay.');
