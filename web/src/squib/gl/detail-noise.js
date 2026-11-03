import { gl } from './context.js';
import { rng } from '../util.js';

// Isotropic, band-limited random field: no gradient-lattice nodes or planes.
// The separable Gaussian is rotationally symmetric; a periodic boundary and
// mip chain keep sampling continuous across scale. Material motion supplies time.
let cached;
export function detailNoise(){
  if(cached)return cached;
  const size=128,q=rng(725371),data=new Uint8Array(size**3);
  let a=new Float32Array(size**3),b=new Float32Array(size**3);
  for(let n=0;n<a.length;n++)a[n]=q()-.5;
  const blur=(sigma,radius)=>{
    const kernel=Array.from({length:radius*2+1},(_,n)=>Math.exp(-((n-radius)**2)/(2*sigma**2))),total=kernel.reduce((s,v)=>s+v,0);
    for(let n=0;n<kernel.length;n++)kernel[n]/=total;
    for(let axis=0;axis<3;axis++){
      for(let z=0;z<size;z++)for(let y=0;y<size;y++)for(let x=0;x<size;x++){
        let v=0;
        for(let j=-radius;j<=radius;j++){
          const xx=axis===0?(x+j+size)&127:x,yy=axis===1?(y+j+size)&127:y,zz=axis===2?(z+j+size)&127:z;
          v+=a[(zz*size+yy)*size+xx]*kernel[j+radius];
        }
        b[(z*size+y)*size+x]=v;
      }
      [a,b]=[b,a];
    }
  };
  blur(1.5,4);const fine=a.slice();blur(3.5,10);
  // Remove low spatial frequencies before renewing phases. Unrelated broad
  // offsets otherwise brighten/dim an entire puff even after variance correction.
  let variance=0;for(let n=0;n<a.length;n++){fine[n]-=a[n];variance+=fine[n]*fine[n];}
  const gain=.14/Math.sqrt(variance/a.length);
  for(let n=0;n<data.length;n++)data[n]=Math.max(0,Math.min(255,Math.round((.5+gain*fine[n])*255)));
  const tex=gl.createTexture();gl.bindTexture(gl.TEXTURE_3D,tex);
  for(const axis of [gl.TEXTURE_WRAP_S,gl.TEXTURE_WRAP_T,gl.TEXTURE_WRAP_R])gl.texParameteri(gl.TEXTURE_3D,axis,gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_3D,gl.TEXTURE_MIN_FILTER,gl.LINEAR_MIPMAP_LINEAR);gl.texParameteri(gl.TEXTURE_3D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
  gl.texImage3D(gl.TEXTURE_3D,0,gl.R8,size,size,size,0,gl.RED,gl.UNSIGNED_BYTE,data);
  gl.generateMipmap(gl.TEXTURE_3D);
  return cached={tex3d:tex};
}
