// Sub-grid suspended grains share the analytic cloud's seeded kinematics.
// Evaluate them on the GPU so their count does not add CPU particle updates.
import { FINE_WAVE_GLSL } from '../sim/fine-wave.js';
import { FINE_PARTICLE_AIR } from './fine-particles.js';
import { DUST_FADE_GLSL } from './fine-fade.js';
export const VS_FINE_GRAINS=`#version 300 es
precision highp float;precision highp int;
uniform int uSeed,uId,uWorld,uFloor,uBurstSpread,uFineCount;
uniform vec3 uOrigin,uCam,uRight,uUp,uBack,uTint;
uniform vec2 uRes,uPlate,uTan,uPlane,uPhaseB,uFall;
uniform vec4 uF,uPhaseA;
uniform float uTime,uUnit,uGravity,uTurb,uMeanFall,uSize,uAlpha,uFade,uRate,uLinger,uGround,uShutter;
uniform vec4 uCohorts[32],uBirthCohorts[32];uniform float uFineResidual,uFineBirth,uFineFallResidual;
uniform int uParticleMode;uniform sampler2D uParticlePos,uParticleVel;uniform vec2 uParticleStateSize;
flat out vec4 vColor;flat out vec4 vFoot;flat out vec2 vAxis,vCenter;
uint randomState;
float rnd(){
  randomState+=0x6D2B79F5u;uint t=(randomState^(randomState>>15u))*(1u|randomState);
  t^=t+(t^(t>>7u))*(61u|t);return float(t^(t>>14u))*(1./4294967296.);
}
float normal(){float a=max(rnd(),1e-7),b=rnd();return sqrt(-2.*log(a))*cos(6.2831853*b);}
`+FINE_WAVE_GLSL+FINE_PARTICLE_AIR+DUST_FADE_GLSL+`
vec2 wave(float a,float b,float da,float db,float p,float q){
  return fineWaveMotion(a,b,da,db,p,q,uTime);
}
void main(){
  randomState=uint(uSeed)*0x9E3779B1u^((uint(gl_VertexID)+0x632BE5ABu)*0x85EBCA77u)^uint(uId*7+79)*0xC2B2AE3Du;
  vec4 cohort=uCohorts[gl_VertexID%uFineCount],born=uBirthCohorts[gl_VertexID%uFineCount];float mixture=sqrt(max(0.,1.-uFineResidual*uFineResidual)),birth=sqrt(max(0.,1.-uFineBirth*uFineBirth)),fallMix=sqrt(max(0.,1.-uFineFallResidual*uFineFallResidual));
  float px=(normal()*uFineBirth+born.x*birth)*uF.x,pz=(normal()*uFineBirth+born.z*birth)*uF.x,py=(normal()*uFineBirth+born.y*birth)*uF.x*.7;
  float vx=(normal()*uFineResidual+cohort.x*mixture)*uF.y*(uWorld==1?1.25:1.),vz=(normal()*uFineResidual+cohort.z*mixture)*uF.y*(uWorld==1?1.25:1.);
  float faller=clamp((normal()*uFineFallResidual+cohort.y*fallMix)*.8,-1.7,1.7),size=uSize*(.4+1.4*rnd()),shade=.6+.65*rnd(),t=max(0.,uTime);
  float ef=exp(-t/.65),fall=uFall.x*.65*(1.-ef)*uGravity,df=uFall.x*ef*uGravity;
  float terminal=(uFall.y*t+.01*max(0.,t-3.))*uGravity,dt=(uFall.y+(t>3.?.01:0.))*uGravity;
  float ed=exp(-t/.4),drift=-.16*.4*(1.-ed)+.012*t+.026*max(0.,t-3.),dd=-.16*ed+.012+(t>3.?.026:0.);
  float es=exp(-t/uF.z),eg=exp(-t/2.5),modulation=uBurstSpread==1?1.:(.4+.75*(1.-eg));
  float sp=uF.z*(1.-es)*modulation;
  float ds=es*modulation+(uBurstSpread==1?0.:uF.z*(1.-es)*.3*eg);
  float ee=exp(-t/.45),a=uF.w*uTurb*(1.-ee),da=uF.w*uTurb*ee/.45;
  vec3 p=vec3(px+vx*sp,py+fall*faller,pz+vz*sp),v=vec3(vx*ds,df*faller,vz*ds);
  vec2 w=wave(p.y,uWorld==1?p.z:0.,v.y,uWorld==1?v.z:0.,uPhaseA.x,uPhaseA.y);
  p.x+=a*w.x;v.x+=da*w.x+a*w.y;
  w=wave(uWorld==1?p.z:0.,p.x,uWorld==1?v.z:0.,v.x,uPhaseA.z,uPhaseA.w);
  p.y+=a*.75*w.x;v.y+=.75*(da*w.x+a*w.y);
  if(uWorld==1){w=wave(p.x,p.y,v.x,v.y,uPhaseB.x,uPhaseB.y);p.z+=a*w.x;v.z+=da*w.x+a*w.y;}
  p+=vec3(drift,fall*uMeanFall+terminal,0.);v+=vec3(dd,df*uMeanFall+dt,0.);
  float particleWeight=1.,alive=1.;
  if(uParticleMode==1){
    ivec2 pixel=ivec2(gl_VertexID%int(uParticleStateSize.x),gl_VertexID/int(uParticleStateSize.x));
    vec4 saved=texelFetch(uParticlePos,pixel,0),relative=texelFetch(uParticleVel,pixel,0);
    p=saved.xyz;v=relative.xyz+particleAir(p)+vec3(particleDriftVelocity(t),0.,0.);alive=saved.a;particleWeight=relative.a*dustParticleWeight(gl_VertexID);
  }
  vec2 ndc,velocity;float radius;
  bool visible=alive>0.;
  if(uWorld==1){
    p=uOrigin+vec3(uPlane.x*p.x-uPlane.y*p.z,-p.y,uPlane.y*p.x+uPlane.x*p.z)*uUnit;
    v=vec3(uPlane.x*v.x-uPlane.y*v.z,-v.y,uPlane.y*v.x+uPlane.x*v.z)*uUnit;
    vec3 d=p-uCam;float z=-dot(d,uBack),dz=-dot(v,uBack);
    visible=visible&&z>1e-5&&p.y>=uGround&&(uFloor==0||p.y>=uOrigin.y);z=max(z,1e-5);
    vec2 xy=vec2(dot(d,uRight),dot(d,uUp));
    ndc=xy/(z*uTan);velocity=.5*uRes/uTan*(vec2(dot(v,uRight),dot(v,uUp))*z-xy*dz)/(z*z);
    radius=size*uUnit*uRes.y/(2.*z*uTan.y);
  }else{
    p.xy=uOrigin.xy+p.xy*uUnit;v.xy*=uUnit;
    visible=visible&&(uFloor==0||p.y<=uOrigin.y);
    ndc=vec2(p.x/uPlate.x*2.-1.,1.-p.y/uPlate.y*2.);
    velocity=vec2(v.x,-v.y)*uRes/uPlate;radius=size*uUnit*uRes.y/uPlate.y;
  }
  vec2 axis=velocity*uShutter*.5;float L=length(axis),hw=max(radius,.75),halfSize=L+hw;
  float ramp=clamp((t-.15)/.4,0.,1.),end=max(5.,10./max(.1,uRate))+uLinger;
  float alpha=uAlpha*ramp*exp(-uRate*max(0.,t-uLinger)/uFade)*clamp((end-t)/2.,0.,1.);
  if(uParticleMode==1)alpha*=particleWeight;
  else if(uFineResidual<1.||uFineFallResidual<1.||uFineBirth<1.)alpha*=cohort.w*float(uFineCount);
  alpha*=min(1.,radius*radius/(hw*hw))*hw/(L+hw)/(1.+.25*t);
  alpha=min(alpha,1.);vColor=vec4(uTint*shade*alpha,alpha);vFoot=vec4(halfSize,hw,L,0.);vAxis=L>1e-5?axis/L:vec2(1.,0.);
  vCenter=(ndc*.5+.5)*uRes-axis;
  gl_Position=visible?vec4(ndc-2.*axis/uRes,0.,1.):vec4(3.,3.,0.,1.);gl_PointSize=2.*halfSize;
}`;

export const FS_FINE_GRAINS=`#version 300 es
precision highp float;precision highp int;
flat in vec4 vColor,vFoot;flat in vec2 vAxis,vCenter;
uniform sampler2D uObs;uniform vec2 uRes;uniform int uWorld;
out vec4 o;
void main(){
  if(uWorld==0&&texture(uObs,vec2(gl_FragCoord.x/uRes.x,1.-gl_FragCoord.y/uRes.y)).r>.5)discard;
  vec2 d=gl_FragCoord.xy-vCenter,p=vec2(dot(d,vAxis),dot(d,vec2(-vAxis.y,vAxis.x)));
  vec2 q=vec2(max(abs(p.x)-vFoot.z,0.),p.y)/vFoot.y;float r=dot(q,q);
  if(r>1.||vColor.a<1e-6)discard;
  o=vColor*exp(-r*3.)*(1.-smoothstep(.65,1.,r));
}`;
