import {gl} from '../gl/context.js';
import {toHalf} from '../io/exr.js';

const LIMIT=64*1024*1024,objects=new Map();let used=0,uploads=0;
function halfTable(scale){const f=Float32Array.from({length:256},(_,n)=>(n/255)**2*scale),h=new Uint16Array(256);toHalf(f,h);return h;}
function release(item){for(const tex of item.textures)gl.deleteTexture(tex);used-=item.bytes;}
function reset(){for(const item of objects.values())release(item);objects.clear();used=0;}
function texture(target){
 const t=gl.createTexture();gl.bindTexture(target,t);gl.texParameteri(target,gl.TEXTURE_MIN_FILTER,gl.LINEAR_MIPMAP_LINEAR);gl.texParameteri(target,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
 gl.texParameteri(target,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(target,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
 if(target===gl.TEXTURE_3D)gl.texParameteri(target,gl.TEXTURE_WRAP_R,gl.CLAMP_TO_EDGE);
 gl.pixelStorei(gl.UNPACK_ALIGNMENT,1);return t;
}
export function gpuFrame(cache,index,mode){
 if(!cache)throw Error('Missing baked smoke');
 const key=`${cache.serial}:${mode}:${index}`,cached=objects.get(key);if(cached){objects.delete(key);objects.set(key,cached);return cached;}
 const parametric=mode==='density'&&cache.meta.densityEncoding==='gaussian2d',meta=cache.meta.frames[index][mode],cost=Math.ceil(meta.bytes*(parametric?4/3:mode==='density'?16/7:5/3));
 if(cost>LIMIT/2)throw Error('Smoke cache frame is too large for interpolation. Repack at a lower density resolution.');
 const data=cache.get(index,mode),textures=[];let result,bytes;
 if(parametric){
  const tex=texture(gl.TEXTURE_2D);textures.push(tex);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA16F,...meta.dim,0,gl.RGBA,gl.HALF_FLOAT,new Uint16Array(data.buffer,data.byteOffset,data.byteLength/2));gl.generateMipmap(gl.TEXTURE_2D);
  bytes=cost;result={tex,meta,parametric:true};
 }else if(mode==='density'){
  const table=halfTable(meta.scale),half=new Uint16Array(data.length);
  for(let n=0;n<data.length;n++)half[n]=table[data[n]];
  const tex=texture(gl.TEXTURE_3D);textures.push(tex);gl.texImage3D(gl.TEXTURE_3D,0,gl.R16F,...meta.dim,0,gl.RED,gl.HALF_FLOAT,half);gl.generateMipmap(gl.TEXTURE_3D);
  bytes=Math.ceil(half.byteLength*8/7);result={tex,meta};
 }else{
  const count=meta.dim[0]*meta.dim[1],a=new Uint8Array(count*4),b=new Uint8Array(count*4),tau=new Uint16Array(count);
  const table=halfTable(cache.meta.sheetTauMax);
  for(let n=0;n<count;n++){tau[n]=table[data[n*8]];for(let k=0;k<4;k++){a[n*4+k]=data[n*8+k];b[n*4+k]=data[n*8+k+4];}}
  const t=texture(gl.TEXTURE_2D);textures.push(t);gl.texImage2D(gl.TEXTURE_2D,0,gl.R16F,...meta.dim,0,gl.RED,gl.HALF_FLOAT,tau);gl.generateMipmap(gl.TEXTURE_2D);
  const maps=[a,b].map(buffer=>{const tex=texture(gl.TEXTURE_2D);textures.push(tex);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA8,...meta.dim,0,gl.RGBA,gl.UNSIGNED_BYTE,buffer);gl.generateMipmap(gl.TEXTURE_2D);return tex;});
  bytes=Math.ceil(count*10*4/3);result={tau:t,maps,meta};
 }
 while(used+bytes>LIMIT&&objects.size){const first=objects.keys().next().value;release(objects.get(first));objects.delete(first);}
 result={...result,bytes,textures};objects.set(key,result);used+=bytes;uploads++;return result;
}
export const gpuCacheStatus=()=>({bytes:used,budget:LIMIT,frames:objects.size,uploads});
export const clearGpuCache=reset;
