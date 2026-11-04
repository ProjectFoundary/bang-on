/** Approximate monocular camera geometry. Distances are scene units, not metres. */
export type Mat3 = number[];
export type DepthReference = { width: number; height: number; values: Float32Array | number[]; kind: 'inverse' | 'distance'; scale: number; bias: number };
export type MatteFrames = Uint8Array[] | Map<number, Uint8Array>;
export type VolumeMatte = { id: string; width: number; height: number; frames: MatteFrames;
  holdoutFrames?: MatteFrames; holdoutWidth?: number; holdoutHeight?: number; holdoutPost?: boolean;
  holdoutCoordinates?: 'reference'|'source'; holdoutDepth?: number; holdoutScreen?:boolean;
  role: 'collide' | 'holdout' | 'both'; kind: 'hard' | 'soft'; thickness: number; depthOffset?: number; depth?: number;
  coordinates?: 'reference' | 'source'; static?: boolean; drag?: number; softRadius?: number; colliderBlur?: number; colliderDisplacement?:number;
  cameraBox?:{center:number[];axes:number[][];halfSize:number[]} };
export type VolumeFXScene = { key: string; width: number; height: number; referenceFrame: number; fps: number; sourceTimes?: number[];
  collisionMode?: 'matte2d'|'depth3d';
  depth: DepthReference; camera: { focalPixels: number; focalY?: number; homographies: Mat3[] }; mattes: VolumeMatte[];
  lighting?: { key?: string; ambient?: string; intensity?: number; shadow?: number; azimuth?: number; elevation?: number } };
export type VolumeGrid = { dim: number[]; tiles: number[]; boxMin: number[]; vox: number; aw: number; ah: number };
export const IDENTITY3 = [1, 0, 0, 0, 1, 0, 0, 0, 1];
export function projectH(h: Mat3, x: number, y: number): [number, number] {
  const d = h[6] * x + h[7] * y + h[8];
  return Math.abs(d) > 1e-12 ? [(h[0] * x + h[1] * y + h[2]) / d, (h[3] * x + h[4] * y + h[5]) / d] : [-1e9, -1e9];
}
export function inverseH(h: Mat3): Mat3 {
  if (h.length !== 9 || h.some(v => !Number.isFinite(v))) throw new Error('Invalid camera homography.');
  const [a,b,c,d,e,f,g,j,k] = h, A=e*k-f*j,B=c*j-b*k,C=b*f-c*e,D=f*g-d*k,E=a*k-c*g,F=c*d-a*f,G=d*j-e*g,H=b*g-a*j,I=a*e-b*d;
  const determinant=a*A+b*D+c*G;
  if (Math.abs(determinant)<1e-12) throw new Error('Singular camera homography.');
  return [A,B,C,D,E,F,G,H,I].map(v=>v/determinant);
}
export function sample(values: ArrayLike<number>, w: number, h: number, x: number, y: number): number {
  if (x<0 || y<0 || x>w-1 || y>h-1) return 0;
  const a=Math.floor(x),b=Math.floor(y),c=Math.min(w-1,a+1),d=Math.min(h-1,b+1),u=x-a,v=y-b;
  return (values[b*w+a]*(1-u)+values[b*w+c]*u)*(1-v)+(values[d*w+a]*(1-u)+values[d*w+c]*u)*v;
}
export function depthAt(scene: VolumeFXScene, x: number, y: number): number {
  const d=scene.depth, value=sample(d.values,d.width,d.height,Math.max(0,Math.min(d.width-1,(x+.5)*d.width/scene.width-.5)),Math.max(0,Math.min(d.height-1,(y+.5)*d.height/scene.height-.5)));
  const result=d.kind==='inverse'?d.scale/Math.max(.025,value+d.bias):d.scale*value+d.bias;
  return Number.isFinite(result)&&result>.01?result:1;
}
export function unproject(scene: VolumeFXScene, x: number, y: number, depth=depthAt(scene,x,y)): number[] {
  return [(x-scene.width/2)*depth/scene.camera.focalPixels,(scene.height/2-y)*depth/(scene.camera.focalY??scene.camera.focalPixels),-depth];
}
export function project(scene: VolumeFXScene,p: number[]): [number,number] {
  return [scene.width/2+scene.camera.focalPixels*p[0]/-p[2],scene.height/2-(scene.camera.focalY??scene.camera.focalPixels)*p[1]/-p[2]];
}
export function sourceTime(scene: VolumeFXScene, frame: number): number { return scene.sourceTimes?.[frame]??frame/scene.fps; }
export function sourceFrameAt(scene: VolumeFXScene, seconds: number): number {
  const times=scene.sourceTimes;
  if (!times?.length) return Math.max(0,Math.round(seconds*scene.fps));
  let lo=0,hi=times.length-1;
  while(lo<hi){const mid=Math.ceil((lo+hi)/2);if(times[mid]<=seconds)lo=mid;else hi=mid-1;}
  return lo+1<times.length&&times[lo+1]-seconds<seconds-times[lo]?lo+1:lo;
}
export function matteFrame(m: VolumeMatte, frame: number): Uint8Array {
  if (m.frames instanceof Map) {
    let selected: Uint8Array|undefined,selectedFrame=-Infinity;
    for(const [f,data] of m.frames) if(f<=frame&&f>selectedFrame){selected=data;selectedFrame=f;}
    selected??=m.frames.values().next().value;
    if(!selected)throw new Error(`Empty matte ${m.id}`);
    return selected;
  }
  const selected=m.frames[m.static?0:Math.max(0,Math.min(m.frames.length-1,frame))];
  if(!selected)throw new Error(`Empty matte ${m.id}`);
  return selected;
}
export function matteCoverage(scene:VolumeFXScene,m:VolumeMatte,frame:number,x:number,y:number):number {
  if(m.coordinates!=='reference'&&!m.static)[x,y]=projectH(scene.camera.homographies[frame]??IDENTITY3,x,y);
  return sample(matteFrame(m,frame),m.width,m.height,(x+.5)*m.width/scene.width-.5,(y+.5)*m.height/scene.height-.5)/255;
}
/** Optical alpha has its own dimensions and exact native-frame index. Missing
 * preview frames fall back to raw tracking; an old HQ frame is never frozen
 * across a gap or extended past the end of its coverage. */
export function holdoutCoverage(scene:VolumeFXScene,m:VolumeMatte,frame:number,x:number,y:number):number {
  const reference=m.holdoutCoordinates==='reference',index=reference?0:frame,frames=m.holdoutFrames;
  const value=frames instanceof Map?frames.get(index):frames?.[index];
  if(!value)return matteCoverage(scene,m,frame,x,y);
  if(!reference)[x,y]=projectH(scene.camera.homographies[frame]??IDENTITY3,x,y);
  const width=m.holdoutWidth??m.width,height=m.holdoutHeight??m.height;
  return sample(value,width,height,(x+.5)*width/scene.width-.5,(y+.5)*height/scene.height-.5)/255;
}
const movingDepthCache=new WeakMap<VolumeFXScene,WeakMap<VolumeMatte,number>>();
/** A single reference depth cannot recover a moving body's Z motion. Keep its
 * reference-mask median depth stable instead of picking up the background as
 * the body moves laterally. Explicit depth/depthOffset remain adjustable. */
export function matteDepth(scene:VolumeFXScene,m:VolumeMatte,x:number,y:number):number {
  if(m.cameraBox){const interval=cameraBoxInterval(scene,m,x,y);if(interval)return interval[0];}
  if(m.depth!==undefined)return m.depth;
  if(m.static||m.coordinates==='reference')return depthAt(scene,x,y);
  let cache=movingDepthCache.get(scene);if(!cache){cache=new WeakMap();movingDepthCache.set(scene,cache);}
  let median=cache.get(m);if(median!==undefined)return median;
  const values:number[]=[],step=Math.max(1,Math.ceil(Math.sqrt(scene.width*scene.height/8192)));
  for(let v=step/2;v<scene.height;v+=step)for(let u=step/2;u<scene.width;u+=step)
    if(matteCoverage(scene,m,scene.referenceFrame,u,v)>.7)values.push(depthAt(scene,u,v));
  values.sort((a,b)=>a-b);median=values.length?values[Math.floor(values.length/2)]:depthAt(scene,scene.width/2,scene.height/2);
  cache.set(m,median);return median;
}
/** Exact camera-ray entry/exit for an authored static rectangular CG solid. */
export function cameraBoxInterval(scene:VolumeFXScene,m:VolumeMatte,x:number,y:number):[number,number]|null {
  const box=m.cameraBox;if(!box)return null;
  const ray=[(x-scene.width/2)/scene.camera.focalPixels,(scene.height/2-y)/(scene.camera.focalY??scene.camera.focalPixels),-1];
  let near=0,far=1e9;
  for(let i=0;i<3;i++){
    const axis=box.axes[i],origin=-axis.reduce((sum,q,k)=>sum+q*box.center[k],0),direction=axis.reduce((sum,q,k)=>sum+q*ray[k],0),half=box.halfSize[i];
    if(Math.abs(direction)<1e-9){if(Math.abs(origin)>half)return null;continue;}
    const a=(-half-origin)/direction,b=(half-origin)/direction;near=Math.max(near,Math.min(a,b));far=Math.min(far,Math.max(a,b));if(far<near)return null;
  }return far>0?[near,far]:null;
}
/** Default surface placement clears a finite solid cell before the user adds
 * an offset. Positive FX offsets move toward the camera; no collision is ignored. */
export function emitterDepth(scene:VolumeFXScene,frame:number,x:number,y:number,clearance:number,offset=0):number {
  // An infinite-Z2D obstacle cannot be cleared by moving a source in depth.
  // Keep the source and physical matte on exactly the same reference XY plane.
  if(scene.collisionMode==='matte2d')return depthAt(scene,scene.width/2,scene.height/2);
  let depth=depthAt(scene,x,y)-clearance;
  for(const m of scene.mattes)if(m.kind==='hard'&&m.role!=='holdout'&&matteCoverage(scene,m,frame,x,y)>.5){
    const front=matteDepth(scene,m,x,y)+(m.depthOffset??0);
    if(depth>=front-clearance*.5&&depth<=front+m.thickness+clearance*.5)depth=Math.min(depth,front-clearance);
  }
  return Math.max(.1,depth-offset);
}
/** Depth-plane fallback for generated wisps outside the original simulated support.
 * Generated 2D RGB/alpha has no recovered per-pixel Z; use the effect's source
 * depth there, rather than clipping its alpha to the old simulation footprint. */
export function fallbackVisibility(scene:VolumeFXScene,frame:number,x:number,y:number,effectDepth:number,postMode:'all'|'exclude'|'only'='all'):number {
  if(!scene.mattes.length)return 1;
  [x,y]=projectH(inverseH(scene.camera.homographies[frame]??IDENTITY3),x,y);
  let visible=1;
  for(const m of scene.mattes)if(m.role!=='collide'){
    if(postMode==='exclude'&&m.holdoutPost||postMode==='only'&&!m.holdoutPost)continue;
    const front=(m.holdoutPost?(m.holdoutDepth??matteDepth(scene,m,x,y)):matteDepth(scene,m,x,y))+(m.depthOffset??0);
    if(scene.collisionMode==='matte2d'||front<effectDepth)visible*=1-holdoutCoverage(scene,m,frame,x,y);
  }
  return Math.max(0,Math.min(1,visible));
}
export function gridIndex(g:VolumeGrid,x:number,y:number,z:number):number{return (Math.floor(z/g.tiles[0])*g.dim[1]+y)*g.aw+(z%g.tiles[0])*g.dim[0]+x;}

/** Fully foreground objects need source-pixel optical edges, not the solver's
 * coarse voxel silhouette. This changes optical sampling only; the same matte
 * remains available to the finite physical collider below. */
export function screenForegroundScene(scene:VolumeFXScene,g:VolumeGrid):VolumeFXScene{
  if(scene.collisionMode==='matte2d')return scene;
  const nearest=-(g.boxMin[2]+g.dim[2]*g.vox);
  if(nearest<=0)return scene;
  return {...scene,mattes:scene.mattes.map(m=>{
    if(m.holdoutPost||m.role==='collide'||m.cameraBox)return m;
    const pixels=matteFrame(m,scene.referenceFrame);let found=false,foreground=true;
    for(let y=0;y<m.height&&foreground;y++)for(let x=0;x<m.width;x++)if(pixels[y*m.width+x]){
      found=true;const u=(x+.5)*scene.width/m.width-.5,v=(y+.5)*scene.height/m.height-.5;
      if(matteDepth(scene,m,u,v)+(m.depthOffset??0)+m.thickness>=nearest){foreground=false;break;}
    }
    return found&&foreground?{...m,holdoutScreen:true}:m;
  })};
}
const planeCache=new WeakMap<VolumeFXScene,Map<string,{width:number;height:number;values:Float32Array;depths:Float32Array}>>();
/** Three box passes approximate a Gaussian with the requested sigma in proxy
 * pixels. Unlike the former softRadius path, sigma0 is a true no-op. */
export function blurPlane(input:Float32Array,width:number,height:number,sigma:number){
  if(sigma<=0)return input;
  const ideal=Math.sqrt(4*sigma*sigma+1),low=Math.max(1,Math.floor(ideal)%2?Math.floor(ideal):Math.floor(ideal)-1),high=low+2;
  const narrow=Math.max(0,Math.min(3,Math.round((12*sigma*sigma-3*low*low-12*low-9)/(-4*low-4))));
  let values=input;
  for(let pass=0;pass<3;pass++){
    const radius=((pass<narrow?low:high)-1)/2;if(!radius)continue;
    for(const horizontal of [true,false]){
      const out=new Float32Array(values.length),length=horizontal?width:height,lines=horizontal?height:width;
      for(let row=0;row<lines;row++){
        const at=(n:number)=>horizontal?row*width+n:n*width+row;let sum=0;
        for(let k=-radius;k<=radius;k++)sum+=values[at(Math.max(0,Math.min(length-1,k)))];
        for(let n=0;n<length;n++){out[at(n)]=sum/(2*radius+1);sum+=values[at(Math.min(length-1,n+radius+1))]-values[at(Math.max(0,n-radius))];}
      }values=out;
    }
  }return values;
}
/** Separable square mask offset at proxy resolution, O(pixels), with subpixel
 * interpolation. The selected foreground sample carries its own depth outward. */
export function offsetColliderPlane(input:Float32Array,depth:Float32Array,w:number,h:number,amount:number){
  const expand=amount>0,r=Math.abs(amount),low=Math.floor(r),fraction=r-low;
  const morph=(radius:number)=>{
    let values=input,weightedDepth=depth;
    if(!radius)return {values,weightedDepth};
    for(const horizontal of [true,false]){
      const next=new Float32Array(values.length),nextDepth=new Float32Array(values.length),length=horizontal?w:h,lines=horizontal?h:w;
      for(let row=0;row<lines;row++){
        const at=(n:number)=>horizontal?row*w+n:n*w+row,queue=new Int32Array(length);let head=0,tail=0,added=-1;
        for(let n=0;n<length;n++){
          const end=Math.min(length-1,n+radius);
          while(added<end){const k=++added,v=values[at(k)];while(tail>head&&(expand?values[at(queue[tail-1])]<=v:values[at(queue[tail-1])]>=v))tail--;queue[tail++]=k;}
          while(tail>head&&queue[head]<n-radius)head++;
          const source=at(queue[head]),dest=at(n);next[dest]=values[source];
          nextDepth[dest]=next[dest]*(input[dest]>.00001?depth[dest]/input[dest]:values[source]>.00001?weightedDepth[source]/values[source]:0);
        }
      }values=next;weightedDepth=nextDepth;
    }return {values,weightedDepth};
  };
  const a=morph(low);if(!fraction)return a;const b=morph(low+1),values=new Float32Array(input.length),weightedDepth=new Float32Array(input.length);
  for(let i=0;i<input.length;i++){values[i]=a.values[i]*(1-fraction)+b.values[i]*fraction;weightedDepth[i]=a.weightedDepth[i]*(1-fraction)+b.weightedDepth[i]*fraction;}
  return {values,weightedDepth};
}
export function colliderPlane(scene:VolumeFXScene,m:VolumeMatte,frame:number){
  let cache=planeCache.get(scene);if(!cache){cache=new Map();planeCache.set(scene,cache);}
  const key=`${m.id}:${m.static?0:frame}:${m.colliderBlur??0}:${m.colliderDisplacement??0}:${m.depth??''}`,old=cache.get(key);if(old)return old;
  const width=Math.min(256,m.width),height=Math.max(8,Math.round(width*scene.height/scene.width));
  let values:Float32Array=new Float32Array(width*height),weightedDepth:Float32Array=new Float32Array(values.length);
  for(let y=0;y<height;y++)for(let x=0;x<width;x++){
    const u=(x+.5)*scene.width/width-.5,v=(y+.5)*scene.height/height-.5,index=y*width+x;
    values[index]=matteCoverage(scene,m,frame,u,v);weightedDepth[index]=values[index]*matteDepth(scene,m,u,v);
  }
  if(m.colliderDisplacement){const offset=offsetColliderPlane(values,weightedDepth,width,height,m.colliderDisplacement*width/960);values=offset.values;weightedDepth=offset.weightedDepth;}
  const sigma=(m.colliderBlur??0)*width/960;
  values=blurPlane(values,width,height,sigma);weightedDepth=blurPlane(weightedDepth,width,height,sigma);
  // Extend the object's own depth into the soft fringe, instead of sampling
  // unrelated background depth just outside its silhouette.
  const depths=new Float32Array(values.length);
  for(let i=0;i<depths.length;i++)depths[i]=values[i]>.00001?weightedDepth[i]/values[i]:0;
  const result={width,height,values,depths};cache.set(key,result);while(cache.size>Math.max(4,scene.mattes.length*4))cache.delete(cache.keys().next().value!);return result;
}
export function validateVolumeScene(s:VolumeFXScene) {
  if(s.collisionMode!==undefined&&!['matte2d','depth3d'].includes(s.collisionMode))throw new Error('Invalid collision mode.');
  if(!(s.width>0&&s.height>0&&s.fps>0&&s.camera.focalPixels>0))throw new Error('Invalid volume scene dimensions or camera.');
  if(s.depth.values.length!==s.depth.width*s.depth.height)throw new Error('Depth dimensions do not match its values.');
  if(!Number.isFinite(s.depth.scale)||s.depth.scale<=0||!Number.isFinite(s.depth.bias))throw new Error('Invalid depth mapping.');
  if(s.sourceTimes?.some((t,i)=>!Number.isFinite(t)||(i>0&&t<=s.sourceTimes![i-1])))throw new Error('Source timestamps must increase.');
  for(const m of s.mattes){
    if(!(m.width>0&&m.height>0&&m.thickness>0))throw new Error(`Invalid matte geometry ${m.id}`);
    if(!Number.isFinite(m.colliderDisplacement??0)||Math.abs(m.colliderDisplacement??0)>128)throw new Error(`Invalid collider displacement ${m.id}`);
    if(m.cameraBox){const b=m.cameraBox;if(!m.static||b.center.length!==3||b.halfSize.length!==3||b.axes.length!==3||b.axes.some(a=>a.length!==3)||[...b.center,...b.halfSize,...b.axes.flat()].some(v=>!Number.isFinite(v))||b.halfSize.some(v=>v<=0))throw new Error(`Invalid CG box ${m.id}`);}
    if(!Number.isFinite(m.colliderBlur??0)||(m.colliderBlur??0)<0||(m.colliderBlur??0)>128)throw new Error(`Invalid collider blur ${m.id}`);
    const masks=m.frames instanceof Map?[...m.frames.values()]:m.frames;
    if(!masks.length||masks.some(mask=>mask.length!==m.width*m.height))throw new Error(`Invalid matte pixels ${m.id}`);
    if(m.holdoutFrames){
      const width=m.holdoutWidth??m.width,height=m.holdoutHeight??m.height,frames=m.holdoutFrames instanceof Map?[...m.holdoutFrames.values()]:m.holdoutFrames;
      if(!(Number.isInteger(width)&&width>0&&Number.isInteger(height)&&height>0)||frames.some(mask=>mask.length!==width*height))throw new Error(`Invalid optical matte pixels ${m.id}`);
    }
  }
}

/** Rasterise finite collision surfaces and camera-depth optical occlusion.
 * A foreground surface occludes everything behind it, including a solver box
 * that starts behind the surface. Only the physical collider has thickness. */
export function voxelizeScene(scene:VolumeFXScene,g:VolumeGrid,frame:number) {
  const solid=new Uint8Array(g.aw*g.ah),holdout=new Uint8Array(solid.length),motion=new Float32Array(solid.length*4);
  const prior=Math.max(0,frame-1),dt=Math.max(1/120,sourceTime(scene,frame)-sourceTime(scene,prior));
  const [nx,ny,nz]=g.dim,flat=scene.collisionMode==='matte2d',flatDepth=depthAt(scene,scene.width/2,scene.height/2);
  const exact=flat?[]:scene.mattes.filter(m=>m.cameraBox&&m.kind==='hard'&&m.role!=='holdout'&&!m.depthOffset&&!m.colliderBlur&&!m.colliderDisplacement);
  const boxes=exact.map(m=>{
    const box=m.cameraBox!;
    return box.axes.map((axis,i)=>({axis,offset:axis.reduce((sum,q,k)=>sum+q*(g.boxMin[k]+g.vox*.5-box.center[k]),0),
      // A voxel whose centre misses a thin slab can still overlap it. Use
      // the voxel's extent along each box axis to keep the roof continuous.
      bound:box.halfSize[i]+g.vox*.5*axis.reduce((sum,q)=>sum+Math.abs(q),0)}));
  });
  const planes=scene.mattes.filter(m=>!exact.includes(m)&&!(m.cameraBox&&!flat&&m.role==='holdout'))
    .map(m=>({m,current:colliderPlane(scene,m,frame),previous:colliderPlane(scene,m,prior)}));
  for(let z=0;z<nz;z++)for(let y=0;y<ny;y++)for(let x=0;x<nx;x++){
    const index=gridIndex(g,x,y,z);
    if(boxes.some(box=>box.every(({axis,offset,bound})=>Math.abs(offset+g.vox*(x*axis[0]+y*axis[1]+z*axis[2]))<=bound)))solid[index]=255;
    if(!planes.length)continue;
    const p=g.boxMin.map((q,a)=>q+([x,y,z][a]+.5)*g.vox),distance=-p[2];
    if(distance<=.01)continue;
    const [opticalU,opticalV]=project(scene,p),[u,v]=flat?project(scene,[p[0],p[1],-flatDepth]):[opticalU,opticalV];
    for(const {m,current,previous:previousPlane} of planes){
      const cover=(plane:typeof current,x:number,y:number)=>sample(plane.values,plane.width,plane.height,(x+.5)*plane.width/scene.width-.5,(y+.5)*plane.height/scene.height-.5);
      const optical=m.holdoutPost||m.holdoutScreen?0:holdoutCoverage(scene,m,frame,opticalU,opticalV),rawFront=matteDepth(scene,m,opticalU,opticalV)+(m.depthOffset??0);
      // Collision softness never changes the user's optical holdout matte.
      if(!m.holdoutPost&&!m.holdoutScreen&&(!m.cameraBox||flat)&&(flat||distance>=rawFront)&&m.role!=='collide')holdout[index]=Math.max(holdout[index],Math.round(optical*255));
      if(m.role==='holdout')continue;
      const coverage=cover(current,u,v);if(coverage<.002)continue;
      const blurredDepth=sample(current.depths,current.width,current.height,(u+.5)*current.width/scene.width-.5,(v+.5)*current.height/scene.height-.5);
      const interval=!flat&&m.cameraBox?cameraBoxInterval(scene,m,u,v):null;
      const front=(((m.colliderBlur??0)>0||(m.colliderDisplacement??0)!==0)&&blurredDepth>0?blurredDepth:matteDepth(scene,m,u,v))+(m.depthOffset??0);
      const thickness=interval?Math.max(.001,interval[1]-interval[0]):m.thickness,inside=flat||distance>=front-.5*g.vox&&distance<=front+thickness;
      const solidThreshold=(m.colliderBlur??0)>0?.85:.5;
      const hardCore=m.kind==='hard'&&inside&&coverage>solidThreshold;
      if(hardCore)solid[index]=255;
      // Porous body drag occupies a finite depth band. A local optical-flow
      // normal estimate transports movement in camera XY, not camera motion.
      const radius=Math.max(1,m.softRadius??16)*scene.width/960;
      const previous=cover(previousPlane,u,v),gx=(cover(current,u+radius,v)-cover(current,u-radius,v))/(2*radius);
      const gy=(cover(current,u,v+radius)-cover(current,u,v-radius))/(2*radius);
      const temporal=(coverage-previous)/dt,denom=gx*gx+gy*gy+.00004;
      const vx=Math.max(-scene.width,Math.min(scene.width,-temporal*gx/denom));
      const vy=Math.max(-scene.height,Math.min(scene.height,-temporal*gy/denom));
      if(inside&&m.kind==='hard'){
        motion[index*4]=vx*(flat?flatDepth:distance)/scene.camera.focalPixels/g.vox;
        motion[index*4+1]=-vy*(flat?flatDepth:distance)/(scene.camera.focalY??scene.camera.focalPixels)/g.vox;
      }
      if(m.kind==='soft'||((m.colliderBlur??0)>0&&!hardCore)){
        const sigma=Math.max(g.vox,thickness*.65),delta=distance-front-thickness*.5;
        if(!flat&&Math.abs(delta)>3*sigma)continue;
        const depthWeight=flat?1:Math.exp(-.5*(delta/sigma)**2),weight=coverage*depthWeight;
        const drag=(m.drag??24)*weight;
        if(drag>motion[index*4+3]){
          motion[index*4]=vx*(flat?flatDepth:distance)/scene.camera.focalPixels/g.vox;
          motion[index*4+1]=-vy*(flat?flatDepth:distance)/(scene.camera.focalY??scene.camera.focalPixels)/g.vox;
          motion[index*4+3]=drag;
        }
      }
    }
  }
  return {solid,holdout,motion};
}
