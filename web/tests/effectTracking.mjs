import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const local=createRequire(new URL('../package.json',import.meta.url));
const {build}=local('esbuild');
const bundle=await build({entryPoints:[fileURLToPath(new URL('../src/effectTracking.ts',import.meta.url))],bundle:true,write:false,format:'esm'});
const {effectTrackingPath}=await import('data:text/javascript;base64,'+Buffer.from(bundle.outputFiles[0].text).toString('base64'));
const anchors=[{frame:0,x:.25,y:.5}],automatic=new Float32Array([100,200,110,220]);
let calls=[];const tracker={path:(as,camera)=>{calls.push({as,camera});return automatic;}};
for(const [mode,cameraDefault,want] of [['camera',false,true],['features',true,false],[undefined,true,true],[undefined,false,false]]){
 assert.equal(effectTrackingPath(anchors,2,mode,tracker,cameraDefault,100,100),automatic);
 assert.deepEqual(calls.at(-1),{as:anchors,camera:want});
}
const previousCalls=calls.length;
assert.deepEqual([...effectTrackingPath(anchors,3,'screen',tracker,true,100,100)],[25,50,25,50,25,50]);
assert.equal(calls.length,previousCalls,'Screen mode must bypass automatic camera and feature motion.');
const manual=[{frame:4,x:.75,y:.25},{frame:2,x:.25,y:.75}];
const path=effectTrackingPath(manual,7,'screen',tracker,true,100,100);
assert.deepEqual([...path],[25,75,25,75,25,75,50,50,75,25,75,25,75,25]);
assert.deepEqual(effectTrackingPath(manual,7,'features',null,false,100,100),path,'Manual anchors must remain usable before tracking loads.');
assert.deepEqual([...effectTrackingPath([],2,'screen',null,false,100,100)],[0,0,0,0]);
console.log('Tracking modes pass: explicit features, camera defaults, fixed screen placement and manual anchors.');
