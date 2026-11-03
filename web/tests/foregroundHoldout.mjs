// No browser or provider: camera-depth visibility must outlive the finite solver box.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(new URL('../package.json',import.meta.url)),{build}=require('esbuild');
const bundle=await build({entryPoints:[fileURLToPath(new URL('../src/volumeGeometry.ts',import.meta.url))],bundle:true,write:false,format:'esm'});
const K=await import('data:text/javascript;base64,'+Buffer.from(bundle.outputFiles[0].text).toString('base64'));
const width=160,height=90,grid={dim:[16,8,16],tiles:[4,4],boxMin:[-2,-1,-12],vox:.25,aw:64,ah:32};
function fields(depth,role='both',coverage=255,extra={},mode='depth3d'){
 const matte={id:'pillar',width,height,frames:[new Uint8Array(width*height).fill(coverage)],static:true,coordinates:'reference',role,kind:'hard',thickness:.2,depth,...extra};
 return K.voxelizeScene({key:'foreground',width,height,referenceFrame:0,fps:24,collisionMode:mode,depth:{width:1,height:1,values:[7],kind:'distance',scale:1,bias:0},camera:{focalPixels:140,homographies:[K.IDENTITY3]},mattes:[matte]},grid,0);
}
const foreground=fields(2);
assert(foreground.holdout.some(v=>v===255),'Foreground pillar outside the solver box must still occlude effects behind it.');
assert(!foreground.solid.some(Boolean),'The foreground optical holdout must not create an infinite physical collider.');
const behind=fields(14);assert(!behind.holdout.some(Boolean));assert(!behind.solid.some(Boolean));
const crossing=fields(10),near=[],far=[],physicalZ=new Set();
for(let z=0;z<16;z++)for(let y=0;y<8;y++)for(let x=0;x<16;x++){
 const i=K.gridIndex(grid,x,y,z),distance=12-(z+.5)*.25;
 (distance<9.8?near:distance>10.3?far:[]).push(crossing.holdout[i]);
 if(crossing.solid[i])physicalZ.add(z);
}
assert(near.length&&near.every(v=>v===0),'Smoke in front of the pillar remains visible.');
assert(far.length&&far.every(v=>v===255),'Smoke behind the pillar remains held out past its finite thickness.');
assert(physicalZ.size>0&&physicalZ.size<4,'Physical collision still occupies only a thin surface band.');
assert(!fields(10,'collide').holdout.some(Boolean));
assert(!fields(10,'holdout').solid.some(Boolean));
assert(fields(2,'holdout',128).holdout.some(v=>v===128),'Optical antialiasing coverage is retained.');
assert(!fields(2,'holdout',255,{holdoutPost:true}).holdout.some(Boolean),'Post holdouts stay outside baked guide visibility.');
assert(fields(14,'holdout',255,{},'matte2d').holdout.some(v=>v===255),'2D foreground behaviour remains unchanged.');
const strip=new Uint8Array(width*height);for(let y=0;y<height;y++)strip.fill(128,y*width+71,y*width+79);
const camera={focalPixels:140,homographies:[K.IDENTITY3,[1,0,-13,0,1,2,0,0,1]]};
const matte={id:'pole',width,height,frames:[strip],static:true,coordinates:'reference',role:'both',kind:'hard',thickness:.2,depth:2};
const scene={key:'precise',width,height,referenceFrame:0,fps:24,collisionMode:'depth3d',depth:{width:1,height:1,values:[7],kind:'distance',scale:1,bias:0},camera,mattes:[matte]};
const precise=K.screenForegroundScene(scene,grid);
assert(precise.mattes[0].holdoutScreen,'A fully foreground pole uses pixel optical coverage.');
assert(!matte.holdoutScreen,'Classifying an effect must not mutate the shared scene.');
const a=K.voxelizeScene(scene,grid,0),b=K.voxelizeScene(precise,grid,0);
assert(!b.holdout.some(Boolean),'Pixel holdout must be excluded from voxel optical coverage.');
assert.deepEqual(a.solid,b.solid,'Pixel optical edges must preserve the physical collider.');
assert.equal(K.fallbackVisibility(precise,0,74,30,10),127/255);
assert.equal(K.fallbackVisibility(precise,1,61,32,10),127/255,'Static scene holdout follows camera once.');
assert.equal(K.fallbackVisibility(precise,1,48,34,10),1,'The camera transform must not be applied twice.');
assert.equal(K.fallbackVisibility(precise,1,70,32,10),1,'No coarse mask halo remains outside the tracked pole.');
for(const extra of [{depth:10},{depth:14},{role:'collide'},{holdoutPost:true}])assert(!K.screenForegroundScene({...scene,mattes:[{...matte,...extra}]},grid).mattes[0].holdoutScreen);
assert.equal(K.screenForegroundScene({...scene,collisionMode:'matte2d'},grid).mattes[0],matte);
console.log('Foreground holdout geometry passes: outside-box occlusion, front/back ordering, finite collision, roles, soft edges and separate post mattes.');
