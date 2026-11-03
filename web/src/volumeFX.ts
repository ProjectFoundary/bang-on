// @ts-nocheck — adapter around the unmodified plain-JS Squib volume solver.
import { Sim3D, boxFor } from './squib/sim/Sim3D.js';
import { FS3, FS_RAYMARCH, VOL_HEAD } from './squib/gl/shaders3d.js';
import { PRESETS } from './squib/presets.js';
import { state, app } from './squib/state.js';
import { gl, glCanvas, makeTex, pass, program } from './squib/gl/context.js';
import { makeTargets, freeTargets } from './squib/render/layers.js';
import { lin, rng, seedMix } from './squib/util.js';
import {supportsFineParticles} from './squib/sim/fine-particles3d.js';
import { volumeShader, windSmokeMixShader } from './volumeShaders';
import { depthAt, emitterDepth, unproject, inverseH, projectH, sourceTime, sourceFrameAt, fallbackVisibility, validateVolumeScene, voxelizeScene, screenForegroundScene, cameraBoxInterval, IDENTITY3 } from './volumeGeometry';
import type { VolumeFXScene, VolumeGrid } from './volumeGeometry';
export type { VolumeFXScene, VolumeMatte, DepthReference } from './volumeGeometry';

export type VolumeElementOptions = { w:number;h:number;n:number;x:number;y:number;start:number;end:number;size:number;seed:number;wind?:number;
  windVector?:number[];windResponse?:number;windDispersion?:number;tracking?:'camera'|'features'|'screen';
  angle?:number; color?:string;path?:Float32Array;bounds?:{x:number;y:number;width:number;height:number};
  onsetSeconds?:number;durationSeconds?:number;sourceTimeSeconds?:number;depthOffset?:number;direction?:number[];quality?:'draft'|'high';resolution?:number;matteIds?:string[];shading?:number;density?:number;brightness?:number;lightIntensity?:number;fillIntensity?:number;lightRotation?:number;lightElevation?:number;compositeOnly?:boolean };
export type VolumeElementFrame = { element:HTMLCanvasElement; composite:HTMLCanvasElement; alpha:HTMLCanvasElement; visibility:HTMLCanvasElement;
  rgb:HTMLCanvasElement; light:HTMLCanvasElement|null;shadow:HTMLCanvasElement|null;metadata:Record<string,unknown> };

const clone=value=>JSON.parse(JSON.stringify(value));
const stockScene=clone(state.scene),cache=new Map();let scene:VolumeFXScene|null=null,volumePrograms,displayProgram;
function initialize(){
  if(volumePrograms)return;
  // Own the entire program set. Mutating native FS3 or P3 would leak collider,
  // camera and wind state into ordinary Squib volumes, and fail if stock P3
  // had already compiled before the first adapter effect was placed.
  volumePrograms=Object.fromEntries(Object.entries(FS3).map(([name,source])=>[name,program(volumeShader(name,source))]));
  volumePrograms.raymarch=program(volumeShader('raymarch',FS_RAYMARCH));
  volumePrograms.raymarchFines=program(volumeShader('raymarch',FS_RAYMARCH.replace('#version 300 es','#version 300 es\n#define ANALYTIC_FINES')));
  volumePrograms.forcesFines=program(volumeShader('forces',FS3.forces).replace('#version 300 es','#version 300 es\n#define PARTICLE_FINES'));
  volumePrograms.windSmokeMix=program(windSmokeMixShader(VOL_HEAD));
  displayProgram=program(`#version 300 es
    precision highp float;in vec2 vUv;out vec4 o;uniform sampler2D uS,uF;
    vec3 srgb(vec3 x){return mix(12.92*x,1.055*pow(max(x,vec3(0.)),vec3(1./2.4))-.055,step(vec3(.0031308),x));}
    void main(){vec4 s=texture(uS,vUv),f=texture(uF,vUv);float a=clamp(s.a,0.,1.);
      vec3 soot=max(s.rgb-f.rgb,vec3(0.));vec3 pre=soot+a*(1.-exp(-max(f.rgb,vec3(0.))/max(a,1e-6)*.055));
      o=vec4(clamp(srgb(pre/max(a,1e-6)),0.,1.),a);}`);
}
/** Explicit opt-in for finite collider/wind shaders; stock Sim3D stays intact. */
export function createVolumeSimulator(){initialize();const sim=new Sim3D();sim.P=volumePrograms;return sim;}
function canvas(w,h){const c=document.createElement('canvas');c.width=w;c.height=h;return c;}
export function setVolumeFXScene(value:VolumeFXScene|null){
  if(value)validateVolumeScene(value);
  if(scene?.key!==value?.key){for(const c of cache.values())destroy(c);cache.clear();}
  scene=value;
}
export function volumeFXScene(){return scene;}
export function pruneVolumeFX(ids:Set<string>,owner='preview'){
  for(const [key,c]of cache)if(key.startsWith(owner+':')&&!ids.has(c.effectId)){destroy(c);cache.delete(key);}
}
function destroy(c){c.sim.destroy();freeTargets(c.target);if(c.cgDepth)gl.deleteTexture(c.cgDepth);for(const level of new Set([c.fineMask,c.coarseMask]))for(const texture of [level.solid,level.holdout,level.motion])gl.deleteTexture(texture);}
export function clearVolumeFX(){for(const c of cache.values())destroy(c);cache.clear();scene=null;}
/** Optional readback for collision diagnostics; never reads source photography. */
export function volumeFXDiagnostics(effectId:string,owner='preview',includeHeat=false){
  const c=cache.get(`${owner}:${effectId}`);if(!c)return null;
  const read=field=>{const values=new Float32Array(field.w*field.h*4);gl.bindFramebuffer(gl.FRAMEBUFFER,field.fbo);gl.readPixels(0,0,field.w,field.h,gl.RGBA,gl.FLOAT,values);return values;};
  const dye=read(c.v.dye.read),velocity=read(c.v.vel.read);let coreDensity=0,coreVelocity=0,totalDensity=0;
  for(let i=0;i<c.grid.aw*c.grid.ah;i++){totalDensity+=dye[i*4+3];if(c.fields?.solid[i])coreDensity=Math.max(coreDensity,Math.abs(dye[i*4+3]));}
  for(let i=0;i<velocity.length/4;i++)if(c.coarseMask.fields?.solid[i])for(let a=0;a<3;a++)coreVelocity=Math.max(coreVelocity,Math.abs(velocity[i*4+a]));
  let particles=null;
  if(c.v.smokeParticles){
    const smoke=c.v.smokeParticles,positions=read(smoke.position.read),mass=read(smoke.mass.read),optical=read(smoke.density);let live=0,blocked=0,opticalCore=0;
    for(let i=0;i<smoke.count;i++)if(mass[i*4]>0){live++;const x=Math.floor(positions[i*4]),y=Math.floor(positions[i*4+1]),z=Math.floor(positions[i*4+2]);
      if(x>=0&&y>=0&&z>=0&&x<c.grid.dim[0]&&y<c.grid.dim[1]&&z<c.grid.dim[2]){const k=(Math.floor(z/c.grid.tiles[0])*c.grid.dim[1]+y)*c.grid.aw+(z%c.grid.tiles[0])*c.grid.dim[0]+x;if(c.fields?.solid[k])blocked++;}}
    for(let i=0;i<c.grid.aw*c.grid.ah;i++)if(c.fields?.solid[i])opticalCore=Math.max(opticalCore,Math.abs(optical[i*4+3]));
    particles={count:smoke.count,live,blocked,opticalCore,positions,mass};
  }
  return {dye,velocity,coreDensity,coreVelocity,totalDensity,particles,frame:c.v.frame,grid:c.grid,sourceWorld:c.emitterWorld.slice(),
    tracking:c.tracking,windWorld:c.windWorld.slice(),windResponse:c.windResponse,box:c.v.box,
    lightDirection:scoped(c,()=>c.sim.sunDir()),
    windDispersion:c.windDispersion,heat:includeHeat&&c.v.hasFire?read(c.v.heat.read):null,attributes:includeHeat?read(c.v.attr.read):null,
    debris:c.v.parts.debris.length,sparks:c.v.parts.sparks.length,collisionMask:c.maskCounts};
}
function scoped(s,fn){
  const previous={scene:state.scene,cam:state.cam,plate:state.plate,instances:state.instances,fps:state.fps,sim3d:app.sim3d,freeze:app.freezeEffectsFrame,w:glCanvas.width,h:glCanvas.height};
  try{state.scene=s.scene;state.cam=s.cam;state.plate=s.plate;state.instances=[s.inst];state.fps=24;app.sim3d=s.sim;app.freezeEffectsFrame=undefined;return fn();}
  finally{
    state.scene=previous.scene;state.cam=previous.cam;state.plate=previous.plate;state.instances=previous.instances;state.fps=previous.fps;app.sim3d=previous.sim3d;app.freezeEffectsFrame=previous.freeze;glCanvas.width=previous.w;glCanvas.height=previous.h;
  }
}
function presetFor(kind){
  const source=kind==='fire'?'groundfire':kind==='explosion'?'explosion':['dust_hit','wall_hit','concrete'].includes(kind)?'concrete':['dirt_hit','dirt'].includes(kind)?'dirt':kind==='smoke_plume'||kind==='kfx_black'?'column':'plume';
  const hq=kind==='smoke_plume_hq',grenade=kind==='smoke_grenade'||kind==='smoke_white'||kind==='kfx_white';
  const name=`bang_volume_${kind}`,p=clone(PRESETS[source]);
  // Preserve the authored plume; legacy elements keep their existing recipes.
  if(p.smoke&&!hq){p.smoke.fire=source==='column'?null:p.smoke.fire;p.smoke.volume={...p.smoke.volume,particles:false,cachedDetail:false,densityScale:1};}
  if(grenade)p.defaults.color='#d9dadb';
  if(p.dust){p.dust.densityScale=1;if(p.dust.fines)p.dust.fines={...p.dust.fines,analytic:false,particles:false,n:Math.min(64,p.dust.fines.n??32)};}
  PRESETS[name]=p;return {name,p,source,hq,grenade};
}
function create(effectId,kind,o,width,height,owner){
  initialize();const s=o.matteIds?{...scene!,mattes:scene!.mattes.filter(m=>o.matteIds!.includes(m.id))}:scene!;const {name,p,source,hq,grenade}=presetFor(kind);
  if(hq&&!supportsFineParticles())throw new Error('High-quality plume requires floating-point smoke-particle support on this WebGL2 GPU.');
  const maskedPressure=s.mattes.some(m=>m.kind==='hard'&&m.role!=='holdout'),multigrid=hq&&!maskedPressure;
  const onset=o.onsetSeconds??o.start/s.fps,anchor=sourceFrameAt(s,Math.max(0,onset));
  let x=o.x/Math.max(1,o.w)*s.width,y=o.y/Math.max(1,o.h)*s.height;
  // Existing adapter coordinates may belong to a padded tracked simulation box.
  if(o.bounds){x=(o.x/o.w*o.bounds.width+o.bounds.x)/960*s.width;y=(o.y/o.h*o.bounds.height+o.bounds.y)/540*s.height;}
  if(o.path){x=o.path[anchor*2]/960*s.width;y=o.path[anchor*2+1]/540*s.height;}
  [x,y]=projectH(inverseH(s.camera.homographies[anchor]??IDENTITY3),x,y);
  const scale=Math.max(.1,o.size*(o.bounds?o.bounds.height/540:1)),surface=depthAt(s,x,y);
  // Keep surface-placed emitters in the free cell in front of the depth slab.
  // A fixed24-grid bound makes this placement independent of preview quality.
  const safeOffset=Math.max(.08,s.height*surface/(s.camera.focalY??s.camera.focalPixels)*Math.sqrt(scale)*1.65/24*1.25);
  const distance=emitterDepth(s,anchor,x,y,safeOffset,o.depthOffset??0),position=unproject(s,x,y,distance),unit=s.height*distance/(s.camera.focalY??s.camera.focalPixels);
  const R=unit*Math.sqrt(scale),a=(o.angle??0)*Math.PI/180,windDriven=kind==='explosion'||kind==='fire'||kind.startsWith('smoke_')||kind.startsWith('kfx_');
  const inst={...p.defaults,id:1,preset:name,enabled:true,name:effectId,intensity:1,heat:1,gravityMul:1,speedMul:1,fineGrain:1,full:true,area:.8,sizeMul:1,depth:.7,gust:.6,ridge:20,width:p.defaults.width??.35,length:.9,spreadMul:1,lift:0,
    frame:onset*24,duration:(o.durationSeconds??Math.max(.1,(o.end-o.start+1)/s.fps))*24,scale,seed:o.seed,w:position,wscale:unit,wdir:o.direction??(windDriven?[0,1,0]:[Math.sin(a),Math.cos(a),0]),
    color:o.color??p.defaults.color,facing:p.defaults.facing??0,floor:false,debrisMul:0,sparksMul:0,flash:false,
    ...(hq?{volumeFXShader:volumeShader}:source==='groundfire'?{}:{volumeFXBox:{min:[position[0]-.65*R,position[1]-.45*R,position[2]-.65*R],size:[1.3*R,1.65*R,1.3*R]}})};
  const shading=Math.max(0,Math.min(2,o.shading??1));
  const windVector=o.windVector??[o.wind??0,0,0],windWorld=windVector.map(v=>v*unit),windMagnitude=Math.hypot(...windWorld),tracking=o.tracking??'camera';
  const dispersionEnabled=kind==='explosion'||grenade;
  const dispersionDefault=kind==='explosion'?1:0,requestedDispersion=o.windDispersion??dispersionDefault;
  const windDispersion=dispersionEnabled?Math.max(0,Math.min(2,Number.isFinite(requestedDispersion)?requestedDispersion:dispersionDefault)):0;
  const postHoldoutIds=s.mattes.filter(m=>m.holdoutPost&&m.role!=='collide').map(m=>m.id);
  const c={effectId,kind,inst,hq,grenade,preset:p,options:o,tracking,owner,postHoldoutIds,origin:position,emitterWorld:position,windWorld,windMagnitude,windDispersion,windResponse:windMagnitude>0?(o.windResponse??stockScene.drag):0,quality:o.quality??'high',sourceScene:s,scene:{...stockScene,wind:o.wind??0,multigrid,lightCol:s.lighting?.key??(hq?stockScene.lightCol:'#ddd5c7'),ambCol:s.lighting?.ambient??(hq?stockScene.ambCol:'#69726b'),ambient:Math.min(1,(s.lighting?.intensity??(hq?stockScene.ambient:.45))+.55*(1-shading)),shadow:(s.lighting?.shadow??1)*shading},
    cam:{mode:'3d',data:{frames:new Map([[0,{t:[0,0,0],r:[0,0,0],vfov:null}]]),nums:[0]},rotOrder:'ZXY',fovMode:'lens',focal:1,haperture:s.width/s.camera.focalPixels,vaperture:s.height/(s.camera.focalY??s.camera.focalPixels),
      res:o.resolution??(o.quality==='draft'?(source==='groundfire'?64:32):hq||source==='groundfire'?128:64),groundY:-1000,adaptive:false,multigrid,mc:true,rk2:true,projectAfter:true,substeps:hq?p.smoke.volume.substeps:4,detail:hq?.8:.25,smooth:true,fast:false,sunAz:s.lighting?.azimuth??(hq?120:-25),sunEl:s.lighting?.elevation??(hq?40:60),tune:{jacobi:hq?20:24,curl:.8,turb:1.8,sharp:0}},
    plate:{kind:'bang-volume',w:s.width,h:s.height,first:0,last:Math.ceil((o.n/s.fps)*24)},sim:null,frame:null,
    target:makeTargets(width,height,false),element:canvas(width,height),composite:canvas(width,height),alpha:canvas(width,height),visibility:canvas(width,height),rgb:canvas(width,height)};
  return scoped(c,()=>{
    const base=boxFor(inst),deltaMin=[0,0,0],deltaMax=[0,0,0],duration=inst.duration/24;
    if(tracking!=='camera')for(let i=0;i<=Math.ceil(duration*24);i++){
      const point=emitterAt(c,onset+i/24);for(let a=0;a<3;a++){const d=point[a]-position[a];deltaMin[a]=Math.min(deltaMin[a],d);deltaMax[a]=Math.max(deltaMax[a],d);}
    }
    if(windMagnitude>0){const k=c.windResponse,travel=duration-(1-Math.exp(-k*duration))/k;for(let a=0;a<3;a++){const d=windWorld[a]*travel;deltaMin[a]+=Math.min(0,d);deltaMax[a]+=Math.max(0,d);}}
    if((kind==='explosion'||grenade&&windDispersion>0)&&windMagnitude>0){
      // Explosion comparisons reserve padding even at0. Grenades opt into the
      // wider domain above0, preserving their previous default simulation.
      // Every positive setting shares the same max-dispersion domain.
      const sigma=Math.sqrt(2*.012*2*windMagnitude*unit*Math.max(0,duration-.45));
      for(let a=0;a<3;a++){const pad=1.5*sigma*Math.sqrt(Math.max(0,1-(windWorld[a]/windMagnitude)**2));deltaMin[a]-=pad;deltaMax[a]+=pad;}
    }
    if(tracking!=='camera'||windMagnitude>0)inst.volumeFXBox={min:base.min.map((v,a)=>v+deltaMin[a]),size:base.size.map((v,a)=>v+deltaMax[a]-deltaMin[a])};
    // Widening the domain should not immediately erase the source's small
    // scales. Keep explosion voxel size where practical, bounded at128.
    if(kind==='explosion'&&windMagnitude>0&&!o.resolution)c.cam.res=Math.min(128,Math.ceil(c.cam.res*Math.max(...inst.volumeFXBox.size)/Math.max(...base.size)));
    c.sim=createVolumeSimulator();c.sim.sync();c.v=c.sim.vols.get(1);
    const sync=c.sim.sync.bind(c.sim);c.sim.sync=()=>{sync();for(const volume of c.sim.vols.values())volume.setBudget(48*1024*1024);};c.v.setBudget(48*1024*1024);
    const v=c.v;c.grid={dim:v.dim,tiles:v.tiles,boxMin:v.boxMin,vox:v.vox,aw:v.aw,ah:v.ah};
    c.sourceScene=screenForegroundScene(s,c.grid);
    c.screenHoldoutScene={...c.sourceScene,mattes:c.sourceScene.mattes.filter(m=>m.holdoutScreen)};
    c.volumeHoldoutScene={...c.sourceScene,mattes:c.sourceScene.mattes.filter(m=>!m.holdoutScreen)};
    const cg=s.collisionMode==='matte2d'?[]:s.mattes.filter(m=>m.cameraBox&&m.role!=='collide'&&!m.holdoutPost);
    if(cg.length){
      // Per-pixel CG camera depth keeps holdout edges independent of fluid voxels.
      const values=new Float32Array(s.width*s.height).fill(1e6);
      for(let y=0;y<s.height;y++)for(let x=0;x<s.width;x++)for(const m of cg){
        const hit=cameraBoxInterval(s,m,x,y);if(hit)values[y*s.width+x]=Math.min(values[y*s.width+x],hit[0]+(m.depthOffset??0));
      }
      c.cgDepth=makeTex(s.width,s.height,gl.R32F,gl.RED,gl.FLOAT,gl.NEAREST);
      gl.bindTexture(gl.TEXTURE_2D,c.cgDepth);gl.texSubImage2D(gl.TEXTURE_2D,0,0,0,s.width,s.height,gl.RED,gl.FLOAT,values);
    }
    c.fineMask=makeMaskLevel(c.grid,!s.mattes.length);
    const coarseGrid={dim:v.coarse.dim,tiles:v.coarse.tiles,boxMin:v.boxMin,vox:v.coarse.vox,aw:v.coarse.w,ah:v.coarse.h};
    c.coarseMask=v.vox===v.coarse.vox?c.fineMask:makeMaskLevel(coarseGrid,!s.mattes.length);
    c.uniforms={uVFXEnabled:s.mattes.some(m=>m.kind==='hard'&&m.role!=='holdout')?1:0,uVFXRay:0,uVFXCamera:1,uVFXExactCG:c.cgDepth?1:0,uVFXCGDepth:c.cgDepth??c.fineMask.holdout,uVFXH0:[1,0,0],uVFXH1:[0,1,0],uVFXH2:[0,0,1],uVFXWind:windWorld,uVFXWindK:c.windResponse,
      uVFXWindDispersion:windDispersion,uVFXWindAge:0,uVFXWindUnit:unit,uVFXWindSeed:(o.seed%997)*.031};
    const originalU=v.u.bind(v);v.u=(coarse=false)=>{const level=coarse?c.coarseMask:c.fineMask;return {...originalU(coarse),...c.uniforms,
      uVFXSolid:level.solid,uVFXHoldout:level.holdout,uVFXMotion:level.motion,uVFXMaskDim:level.grid.dim,uVFXMaskTiles:level.grid.tiles,uVFXMaskBoxMin:level.grid.boxMin,uVFXMaskVox:level.grid.vox,
      uVFXWindHasFire:v.hasFire?1:0,uVFXWindHeat:v.hasFire?v.heat.read.tex:v.dye.read.tex,
      uVFXSource:c.emitterWorld.map((q,k)=>(q-v.boxMin[k])/(coarse?v.coarse.vox:v.vox))};};
    const fluid=v.fluid.bind(v);v.fluid=(dt,list,time,sub)=>{updateGeometry(c,sourceFrameAt(s,time));
      c.uniforms.uVFXWindAge=time-onset;
      if(grenade)list=grenadeSplats(c,time,dt,sub);
      c.emitterWorld=emitterAt(c,time);
      if(tracking!=='camera'){
        const previous=emitterAt(c,time-dt),delta=c.emitterWorld.map((v,a)=>v-position[a]),velocity=c.emitterWorld.map((v,a)=>(v-previous[a])/dt);
        list=list.map(row=>({...row,p:row.p.map((v,a)=>v+delta[a]),v:row.v.map((v,a)=>v+velocity[a])}));
      }
      fluid(dt,list,time,sub);mixWindSmoke(c,time-onset,dt);};
    return c;
  });
}
function mixWindSmoke(c,age,dt){
  if((c.kind!=='explosion'&&!c.grenade)||c.windMagnitude<=0||c.windDispersion<=0||age<=.45)return;
  const t=Math.max(0,Math.min(1,(age-.45)/.8)),late=t*t*(3-2*t),v=c.v;
  const diffusion=.012*c.windMagnitude*c.inst.wscale*c.windDispersion*late;
  const coefficients=c.windWorld.map(q=>diffusion*(1-.8*(q/c.windMagnitude)**2));
  const steps=Math.max(1,Math.min(4,Math.ceil(2*coefficients.reduce((a,b)=>a+b,0)*dt/(v.vox*v.vox)/.75)));
  const fields=[v.dye,v.attr,...(v.hasFire?[v.heat]:[])];
  for(let step=0;step<steps;step++){
    const uniforms={...v.u(),uMixDye:v.dye.read.tex,uMixHeat:v.hasFire?v.heat.read.tex:v.dye.read.tex,
      uMixHasFire:v.hasFire?1:0,uMixDt:dt/steps,uMixDiffusion:coefficients,uMixFade:.15*c.windMagnitude/c.inst.wscale*c.windDispersion*late};
    // Keep all baseline fields bound until every output has been evaluated.
    for(const field of fields)pass(volumePrograms.windSmokeMix,field.write,field.read.w,field.read.h,{...uniforms,uMixSource:field.read.tex});
    for(const field of fields)field.swap();
  }
  v.lightFrame=null;v.renderKey=null;if(v.optical)v.optical.key=null;
}
/** Only the source moves; emitted gas remains in the scene and can trail behind.
 * One depth frame cannot recover source Z motion, so retain onset depth. */
function emitterAt(c,time){
  if(c.tracking==='camera')return c.origin;
  const s=c.sourceScene,path=c.options.path,last=Math.max(0,Math.min(c.options.n-1,s.camera.homographies.length-1));
  let lo=Math.min(last,sourceFrameAt(s,Math.max(0,time)));if(lo>0&&sourceTime(s,lo)>time)lo--;
  const hi=Math.min(last,lo+1),t0=sourceTime(s,lo),t1=sourceTime(s,hi),u=hi===lo?0:Math.max(0,Math.min(1,(time-t0)/(t1-t0)));
  const at=(frame)=>{
    let x=c.options.x/c.options.w*s.width,y=c.options.y/c.options.h*s.height;
    if(path?.length>=2*(frame+1)){x=path[frame*2]/960*s.width;y=path[frame*2+1]/540*s.height;}
    [x,y]=projectH(inverseH(s.camera.homographies[frame]??IDENTITY3),x,y);
    return unproject(s,x,y,-c.origin[2]);
  };
  const a=at(lo),b=at(hi);return a.map((value,i)=>value+(b[i]-value)*u);
}
function grenadeSplats(c,time,dt,sub){
  const age=time-c.inst.frame/24;if(age<0||age>c.inst.duration/24)return [];
  const tau=.7,front=(.018+.84*tau*(1-Math.exp(-age/tau))+.12*age)*c.inst.wscale*c.inst.scale;
  const speed=(.84*Math.exp(-age/tau)+.12)*c.inst.wscale*c.inst.scale,col=lin(c.inst.color),list=[];
  for(let j=0;j<48;j++){
    const random=rng(seedMix(c.inst.seed,j+Math.floor(age*8)*2047,719)),z=random()*2-1,angle=random()*Math.PI*2,r=Math.sqrt(1-z*z),fraction=Math.cbrt(random())*.85;
    const dir=[r*Math.cos(angle),z*.62,r*Math.sin(angle)],p=c.inst.w.map((q,k)=>q+dir[k]*front*fraction+(k===1?front*.16:0));
    list.push({p,r:c.inst.wscale*c.inst.scale*.026,v:dir.map(q=>q*speed*fraction),temp:.025,vw:.12,col,d:8*dt*Math.exp(-age/4)*Math.min(1,age/.1)*(32/48),heat:0,curl:c.inst.curl,turb:c.inst.turb,rate:c.inst.dissipate,linger:c.inst.linger});
  }return list;
}
function updateGeometry(c,frame){
  if(c.geometryFrame===frame)return;
  if(c.geometryFrame!=null&&c.sourceScene.mattes.every(m=>m.static||m.coordinates==='reference')){c.geometryFrame=frame;return;}
  for(const level of new Set([c.fineMask,c.coarseMask])){
    const fields=voxelizeScene(c.sourceScene,level.grid,frame);level.fields=fields;
    gl.pixelStorei(gl.UNPACK_ALIGNMENT,1);
    for(const [texture,format,type,data]of [[level.solid,gl.RED,gl.UNSIGNED_BYTE,fields.solid],[level.holdout,gl.RED,gl.UNSIGNED_BYTE,fields.holdout],[level.motion,gl.RGBA,gl.FLOAT,fields.motion]]){
      gl.bindTexture(gl.TEXTURE_2D,texture);gl.texSubImage2D(gl.TEXTURE_2D,0,0,0,level.grid.aw,level.grid.ah,format,type,data);
    }
  }c.fields=c.fineMask.fields;c.maskCounts={solidCells:0,porousCells:0};
  for(let i=0;i<c.fields.solid.length;i++){if(c.fields.solid[i])c.maskCounts.solidCells++;if(c.fields.motion[i*4+3]>.001)c.maskCounts.porousCells++;}
  c.geometryFrame=frame;c.v.lightFrame=null;c.v.renderKey=null;if(c.v.optical)c.v.optical.key=null;
}
function makeMaskLevel(grid,empty){
  if(empty)grid={...grid,dim:[1,1,1],tiles:[1,1],aw:1,ah:1};
  return {grid,solid:makeTex(grid.aw,grid.ah,gl.R8,gl.RED,gl.UNSIGNED_BYTE,gl.NEAREST),holdout:makeTex(grid.aw,grid.ah,gl.R8,gl.RED,gl.UNSIGNED_BYTE,gl.NEAREST),motion:makeTex(grid.aw,grid.ah,gl.RGBA32F,gl.RGBA,gl.FLOAT,gl.NEAREST)};
}
function capture(c,destination,held,frame){
  const s=c.sourceScene,h=inverseH(s.camera.homographies[frame]??IDENTITY3),W=s.width,H=s.height;
  Object.assign(c.uniforms,{uVFXRay:held?1:0,uVFXH0:[h[0],h[1]*H/W,h[2]/W],uVFXH1:[h[3]*W/H,h[4],h[5]/H],uVFXH2:[h[6]*W,h[7]*H,h[8]]});
  gl.bindFramebuffer(gl.FRAMEBUFFER,c.target.S.fbo);gl.viewport(0,0,c.target.w,c.target.h);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT);
  const lit=colour=>lin(colour).map(value=>value*(c.brightness??1));
  c.v.render(c.target,{pos:[0,0,0],right:[1,0,0],up:[0,1,0],back:[0,0,1],tanH:W/2/s.camera.focalPixels,tanV:H/2/(s.camera.focalY??s.camera.focalPixels)},c.sim.sunDir(),{key:lit(c.scene.lightCol).map(value=>value*(c.lightIntensity??1)),sky:lit(c.scene.ambCol).map(value=>value*(c.fillIntensity??1)),amb:c.scene.ambient,fire:lit('#ff8f4d')},c.hq&&c.quality==='high');
  pass(displayProgram,null,c.target.w,c.target.h,{uS:c.target.S.tex,uF:c.target.F.tex});
  const ctx=destination.getContext('2d');ctx.clearRect(0,0,destination.width,destination.height);ctx.drawImage(glCanvas,0,0);
}
export function renderVolumeElement(effectId:string,kind:string,o:VolumeElementOptions,sourceFrame:number,owner='preview'):VolumeElementFrame|null{
  if(!scene)return null;
  const width=Math.max(1,Math.round(o.w)),height=Math.max(1,Math.round(o.h)),key=`${owner}:${effectId}`;
  const {sourceTimeSeconds,path,density,brightness,lightIntensity,fillIntensity,lightRotation,lightElevation,compositeOnly,...stableOptions}=o;
  const recipe=JSON.stringify([scene.key,kind,stableOptions,path?sourcePathHash(path):null]);let c=cache.get(key);
  if(c&&c.recipe!==recipe){destroy(c);cache.delete(key);c=null;}
  if(!c){c=create(effectId,kind,o,width,height,owner);c.recipe=recipe;cache.set(key,c);}
  // Lighting is optical state. Slider drags retain the current fluid solve.
  const az=(c.sourceScene.lighting?.azimuth??(c.hq?120:-25))+(lightRotation??0),el=lightElevation??c.sourceScene.lighting?.elevation??(c.hq?40:60);
  if(c.cam.sunAz!==az||c.cam.sunEl!==el){c.cam.sunAz=az;c.cam.sunEl=el;c.v.lightFrame=null;c.v.renderKey=null;if(c.v.optical)c.v.optical.key=null;c.frame=null;}
  const smoke=kind.startsWith('smoke_')||kind==='kfx_white'||kind==='kfx_black';
  const bounded=value=>Math.max(0,Math.min(4,typeof value==='number'&&Number.isFinite(value)?value:1));
  const keyIntensity=bounded(lightIntensity);
  if(c.lightIntensity!==keyIntensity){c.lightIntensity=keyIntensity;c.frame=null;}
  const fill=Math.min(2,bounded(fillIntensity));
  if(c.fillIntensity!==fill){c.fillIntensity=fill;c.frame=null;}
  const lookDensity=smoke?bounded(density):1,lookBrightness=smoke?bounded(brightness):1;
  const appearanceKey=`${lookDensity}:${lookBrightness}`;
  if(c.appearanceKey!==appearanceKey){
    c.scene.density=stockScene.density*lookDensity;c.density=lookDensity;c.brightness=lookBrightness;c.appearanceKey=appearanceKey;
    // Rerender optics at this frame while retaining all simulated fluid,
    // collision and particle state; slider drags do not replay the solve.
    c.frame=null;
  }
  const seconds=sourceTimeSeconds??sourceTime(scene!,sourceFrame);
  if(c.frame!==sourceFrame||c.sampleTime!==seconds||(!compositeOnly&&!c.outputsComplete))scoped(c,()=>{
    glCanvas.width=width;glCanvas.height=height;
    const simulationFrame=Math.max(-9999,Math.round(seconds*24));c.sim.goto(simulationFrame);c.emitterWorld=emitterAt(c,seconds);updateGeometry(c,sourceFrame);
    const flat=c.sourceScene.collisionMode==='matte2d';
    // The viewer consumes only the composited canvas. A depth scene with native
    // volume holdouts can stay on the GPU and skip two readbacks, three image
    // allocations and the full-canvas RGB/alpha/visibility conversion loop.
    if(compositeOnly&&!flat&&!c.postHoldoutIds.length&&!c.screenHoldoutScene.mattes.length){
      capture(c,c.composite,true,sourceFrame);c.frame=sourceFrame;c.sampleTime=seconds;c.outputsComplete=false;return;
    }
    capture(c,c.element,false,sourceFrame);if(!flat)capture(c,c.composite,true,sourceFrame);
    const raw=c.element.getContext('2d').getImageData(0,0,width,height),held=flat?new ImageData(new Uint8ClampedArray(raw.data),width,height):c.composite.getContext('2d').getImageData(0,0,width,height);
    const alpha=c.alpha.getContext('2d').createImageData(width,height),visibility=c.visibility.getContext('2d').createImageData(width,height),rgb=c.rgb.getContext('2d').createImageData(width,height);
    for(let i=0;i<width*height;i++){
      const k=i*4,a=raw.data[k+3],x=(i%width+.5)/width*scene!.width,y=(Math.floor(i/width)+.5)/height*scene!.height;
      // A2D optical matte is a single screen-space multiply. Applying partial
      // coverage at successive XYZ samples would harden its antialiased edge.
      const base=flat?255*fallbackVisibility(c.sourceScene,sourceFrame,x-.5,y-.5,-c.inst.w[2],'exclude'):a>1?Math.min(255,held.data[k+3]/a*255):255*fallbackVisibility(c.volumeHoldoutScene,sourceFrame,x,y,-c.inst.w[2],'exclude');
      const screen=c.screenHoldoutScene.mattes.length?fallbackVisibility(c.screenHoldoutScene,sourceFrame,x-.5,y-.5,-c.inst.w[2],'exclude'):1;
      // The post matte is never baked into capture visibility. Native output
      // applies it once at source resolution; only live previews use a proxy.
      const post=c.owner==='capture'||!c.postHoldoutIds.length?1:fallbackVisibility(c.sourceScene,sourceFrame,x-.5,y-.5,-c.inst.w[2],'only');
      const visible=base*screen*post;
      if(flat||c.postHoldoutIds.length||c.screenHoldoutScene.mattes.length)held.data[k+3]=a*visible/255;
      for(let j=0;j<3;j++){alpha.data[k+j]=a;visibility.data[k+j]=visible;const v=raw.data[k+j]/255,linear=(v<=.04045?v/12.92:((v+.055)/1.055)**2.4)*a/255;rgb.data[k+j]=255*(linear<=.0031308?12.92*linear:1.055*linear**(1/2.4)-.055);}
      alpha.data[k+3]=visibility.data[k+3]=rgb.data[k+3]=255;
    }
    if(flat||c.postHoldoutIds.length||c.screenHoldoutScene.mattes.length)c.composite.getContext('2d').putImageData(held,0,0);
    c.alpha.getContext('2d').putImageData(alpha,0,0);c.visibility.getContext('2d').putImageData(visibility,0,0);c.rgb.getContext('2d').putImageData(rgb,0,0);c.frame=sourceFrame;c.sampleTime=seconds;c.outputsComplete=true;
  });
  return {element:c.element,composite:c.composite,alpha:c.alpha,visibility:c.visibility,rgb:c.rgb,light:null,shadow:null,metadata:{actual3D:true,depthCalibrated:false,movingBodyDepth:'Stable reference-mask median; Z motion is not recovered from one depth frame.',simulationFPS:24,sourceFPS:scene.fps,sourceTime:seconds,simulationFrame:Math.round(seconds*24),sourceDepth:-c.inst.w[2],guideOccluded:false,collisionMode:scene.collisionMode??'depth3d',finiteDepthColliders:scene.collisionMode!=='matte2d',visibilityMeaning:scene.collisionMode==='matte2d'?'Raw2D matte is an optical holdout at every depth; collision is a straightZ extrusion.':'Native XYZ volume front/back ratio; new generated wisps require approximate depth assignment, never simulation-alpha clipping.',quality:o.quality??'high',grid:c.grid.dim,
    profile:c.hq?'squib-plume-hq-r18-v1':c.grenade?'bang-smoke-grenade-v1':c.kind==='fire'?'squib-groundfire-v1':'legacy-volume',emission:c.grenade?'expanding-grenade':'authored-squib',hasFire:c.v.hasFire,
    flowGrid:c.v.coarse.dim,flowResolution:c.cam.res,densityScale:c.v.coarse.vox/c.v.vox,particleCount:c.v.smokeParticles?.count??0,requestedParticleCount:c.preset.smoke?.volume?.particleCount??0,
    smokeDensity:c.density??1,smokeBrightness:c.brightness??1,lightIntensity:c.lightIntensity??1,fillIntensity:c.fillIntensity??1,lightAzimuth:c.cam.sunAz,lightElevation:c.cam.sunEl,
    particleBlend:c.v.smokeParticles?(c.preset.smoke?.volume?.particleBlend??0)*c.cam.detail:0,renderDetail:c.cam.detail,cachedDetail:c.preset.smoke?.volume?.cachedDetail??false,persistentFine:c.preset.smoke?.volume?.persistentFine??false,
    sourceColour:c.inst.color,substeps:c.cam.substeps,multigrid:c.cam.multigrid,bounds:c.hq?'authored-plume':c.kind==='fire'?'authored-groundfire':'legacy-adapter',rayStep:c.v.coarse.vox/c.v.vox*(c.hq&&c.quality==='high'?.4:.6),
    sourceTracking:c.tracking,sourceWorld:c.emitterWorld.slice(),windWorld:c.windWorld.slice(),windResponse:c.windResponse,windDispersion:c.windDispersion,
    postHoldoutIds:c.postHoldoutIds,screenHoldoutIds:c.screenHoldoutScene.mattes.map(m=>m.id),excludedPostHoldoutIds:c.owner==='capture'?c.postHoldoutIds:[],
    postHoldoutVisibility:c.postHoldoutIds.length?(c.owner==='capture'?'Excluded; apply approved alpha once in the native compositor.':c.sourceScene.collisionMode==='matte2d'?'Screen-space optical proxy; native output uses full-resolution alpha.':'Approximate source-depth plane gate for optical preview; no per-pixel elementZ is recovered.'):'none',
    colliderBlur:safeColliderBlurMetadata(c.sourceScene),collisionMask:c.maskCounts,debris:c.v.parts.debris.length,sparks:c.v.parts.sparks.length}};
}
function safeColliderBlurMetadata(s){return s.mattes.filter(m=>m.role!=='holdout').map(m=>({id:m.id,sigmaLogicalPixels:m.colliderBlur??0,hardCoreThreshold:(m.colliderBlur??0)>0?.85:.5}));}
const sourcePathHashes=new WeakMap<Float32Array,string>();
function sourcePathHash(path:Float32Array){let value=sourcePathHashes.get(path);if(value)return value;let hash=2166136261;for(const word of new Uint32Array(path.buffer,path.byteOffset,path.length)){hash=Math.imul(hash^word,16777619);}value=`${path.length}:${hash>>>0}`;sourcePathHashes.set(path,value);return value;}
/** Capture consumers must await their blob/file callback before the next frame,
 * because a per-element cache reuses its canvases. Yield between GPU frames. */
export async function captureVolumeGuides(effectId:string,kind:string,options:VolumeElementOptions,sourceFrames:number[],
  receive:(frame:number,result:VolumeElementFrame)=>void|Promise<void>,signal?:AbortSignal,owner='capture'){
  for(const frame of sourceFrames){
    if(signal?.aborted)throw new DOMException('Capture cancelled','AbortError');
    await new Promise<void>(resolve=>requestAnimationFrame(()=>resolve()));
    const result=renderVolumeElement(effectId,kind,options,frame,owner);
    if(!result)throw new Error('Load a depth scene before capturing volume guides.');
    await receive(frame,result);
  }
}
