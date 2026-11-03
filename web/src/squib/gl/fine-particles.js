// Particle-carried mineral density. State stays in float32; density is scattered
// with a partition-of-unity kernel into a float32 blending target, then converted
// once for the ordinary volume renderer.
import { NOISE } from './shaders.js';
import { CURL_NOISE3 } from './curl-noise.js';
import { DUST_FADE_GLSL } from './fine-fade.js';
const HEAD=`#version 300 es
precision highp float;precision highp int;precision highp sampler2D;
layout(location=0) out vec4 oPosition;layout(location=1) out vec4 oVelocity;
uniform vec2 uStateSize;uniform int uCount;
`;

export const FINE_PARTICLE_AIR=NOISE+CURL_NOISE3+`
uniform sampler2D uParticleAir;
uniform vec3 uParticleOrigin,uParticleAirMin,uParticleAirDim;
uniform vec2 uParticlePlane,uParticleAirTiles;
uniform float uParticleUnit,uParticleAirVox;
uniform int uParticleSheet;uniform vec2 uParticlePlate;
uniform vec4 uParticleEddy;
vec3 particleEddy(vec3 p){
  if(uParticleEddy.x<=0.)return vec3(0.);
  float age=max(0.,uParticleEddy.z),scale=max(.005,uParticleEddy.y);
  vec3 q=p/scale+uParticleEddy.w;vec3 v;
  if(uParticleSheet==1){
    vec3 a=gradientNoise(vec3(q.xy,age*.35)),b=gradientNoise(vec3(q.xy*2.3+9.7,age*.7+17.));
    v=vec3(a.y,-a.x,0.)+.35*vec3(b.y,-b.x,0.);
  }else v=.7*(curlPotential(q,age*.35)+.35*curlPotential(q*2.3+9.7,age*.7));
  return v*uParticleEddy.x*(1.-exp(-age/.15));
}
vec3 particleWorld(vec3 p){
  if(uParticleSheet==1)return uParticleOrigin+p*uParticleUnit;
  return uParticleOrigin+vec3(uParticlePlane.x*p.x-uParticlePlane.y*p.z,-p.y,uParticlePlane.y*p.x+uParticlePlane.x*p.z)*uParticleUnit;
}
vec3 particleAir(vec3 p){
  if(uParticleSheet==1){
    vec2 uv=particleWorld(p).xy/uParticlePlate;
    vec2 v=texture(uParticleAir,vec2(uv.x,1.-uv.y)).xy*(uParticlePlate.y/uParticleUnit/uParticleAirDim.y);
    return vec3(v.x,-v.y,0.)+particleEddy(p);
  }
  vec3 q=clamp((particleWorld(p)-uParticleAirMin)/uParticleAirVox,vec3(.5),uParticleAirDim-.5);
  float z=q.z-.5,ka=floor(z),kb=min(ka+1.,uParticleAirDim.z-1.),f=z-ka;
  float tx=uParticleAirTiles.x;vec2 size=uParticleAirDim.xy*uParticleAirTiles;
  vec2 a=vec2(mod(ka,tx),floor(ka/tx))*uParticleAirDim.xy,b=vec2(mod(kb,tx),floor(kb/tx))*uParticleAirDim.xy;
  vec3 v=mix(texture(uParticleAir,(a+q.xy)/size).xyz,texture(uParticleAir,(b+q.xy)/size).xyz,f)*uParticleAirVox/uParticleUnit;
  return vec3(dot(v.xz,uParticlePlane),-v.y,dot(v.xz,vec2(-uParticlePlane.y,uParticlePlane.x)))+particleEddy(p);
}
float particleDrift(float t){return -.16*.4*(1.-exp(-t/.4))+.012*t+.026*max(0.,t-3.);}
float particleDriftVelocity(float t){return -.16*exp(-t/.4)+.012+(t>3.?.026:0.);}
`;

export const FS_FINE_PARTICLE_INIT=HEAD+NOISE+`
uniform int uSeed,uId,uFineCount;
uniform vec4 uCohorts[32],uBirthCohorts[32];
uniform float uSrc,uSpread,uFallKick,uSettle,uGravity,uResidual,uFallResidual,uBirth,uMeanFall,uBirthBreakup;
uniform vec4 uFallModes;uniform float uFastSpread,uFastFallSpread;
bool fastParticle(int id){uint q=uint(id)*0x85ebca77u^uint(uSeed);q^=q>>16u;q*=0x7feb352du;q^=q>>15u;return float(q)*(1./4294967296.)<uFallModes.x;}
uint randomState;
float rnd(){randomState+=0x6D2B79F5u;uint t=(randomState^(randomState>>15u))*(1u|randomState);t^=t+(t^(t>>7u))*(61u|t);return float(t^(t>>14u))*(1./4294967296.);}
float normal(){float a=max(rnd(),1e-7),b=rnd();return sqrt(-2.*log(a))*cos(6.2831853*b);}
void main(){
  int id=int(gl_FragCoord.y)*int(uStateSize.x)+int(gl_FragCoord.x);
  if(id>=uCount){oPosition=vec4(0.);oVelocity=vec4(0.);return;}
  randomState=uint(uSeed)*0x9E3779B1u^((uint(id)+0x632BE5ABu)*0x85EBCA77u)^uint(uId*7+79)*0xC2B2AE3Du;
  int group=id%uFineCount;vec4 c=uCohorts[group],b=uBirthCohorts[group];
  float vc=sqrt(max(0.,1.-uResidual*uResidual)),fc=sqrt(max(0.,1.-uFallResidual*uFallResidual)),bc=sqrt(max(0.,1.-uBirth*uBirth));
  float x=(normal()*uBirth+b.x*bc)*uSrc,z=(normal()*uBirth+b.z*bc)*uSrc,y=(normal()*uBirth+b.y*bc)*uSrc*.7;
  if(uBirthBreakup>0.){
    // Rejection sampling changes the birth distribution, not particle mass.
    // The same stationary pockets are then carried and mixed by actual air.
    for(int j=0;j<24;j++){
      vec3 q=vec3(x,y,z)/max(uSrc,1e-5)+float(uSeed%97)*.317;
      float n=.7*gnoise(q*2.1)+.3*gnoise(q*5.3+8.7);
      float probability=mix(1.,.12+.88*smoothstep(.35,.65,n),uBirthBreakup);
      if(rnd()<probability)break;
      x=(normal()*uBirth+b.x*bc)*uSrc;z=(normal()*uBirth+b.z*bc)*uSrc;y=(normal()*uBirth+b.y*bc)*uSrc*.7;
    }
  }
  float vx=(normal()*uResidual+c.x*vc)*uSpread,vz=(normal()*uResidual+c.z*vc)*uSpread;
  float faller=(normal()*uFallResidual+c.y*fc)*.8;
  float weight=1.;
  if(uResidual<1.||uFallResidual<1.||uBirth<1.){
    int population=(uCount-1-group)/uFineCount+1;weight=c.w*float(uCount)/float(population);
  }
  float fallSpeed=uFallKick*(uMeanFall+faller);
  if(uFallModes.x>0.){
    bool fast=fastParticle(id);fallSpeed=(fast?uFallModes.z:uFallModes.y)+faller/.8*uFallModes.w*(fast?uFastFallSpread:1.);
    if(fast){vx*=uFastSpread;vz*=uFastSpread;}
  }
  oPosition=vec4(x,y,z,1.);oVelocity=vec4(vx,(fallSpeed+uSettle)*uGravity,vz,weight);
}`;

export const FS_FINE_PARTICLE_STEP=HEAD+FINE_PARTICLE_AIR+`
uniform sampler2D uPosition,uVelocity;
uniform float uAge,uDt,uTauX,uTauY,uSettle,uGravity,uParticleGround;
uniform vec3 uParticleBoxMin,uParticleBoxMax;uniform int uKillBounds;
uniform float uDiffusion;uniform int uNoiseStep,uNoiseSeed;
uniform vec4 uFallModes;uniform float uFastTau,uFastSettle;uniform vec2 uLateFall;
bool fastParticle(int id){uint q=uint(id)*0x85ebca77u^uint(uNoiseSeed);q^=q>>16u;q*=0x7feb352du;q^=q>>15u;return float(q)*(1./4294967296.)<uFallModes.x;}
uint diffuseHash(uint q){q^=q>>16u;q*=0x7feb352du;q^=q>>15u;q*=0x846ca68bu;return q^(q>>16u);}
vec2 diffuseNormal(uint q){
  float a=(float(diffuseHash(q))+1.)*(1./4294967296.),b=float(diffuseHash(q^0x9e3779b9u))*(1./4294967296.);
  float r=sqrt(-2.*log(max(a,1e-7))),angle=6.2831853*b;return r*vec2(cos(angle),sin(angle));
}
vec2 relax(float v,float terminal,float tau,float dt){float e=exp(-dt/tau),q=tau*(1.-e);return vec2(terminal*dt+(v-terminal)*q,terminal+(v-terminal)*e);}
void main(){
  ivec2 pixel=ivec2(gl_FragCoord.xy);vec4 p=texelFetch(uPosition,pixel,0),v=texelFetch(uVelocity,pixel,0);
  float dt=clamp(uAge+uDt,0.,uDt),age=max(0.,uAge);
  if(p.a<=0.||dt<=0.){oPosition=p;oVelocity=v;return;}
  int id=pixel.y*int(uStateSize.x)+pixel.x;
  bool fast=uFallModes.x>0.&&fastParticle(id);
  float tauY=fast?uFastTau:uTauY,settle=fast?uFastSettle:uSettle;
  vec2 x=relax(v.x,0.,uTauX,dt),z=relax(v.z,0.,uTauX,dt),y;
  float later=(!fast&&uLateFall.x>.5)?uLateFall.y:settle+.01;
  if(age<3.&&age+dt>3.){
    vec2 a=relax(v.y,settle*uGravity,tauY,3.-age),b=relax(a.y,later*uGravity,tauY,age+dt-3.);y=vec2(a.x+b.x,b.y);
  }else y=relax(v.y,(age>=3.?later:settle)*uGravity,tauY,dt);
  vec3 relative=vec3(x.x+particleDrift(age+dt)-particleDrift(age),y.x,z.x);
  vec3 first=particleAir(p.xyz),mid=p.xyz+.5*(relative+first*dt);
  vec3 position=p.xyz+relative+particleAir(mid)*dt;
  if(uDiffusion>0.){
    uint id=uint(pixel.y)*uint(uStateSize.x)+uint(pixel.x),key=id*0x85ebca77u^uint(uNoiseStep)*0x9e3779b1u^uint(uNoiseSeed);
    vec2 xy=diffuseNormal(key);float z=diffuseNormal(key^0x632be5abu).x;
    position+=sqrt(2.*uDiffusion*dt)*vec3(xy,uParticleSheet==1?0.:z);
  }
  vec3 world=particleWorld(position);
  float alive=(uParticleSheet==1?world.y<=uParticleGround:world.y>=uParticleGround)?1.:0.;
  if(uKillBounds==1&&(any(lessThan(world,uParticleBoxMin))||any(greaterThan(world,uParticleBoxMax))))alive=0.;
  oPosition=vec4(position,alive);oVelocity=vec4(x.y,y.y,z.y,v.a);
}`;

export const VS_FINE_PARTICLE_DEPOSIT=`#version 300 es
precision highp float;precision highp int;precision highp sampler2D;
uniform sampler2D uPosition,uVelocity;uniform vec2 uStateSize,uGridTiles;
uniform vec3 uGridDim,uGridMin,uParticleOrigin;uniform vec2 uParticlePlane;
uniform float uGridVox,uParticleUnit,uMass;uniform int uKernel;
flat out float vDensity;
${DUST_FADE_GLSL}
float quadratic(float d,int k){return k==0?.5*(.5-d)*(.5-d):(k==1?.75-d*d:.5*(.5+d)*(.5+d));}
void main(){
  int stride=uKernel==2?27:8,id=gl_VertexID/stride,corner=gl_VertexID%stride;
  ivec2 pixel=ivec2(id%int(uStateSize.x),id/int(uStateSize.x));
  vec4 p=texelFetch(uPosition,pixel,0),v=texelFetch(uVelocity,pixel,0);
  vec3 world=uParticleOrigin+vec3(uParticlePlane.x*p.x-uParticlePlane.y*p.z,-p.y,uParticlePlane.y*p.x+uParticlePlane.x*p.z)*uParticleUnit;
  vec3 q=(world-uGridMin)/uGridVox-.5;ivec3 cell;float weight;
  if(uKernel==2){
    ivec3 k=ivec3(corner%3,(corner/3)%3,corner/9);cell=ivec3(floor(q+.5));vec3 d=q-vec3(cell);
    weight=quadratic(d.x,k.x)*quadratic(d.y,k.y)*quadratic(d.z,k.z);cell+=k-1;
  }else{
    ivec3 k=ivec3(corner&1,(corner>>1)&1,(corner>>2)&1);cell=ivec3(floor(q));vec3 d=q-vec3(cell);
    vec3 w=mix(1.-d,d,vec3(k));weight=w.x*w.y*w.z;cell+=k;
  }
  bool valid=p.a>0.&&all(greaterThanEqual(cell,ivec3(0)))&&all(lessThan(cell,ivec3(uGridDim)));
  int tx=int(uGridTiles.x);vec2 at=vec2((cell.z%tx)*int(uGridDim.x)+cell.x,(cell.z/tx)*int(uGridDim.y)+cell.y)+.5;
  gl_Position=valid?vec4(at/(uGridDim.xy*uGridTiles)*2.-1.,0.,1.):vec4(3.,3.,0.,1.);gl_PointSize=1.;
  vDensity=valid?weight*uMass*v.a*dustParticleWeight(id):0.;
}`;

export const FS_FINE_PARTICLE_DEPOSIT=`#version 300 es
precision highp float;flat in float vDensity;uniform vec3 uColor;out vec4 o;
void main(){o=vec4(uColor*vDensity,vDensity);}`;

export const FS_FINE_PARTICLE_COPY=`#version 300 es
precision highp float;precision highp sampler2D;
uniform sampler2D uSource;layout(location=0) out vec4 o;layout(location=1) out vec4 oMotion;
void main(){o=texelFetch(uSource,ivec2(gl_FragCoord.xy),0);oMotion=vec4(0.,0.,0.,o.a);}`;

export const VS_FINE_PARTICLE_DEPOSIT_2D=`#version 300 es
precision highp float;precision highp int;precision highp sampler2D;
uniform sampler2D uPosition,uVelocity;uniform vec2 uStateSize,uGridDim,uOrigin,uPlate;
uniform float uUnit,uMass;uniform int uKernel;
flat out float vDensity;
${DUST_FADE_GLSL}
float quadratic(float d,int k){return k==0?.5*(.5-d)*(.5-d):(k==1?.75-d*d:.5*(.5+d)*(.5+d));}
void main(){
  int stride=uKernel==2?9:4,id=gl_VertexID/stride,corner=gl_VertexID%stride;
  ivec2 pixel=ivec2(id%int(uStateSize.x),id/int(uStateSize.x));
  vec4 p=texelFetch(uPosition,pixel,0),v=texelFetch(uVelocity,pixel,0);
  vec2 world=uOrigin+p.xy*uUnit,uv=world/uPlate;
  vec2 q=vec2(uv.x,1.-uv.y)*uGridDim-.5;ivec2 cell;float weight;
  if(uKernel==2){
    ivec2 k=ivec2(corner%3,corner/3);cell=ivec2(floor(q+.5));vec2 d=q-vec2(cell);
    weight=quadratic(d.x,k.x)*quadratic(d.y,k.y);cell+=k-1;
  }else{
    ivec2 k=ivec2(corner&1,(corner>>1)&1);cell=ivec2(floor(q));vec2 d=q-vec2(cell);
    vec2 w=mix(1.-d,d,vec2(k));weight=w.x*w.y;cell+=k;
  }
  bool valid=p.a>0.&&all(greaterThanEqual(cell,ivec2(0)))&&all(lessThan(cell,ivec2(uGridDim)));
  gl_Position=valid?vec4((vec2(cell)+.5)/uGridDim*2.-1.,0.,1.):vec4(3.,3.,0.,1.);gl_PointSize=1.;
  vDensity=valid?weight*uMass*v.a*dustParticleWeight(id):0.;
}`;
