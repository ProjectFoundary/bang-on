import type { Project } from './api';

/** Top-down, screen-space masks. Load the whole sequence before simulating or scrubbing. */
export type SoftCollider = { sigma: number; drag: number }; // sigma in the 960x540 logical plate; drag per second
export type ColliderSequence = { key: string; width: number; height: number; data: Uint8Array[]; soft?: SoftCollider };
export type SimBounds = { x: number; y: number; width: number; height: number };
const pending = new Map<string, Promise<ColliderSequence>>();
export const COLLIDER_VERSION = 'squib-moving-wall-v2';

/** Opt-in porous motion influence. The tracked source matte itself stays intact. */
export function softCollider(sequence: ColliderSequence, soft: SoftCollider): ColliderSequence {
  if (!(soft.sigma > 0) || !(soft.drag > 0) || !Number.isFinite(soft.sigma+soft.drag))
    throw new Error('Soft collider requires positive finite blur and drag.');
  return { ...sequence, key: JSON.stringify([sequence.key,'soft-motion-v1',soft.sigma,soft.drag]), soft: { ...soft } };
}

function gaussian(src: ArrayLike<number>, w: number, h: number, sx: number, sy: number) {
  // Three variance-matched box passes approximate a Gaussian in linear time;
  // full-shot mattes and per-frame signed velocities need the same kernel.
  const radii=(sigma:number)=>{
    let low=Math.floor(Math.sqrt(4*sigma*sigma+1));if(low%2===0)low--;
    low=Math.max(1,low);
    const count=Math.max(0,Math.min(3,Math.round((12*sigma*sigma-3*low*low-12*low-9)/(-4*low-4))));
    return Array.from({length:3},(_,i)=>(low+(i<count?0:2)-1)/2);
  };
  const box=(input:Float32Array,r:number,horizontal:boolean)=>{
    if(!r)return input;
    const output=new Float32Array(w*h),length=horizontal?w:h,lines=horizontal?h:w;
    for(let line=0;line<lines;line++){
      const at=(j:number)=>horizontal?line*w+j:j*w+line;
      let sum=0;for(let k=-r;k<=r;k++)sum+=input[at(Math.max(0,Math.min(length-1,k)))];
      for(let j=0;j<length;j++){
        output[at(j)]=sum/(2*r+1);
        sum+=input[at(Math.min(length-1,j+r+1))]-input[at(Math.max(0,j-r))];
      }
    }
    return output;
  };
  let out: Float32Array=Float32Array.from(src);
  for(const r of radii(sx))out=box(out,r,true);
  for(const r of radii(sy))out=box(out,r,false);
  return out;
}

/** Spread contour-normal movement through the feathered body influence. */
function softWallMotion(data: Map<number,Uint8Array>, w:number,h:number,sx:number,sy:number) {
  const hardMotion=wallMotion(data,w,h),cache=new Map<string,Float32Array>();
  return (f:number,fps:number)=>{
    const key=`${f}:${fps}`,old=cache.get(key);if(old)return old;
    const velocity=new Float32Array(w*h*2);
    if(data.has(f-1)){
      const field=signedDistance(data.get(f)!,w,h),previous=signedDistance(data.get(f-1)!,w,h);
      if(field.occupied && previous.occupied){
        const source=hardMotion(f,fps),weight=new Float32Array(w*h),vx=new Float32Array(w*h),vy=new Float32Array(w*h);
        for(let i=0;i<weight.length;i++)if(Math.abs(field.field[i])<=4){weight[i]=1;vx[i]=source[2*i];vy[i]=source[2*i+1];}
        const norm=gaussian(weight,w,h,sx,sy),x=gaussian(vx,w,h,sx,sy),y=gaussian(vy,w,h,sx,sy);
        for(let i=0;i<weight.length;i++)if(norm[i]>.002){velocity[2*i]=x[i]/norm[i];velocity[2*i+1]=y[i]/norm[i];}
      }
    }
    cache.set(key,velocity);while(cache.size>2)cache.delete(cache.keys().next().value!);
    return velocity;
  };
}

/** Chamfer distance in simulation cells, positive inside a solid and negative outside. */
function signedDistance(mask: Uint8Array, w: number, h: number) {
  let occupied = 0;
  for (const value of mask) if (value > 127) occupied++;
  const distance = (inside: boolean) => {
    const d = new Float32Array(mask.length), far = w+h, diagonal = Math.SQRT2;
    for (let i=0;i<d.length;i++) d[i] = (mask[i]>127)===inside ? 0 : far;
    for (let y=0;y<h;y++) for (let x=0;x<w;x++) {
      const i=y*w+x;
      if(x) d[i]=Math.min(d[i],d[i-1]+1);
      if(y) {
        d[i]=Math.min(d[i],d[i-w]+1);
        if(x)d[i]=Math.min(d[i],d[i-w-1]+diagonal);
        if(x+1<w)d[i]=Math.min(d[i],d[i-w+1]+diagonal);
      }
    }
    for(let y=h-1;y>=0;y--)for(let x=w-1;x>=0;x--){
      const i=y*w+x;
      if(x+1<w)d[i]=Math.min(d[i],d[i+1]+1);
      if(y+1<h){
        d[i]=Math.min(d[i],d[i+w]+1);
        if(x)d[i]=Math.min(d[i],d[i+w-1]+diagonal);
        if(x+1<w)d[i]=Math.min(d[i],d[i+w+1]+diagonal);
      }
    }
    return d;
  };
  const toSolid=distance(true),toEmpty=distance(false);
  for(let i=0;i<toSolid.length;i++)toSolid[i]=mask[i]>127 ? toEmpty[i]-.5 : .5-toSolid[i];
  return { field:toSolid, occupied };
}

/** Normal wall velocity from contour motion. Tangential motion is left free-slip. */
function wallMotion(data: Map<number,Uint8Array>, w:number,h:number) {
  const fields=new Map<number,ReturnType<typeof signedDistance>>(),velocities=new Map<string,Float32Array>();
  const sdf=(f:number)=>{
    let field=fields.get(f);
    if(!field){field=signedDistance(data.get(f)!,w,h);fields.set(f,field);}
    while(fields.size>3)fields.delete(fields.keys().next().value!);
    return field;
  };
  return (f:number,fps:number)=>{
    const key=`${f}:${fps}`,cached=velocities.get(key);if(cached)return cached;
    const velocity=new Float32Array(w*h*2);
    if(data.has(f-1)){
      const current=sdf(f),previous=sdf(f-1);
      if(current.occupied && previous.occupied){
        const a=current.field,b=previous.field,maxSpeed=2*Math.max(w,h);
        for(let y=0;y<h;y++)for(let x=0;x<w;x++){
          const i=y*w+x;if(Math.abs(a[i])>4)continue;
          const gx=(a[y*w+Math.min(w-1,x+1)]-a[y*w+Math.max(0,x-1)])/(x===0||x===w-1?1:2);
          const gy=(a[Math.min(h-1,y+1)*w+x]-a[Math.max(0,y-1)*w+x])/(y===0||y===h-1?1:2);
          const g2=gx*gx+gy*gy;if(g2<.01)continue;
          const normalSpeed=-(a[i]-b[i])*fps/g2;
          let vx=gx*normalSpeed,vy=-gy*normalSpeed; // CPU rows down, fluid Y up.
          const speed=Math.hypot(vx,vy);if(speed>maxSpeed){vx*=maxSpeed/speed;vy*=maxSpeed/speed;}
          velocity[i*2]=vx;velocity[i*2+1]=vy;
        }
      }
    }
    velocities.set(key,velocity);while(velocities.size>2)velocities.delete(velocities.keys().next().value!);
    return velocity;
  };
}

export function colliderKey(p: Project): string {
  if (!p.settings.collider) return '';
  return JSON.stringify([COLLIDER_VERSION, p.id, p.frames, p.people_matte?.id, p.people_matte?.current,
    p.people.selection, p.people.feather, p.people.trim]);
}

export async function loadCollider(p: Project): Promise<ColliderSequence | null> {
  const key = colliderKey(p);
  if (!key) return null;
  if (p.people.selection !== 'manual' || !p.people_matte?.current)
    throw new Error('Build a current people matte before using collider mode.');
  const existing = pending.get(key);
  if (existing) return existing;
  // Capture values now; the reactive project can change while frames are loading.
  const id = p.id, count = p.frames;
  const query = new URLSearchParams({ view: 'mask', matte: p.people_matte.id,
    feather: String(p.people.feather), trim: String(p.people.trim) });
  const promise = (async () => {
    const width = 512, height = 288, data = new Array<Uint8Array>(count);
    let next = 0;
    const worker = async () => {
      const c = document.createElement('canvas'); c.width = width; c.height = height;
      const g = c.getContext('2d', { willReadFrequently: true })!;
      while (next < count) {
        const f = next++;
        const response = await fetch(`/api/projects/${encodeURIComponent(id)}/people/frame/${f}?${query}`);
        if (!response.ok) throw new Error(`Collider matte frame ${f} could not be loaded (${response.status}). Rebuild or reload the matte.`);
        const bitmap = await createImageBitmap(await response.blob(), { resizeWidth: width, resizeHeight: height, resizeQuality: 'high' });
        try { g.clearRect(0, 0, width, height); g.drawImage(bitmap, 0, 0); } finally { bitmap.close(); }
        const rgba = g.getImageData(0, 0, width, height).data, mask = new Uint8Array(width * height);
        for (let i = 0; i < mask.length; i++) mask[i] = rgba[i * 4];
        data[f] = mask;
      }
    };
    // Settle every worker before evicting a failed load; no partial sequence escapes.
    const results = await Promise.allSettled(Array.from({ length: 4 }, worker));
    const failed = results.find(r => r.status === 'rejected');
    if (failed?.status === 'rejected') throw failed.reason;
    return { key, width, height, data };
  })();
  pending.set(key, promise);
  while (pending.size > 2) pending.delete(pending.keys().next().value!);
  try { return await promise; } catch (e) { if (pending.get(key) === promise) pending.delete(key); throw e; }
}

/** Undo each effect's tracked draw translation so its obstacle stays on the person. */
export function colliderForSim(sequence: ColliderSequence, gw: number, gh: number, path: Float32Array | undefined, anchor: number,
    bounds: SimBounds = { x: 0, y: 0, width: 960, height: 540 }) {
  const { width, height } = sequence, data = new Map<number, Uint8Array>();
  const ax = path?.[anchor * 2] ?? 0, ay = path?.[anchor * 2 + 1] ?? 0;
  for (let f = 0; f < sequence.data.length; f++) {
    const src = sequence.data[f], dst = new Uint8Array(gw * gh);
    const dx = ((path?.[f * 2] ?? 0) - ax) * width / 960;
    const dy = ((path?.[f * 2 + 1] ?? 0) - ay) * height / 540;
    for (let y = 0; y < gh; y++) for (let x = 0; x < gw; x++) {
      const sx = ((x + .5) * bounds.width / gw + bounds.x) * width / 960 - .5 + dx;
      const sy = ((y + .5) * bounds.height / gh + bounds.y) * height / 540 - .5 + dy;
      if (sx < 0 || sy < 0 || sx > width - 1 || sy > height - 1) continue;
      const x0 = Math.floor(sx), y0 = Math.floor(sy), x1 = Math.min(width - 1, x0 + 1), y1 = Math.min(height - 1, y0 + 1);
      const u = sx - x0, v = sy - y0;
      dst[y * gw + x] = Math.round((src[y0 * width + x0] * (1-u) + src[y0 * width + x1] * u) * (1-v)
        + (src[y1 * width + x0] * (1-u) + src[y1 * width + x1] * u) * v);
    }
    data.set(f, dst);
  }
  // Squib flips Y when reading uObs; its CPU masks must remain top-down.
  if(sequence.soft){
    const {sigma,drag}=sequence.soft,sx=sigma*gw/bounds.width,sy=sigma*gh/bounds.height;
    const influence=new Map<number,Uint8Array>();
    for(const [f,mask] of data){
      const blurred=gaussian(mask,gw,gh,sx,sy);
      // R8 values0..127 encode soft coverage. They never cross solid()'s0.5
      // threshold, so pressure/advection cannot carve out a binary silhouette.
      influence.set(f,Uint8Array.from(blurred,v=>Math.round(v*127/255)));
    }
    return {gw,gh,data:influence,frames:[...influence.keys()],invert:false,soft:{sigma,drag},
      motion:softWallMotion(data,gw,gh,sx,sy)};
  }
  return { gw, gh, data, frames: [...data.keys()], invert: false, soft: undefined, motion:wallMotion(data,gw,gh) };
}
