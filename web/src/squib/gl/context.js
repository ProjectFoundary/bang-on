import { FS, FS_SPRITE, VS_QUAD, VS_SPRITE } from './shaders.js';

/* =========================================================================
   WebGL setup
   ========================================================================= */
// KeyFX: an offscreen canvas instead of Squib's #gl element; alpha kept (straight) so the effects layer can be drawn over the plate
export const glCanvas = document.createElement('canvas');
export const gl = glCanvas.getContext('webgl2',{alpha:true,antialias:false,premultipliedAlpha:false,preserveDrawingBuffer:true});
if (!gl || !gl.getExtension('EXT_color_buffer_float')){
  $('#viewer').insertAdjacentHTML('beforeend','<div class="fallback">This browser has no WebGL2 float render targets.<br>Open Squib in desktop Chrome, Edge or Firefox.</div>');
  throw new Error('WebGL2 + EXT_color_buffer_float required');
}
gl.getExtension('OES_texture_float_linear');

export function compile(type, src){
  const s=gl.createShader(type); gl.shaderSource(s,src); gl.compileShader(s);
  if(!gl.getShaderParameter(s,gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)+'\n'+src);
  return s;
}
export function program(fs, vs=VS_QUAD){
  const p=gl.createProgram();
  gl.attachShader(p,compile(gl.VERTEX_SHADER,vs)); gl.attachShader(p,compile(gl.FRAGMENT_SHADER,fs));
  gl.linkProgram(p);
  if(!gl.getProgramParameter(p,gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  const locs={};
  return {p, loc(n){ if(!(n in locs)) locs[n]=gl.getUniformLocation(p,n); return locs[n]; }};
}
export const P = {}; for (const k in FS) P[k]=program(FS[k]);
P.sprite = program(FS_SPRITE, VS_SPRITE);

// fullscreen triangle
export const quadVAO = gl.createVertexArray(); gl.bindVertexArray(quadVAO);
gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,3,-1,-1,3]), gl.STATIC_DRAW);
gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0,2,gl.FLOAT,false,0,0);
// sprite instancing
export const spriteVAO = gl.createVertexArray(); gl.bindVertexArray(spriteVAO);
gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,1,1]), gl.STATIC_DRAW);
gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0,2,gl.FLOAT,false,0,0);
export const instBuf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, instBuf);
for (let i=0;i<3;i++){ gl.enableVertexAttribArray(1+i); gl.vertexAttribPointer(1+i,4,gl.FLOAT,false,48,i*16); gl.vertexAttribDivisor(1+i,1); }
gl.bindVertexArray(null);

export function makeTex(w,h,ifmt,fmt,type,filter){
  const t=gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D,t);
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,filter); gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,filter);
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
  gl.texImage2D(gl.TEXTURE_2D,0,ifmt,w,h,0,fmt,type,null);
  return t;
}
export const F16 = ()=>[gl.RGBA16F,gl.RGBA,gl.HALF_FLOAT];
export const R16 = ()=>[gl.R16F,gl.RED,gl.HALF_FLOAT];
export const RG16 = ()=>[gl.RG16F,gl.RG,gl.HALF_FLOAT];
export function makeFBO(w,h,[ifmt,fmt,type],filter=gl.LINEAR){
  const tex=makeTex(w,h,ifmt,fmt,type,filter);
  const fbo=gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER,fbo);
  gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,tex,0);
  gl.clearColor(0,0,0,0); gl.clear(gl.COLOR_BUFFER_BIT);
  return {tex,fbo,w,h};
}
/* two same-size colour targets drawn in one pass (layout locations 0 and 1). The second also gets its own single-target
   FBO, so it can be read back, held out and sampled like any other layer. Returns [both, second]. */
export function makeFBO2(w,h,fmt){
  const a=makeFBO(w,h,fmt), b=makeFBO(w,h,fmt), fbo=gl.createFramebuffer();
  gl.bindFramebuffer(gl.FRAMEBUFFER,fbo);
  gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,a.tex,0);
  gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT1,gl.TEXTURE_2D,b.tex,0);
  gl.drawBuffers([gl.COLOR_ATTACHMENT0,gl.COLOR_ATTACHMENT1]);
  // `only` targets the first texture alone, for passes that write just location 0 (they'd leave the second undefined)
  return [{tex:a.tex,fbo,w,h,only:a}, b];
}
export function freeFBO(f){ if(!f) return; gl.deleteTexture(f.tex); gl.deleteFramebuffer(f.fbo); if (f.only) gl.deleteFramebuffer(f.only.fbo); }
export function makeDouble(w,h,fmt){ const d={read:makeFBO(w,h,fmt), write:makeFBO(w,h,fmt), swap(){ const t=d.read; d.read=d.write; d.write=t; }}; return d; }

export function setUniforms(prog, u){
  gl.useProgram(prog.p); let unit=0;
  for (const k in u){
    const v=u[k], loc=prog.loc(k); if (loc===null) continue;
    if (v instanceof WebGLTexture){ gl.activeTexture(gl.TEXTURE0+unit); gl.bindTexture(gl.TEXTURE_2D,v); gl.uniform1i(loc,unit); unit++; }
    else if (v.tex3d){ gl.activeTexture(gl.TEXTURE0+unit);gl.bindTexture(gl.TEXTURE_3D,v.tex3d);gl.uniform1i(loc,unit);unit++; }
    else if (typeof v==='number') gl.uniform1f(loc,v);
    else if (v.i!==undefined) gl.uniform1i(loc,v.i);
    else if (v.i2!==undefined) gl.uniform2iv(loc,v.i2);
    else if (v.i3!==undefined) gl.uniform3iv(loc,v.i3);
    else if (v.v4!==undefined) gl.uniform4fv(loc,v.v4);
    else if (v.length===2) gl.uniform2f(loc,v[0],v[1]);
    else if (v.length===3) gl.uniform3f(loc,v[0],v[1],v[2]);
    else if (v.length===4) gl.uniform4f(loc,v[0],v[1],v[2],v[3]);
  }
}
export function pass(prog, target, w, h, u){
  gl.bindFramebuffer(gl.FRAMEBUFFER, target ? target.fbo : null);
  gl.viewport(0,0,w,h);
  setUniforms(prog,u);
  gl.bindVertexArray(quadVAO); gl.drawArrays(gl.TRIANGLES,0,3);
}
export let instData = new Float32Array(12*1024);
export function drawSprites(target, w, h, scale, arr, count, mix, fragmentOffset=[0,0]){
  if (!count) return;
  gl.bindFramebuffer(gl.FRAMEBUFFER, target.fbo); gl.viewport(0,0,w,h);
  setUniforms(P.sprite,{uRes:[w,h], uScale:scale, uMix:{i:typeof mix==='number'?mix:mix?1:0},uFragmentOffset:fragmentOffset});
  gl.bindVertexArray(spriteVAO); gl.bindBuffer(gl.ARRAY_BUFFER,instBuf);
  gl.bufferData(gl.ARRAY_BUFFER, arr.subarray(0,count*12), gl.DYNAMIC_DRAW);
  gl.drawArraysInstanced(gl.TRIANGLE_STRIP,0,4,count);
  gl.bindVertexArray(null);
}
/* sprite list builder */
export class Sprites{
  constructor(){ this.a=new Float32Array(12*256); this.n=0; }
  clear(){ this.n=0; }
  push(cx,cy,ax,ay,hw,shape,rot,extra,r,g,b,al){
    if ((this.n+1)*12>this.a.length){ const na=new Float32Array(this.a.length*2); na.set(this.a); this.a=na; }
    const a=this.a, o=this.n*12;
    a[o]=cx;a[o+1]=cy;a[o+2]=ax;a[o+3]=ay;a[o+4]=hw;a[o+5]=shape;a[o+6]=rot;a[o+7]=extra;a[o+8]=r;a[o+9]=g;a[o+10]=b;a[o+11]=al;
    this.n++;
  }
}
export const sprVel=new Sprites(), sprDye=new Sprites(), sprAttr=new Sprites(), sprHeat=new Sprites(), sprMaterial=new Sprites(), sprFx=new Sprites();
