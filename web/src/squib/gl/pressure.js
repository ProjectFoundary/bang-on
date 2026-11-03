// Geometric multigrid with cell-centred open pressure boundaries. All levels
// use FLOAT pressure/residuals; these temporary corrections are not history.
import {gl,makeDouble,makeFBO,freeFBO,program,pass} from './context.js';
const R32=()=>[gl.R32F,gl.RED,gl.FLOAT];
const head=`#version 300 es
precision highp float;precision highp int;precision highp sampler2D;
in vec2 vUv;layout(location=0)out vec4 o;
uniform ivec3 uSize,uOther;uniform ivec2 uTiles,uOtherTiles;uniform vec3 uInvH2;
uniform sampler2D uP,uRhs,uOtherP;uniform int uFloor;
ivec3 cell(){ivec2 q=ivec2(gl_FragCoord.xy),tile=q/uSize.xy;return ivec3(q-tile*uSize.xy,tile.y*uTiles.x+tile.x);}
ivec2 atlas(ivec3 p,ivec3 size,ivec2 tiles){return ivec2((p.z%tiles.x)*size.x+p.x,(p.z/tiles.x)*size.y+p.y);}
bool inside(ivec3 p,ivec3 size){return all(greaterThanEqual(p,ivec3(0)))&&all(lessThan(p,size));}
float pressure(ivec3 p,float centre){
 if(uFloor==1&&p.y<0)return centre;
 return inside(p,uSize)?texelFetch(uP,atlas(p,uSize,uTiles),0).r:-centre;
}
float neighbours(ivec3 p,float c){
 float s=uInvH2.x*(pressure(p+ivec3(1,0,0),c)+pressure(p-ivec3(1,0,0),c))+uInvH2.y*(pressure(p+ivec3(0,1,0),c)+pressure(p-ivec3(0,1,0),c));
 #ifdef VOLUME
 s+=uInvH2.z*(pressure(p+ivec3(0,0,1),c)+pressure(p-ivec3(0,0,1),c));
 #endif
 return s;
}
float diagonal(){return 2.*(uInvH2.x+uInvH2.y+uInvH2.z);}

`;
const shaders={
 smooth:head+`void main(){ivec3 p=cell();if(!inside(p,uSize)){o=vec4(0.);return;}ivec2 q=ivec2(gl_FragCoord.xy);float c=texelFetch(uP,q,0).r,b=texelFetch(uRhs,q,0).r;o=vec4(mix(c,(neighbours(p,c)-b)/diagonal(),2./3.),0.,0.,1.);}`,
 residual:head+`void main(){ivec3 p=cell();if(!inside(p,uSize)){o=vec4(0.);return;}ivec2 q=ivec2(gl_FragCoord.xy);float c=texelFetch(uP,q,0).r;o=vec4(texelFetch(uRhs,q,0).r-(neighbours(p,c)-diagonal()*c),0.,0.,1.);}`,
 restrict:head+`void main(){ivec3 p=cell();if(!inside(p,uSize)){o=vec4(0.);return;}float sum=0.,n=0.;
 for(int z=0;z<2;z++)for(int y=0;y<2;y++)for(int x=0;x<2;x++){
  #ifndef VOLUME
  if(z>0)continue;
  #endif
  vec3 ratio=vec3(uOther)/vec3(uSize);ivec3 q=ivec3(floor((vec3(p)+(vec3(x,y,z)+.5)*.5)*ratio));
  if(inside(q,uOther)){sum+=texelFetch(uOtherP,atlas(q,uOther,uOtherTiles),0).r;n+=1.;}
 }o=vec4(sum/max(n,1.),0.,0.,1.);}`,
 prolong:head+`float coarse(ivec3 p){
float sign=1.;
for(int k=0;k<3;k++){if(p[k]<0){p[k]=-1-p[k];sign*=-1.;}else if(p[k]>=uOther[k]){p[k]=2*uOther[k]-1-p[k];sign*=-1.;}}
return sign*texelFetch(uOtherP,atlas(clamp(p,ivec3(0),uOther-1),uOther,uOtherTiles),0).r;
}
 void main(){ivec3 p=cell();if(!inside(p,uSize)){o=vec4(0.);return;}vec3 q=(vec3(p)+.5)*vec3(uOther)/vec3(uSize)-.5;ivec3 a=ivec3(floor(q));vec3 f=fract(q);float s=0.;
 for(int z=0;z<2;z++)for(int y=0;y<2;y++)for(int x=0;x<2;x++){
 #ifndef VOLUME
 if(z>0)continue;
 #endif
 ivec3 c=a+ivec3(x,y,z);if(uFloor==1)c.y=max(c.y,0);
 float weight=(x==0?1.-f.x:f.x)*(y==0?1.-f.y:f.y);
 #ifdef VOLUME
 weight*=z==0?1.-f.z:f.z;
 #endif
 s+=weight*coarse(c);
 }o=vec4(texelFetch(uP,ivec2(gl_FragCoord.xy),0).r+s,0.,0.,1.);}`
};
const programs=new Map();
export class PressureSolver{
 constructor(dim,volume=false){
  this.volume=volume;
  if(!programs.has(volume))programs.set(volume,Object.fromEntries(Object.entries(shaders).map(([k,s])=>[k,program(volume?s.replace('#version 300 es','#version 300 es\n#define VOLUME'):s)])));
  this.P=programs.get(volume);this.levels=[];
  for(let size=dim.slice();;size=size.map((n,k)=>!volume&&k===2?1:Math.ceil(n/2))){
   const tx=volume?Math.ceil(Math.sqrt(size[2])):1,tiles=[tx,Math.ceil(size[2]/tx)],w=size[0]*tiles[0],h=size[1]*tiles[1];
   this.levels.push({size,tiles,w,h,p:makeDouble(w,h,R32()),rhs:makeFBO(w,h,R32()),residual:makeFBO(w,h,R32())});
   if(Math.max(...size)<=8)break;
  }
 }
 clear(target){gl.bindFramebuffer(gl.FRAMEBUFFER,target.fbo);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT);}
 uniforms(l){return {uSize:{i3:l.size},uInvH2:l.size.map((n,k)=>!this.volume&&k===2?0:(n/this.levels[0].size[k])**2),uTiles:{i2:l.tiles},uFloor:{i:this.volume?1:0},uP:l.p.read.tex,uRhs:l.rhsTexture??l.rhs.tex};}
 smooth(l,n){for(let j=0;j<n;j++){pass(this.P.smooth,l.p.write,l.w,l.h,this.uniforms(l));l.p.swap();}}
 cycle(index){
  const l=this.levels[index];
  if(index===this.levels.length-1){this.smooth(l,24);return;}
  this.smooth(l,3);
  pass(this.P.residual,l.residual,l.w,l.h,this.uniforms(l));
  const coarse=this.levels[index+1];
  pass(this.P.restrict,coarse.rhs,coarse.w,coarse.h,{...this.uniforms(coarse),uOther:{i3:l.size},uOtherTiles:{i2:l.tiles},uOtherP:l.residual.tex});
  this.clear(coarse.p.read);this.cycle(index+1);
  pass(this.P.prolong,l.p.write,l.w,l.h,{...this.uniforms(l),uOther:{i3:coarse.size},uOtherTiles:{i2:coarse.tiles},uOtherP:coarse.p.read.tex});l.p.swap();
  this.smooth(l,3);
 }
 solve(rhs,cycles=1){
  const l=this.levels[0];l.rhsTexture=rhs;this.clear(l.p.read);
  for(let n=0;n<cycles;n++)this.cycle(0);
  return l.p.read.tex;
 }
 destroy(){for(const l of this.levels){freeFBO(l.p.read);freeFBO(l.p.write);freeFBO(l.residual);freeFBO(l.rhs);}}
}
