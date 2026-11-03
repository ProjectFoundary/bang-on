import {VOL_HEAD} from './shaders3d.js';
import {NOISE} from './shaders.js';
const HEAD=VOL_HEAD.replace('layout(location=0) out vec4 o;','layout(location=0) out vec4 oPosition;layout(location=1) out vec4 oMass;');

// Continuous source cohorts follow the same fine scalar carrier as grid smoke.
// Their positive mass is projected conservatively; there is no render-clock noise.
export const FS_SMOKE_PARTICLE_STEP=HEAD+NOISE+`
uniform sampler2D uPosition,uMass,uCarrier,uDensity;
uniform ivec2 uStateSize;uniform int uCount,uBirthStart,uBirthCount,uSourceCount,uSeed,uStepIndex;
uniform vec4 uSource[8],uSourceData[8];
uniform float uBirthMass,uAge,uDt,uLife,uBreakup,uBreakupK;
uint randomState;
float rnd(){randomState+=0x6D2B79F5u;uint t=(randomState^(randomState>>15u))*(1u|randomState);t^=t+(t^(t>>7u))*(61u|t);return float(t^(t>>14u))*(1./4294967296.);}
vec3 normal3(){
  float a=sqrt(-2.*log(max(rnd(),1e-7))),b=6.2831853*rnd(),c=sqrt(-2.*log(max(rnd(),1e-7))),d=6.2831853*rnd();
  return vec3(a*cos(b),a*sin(b),c*cos(d));
}
bool inBox(vec3 p){return all(greaterThanEqual(p,vec3(0.)))&&all(lessThan(p,uDim));}
void main(){
  ivec2 pixel=ivec2(gl_FragCoord.xy);int id=pixel.y*uStateSize.x+pixel.x;
  if(id>=uCount){oPosition=vec4(0.);oMass=vec4(0.);return;}
  vec4 p=texelFetch(uPosition,pixel,0),m=texelFetch(uMass,pixel,0);
  bool born=uSourceCount>0&&(id-uBirthStart+uCount)%uCount<uBirthCount;
  if(born){
    randomState=uint(uSeed)*0x9E3779B1u^(uint(id)+0x632BE5ABu)*0x85EBCA77u^uint(uStepIndex)*0xC2B2AE3Du;
    float choose=rnd();int source=uSourceCount-1;
    for(int i=0;i<8;i++){if(i>=uSourceCount)break;if(choose<uSourceData[i].x){source=i;break;}}
    vec4 s=uSource[source];vec3 point=s.xyz;
    for(int j=0;j<32;j++){
      vec3 d=normal3()*(s.w/sqrt(6.));float q=dot(d,d)/(s.w*s.w);
      if(q>=1.)continue;
      vec3 candidate=s.xyz+d;if(!inBox(candidate))continue;
      vec3 b=candidate*uBreakupK;
      float n=.65*gnoise(b)+.35*gnoise(b*2.13+7.1);
      float fragment=mix(1.,.12+1.85*smoothstep(.28,.70,n),uBreakup);
      float probability=(1.-smoothstep(.65,1.,q))*fragment/mix(1.,1.97,uBreakup);
      if(rnd()<probability){point=candidate;break;}
    }
    p=vec4(point,uAge);m=vec4(uBirthMass,uSourceData[source].yz,uBirthMass);
  }
  if(m.x<=0.){oPosition=p;oMass=m;return;}
  vec3 a=S(uCarrier,p.xyz).xyz,mid=p.xyz+.5*uDt*a;
  vec3 next=p.xyz+uDt*S(uCarrier,mid).xyz;
  float age=max(0.,uAge+uDt-p.w),rho=max(S(uDensity,next).a,0.);
  float keep=exp(-m.y*uDt*clamp((age-m.z)/.75,0.,1.)*(1.+.12*rho));
  // Match the grid's rounded-box sponge. Recycle cohorts with a soft tail.
  float tail=1.-smoothstep(uLife*.9,uLife,age),previous=1.-smoothstep(uLife*.9,uLife,max(0.,age-uDt));
  m.x*=keep*edgeKeep(next)*tail/max(previous,1e-7);
  if(!inBox(next)||uBoxMin.y+next.y*uVox<uGround||m.x<1e-12)m.x=0.;
  oPosition=vec4(next,p.w);oMass=m;
}`;

export const VS_SMOKE_PARTICLE_DEPOSIT=`#version 300 es
precision highp float;precision highp int;precision highp sampler2D;
uniform sampler2D uPosition,uMass;uniform ivec2 uStateSize;uniform vec3 uGridDim;uniform vec2 uTiles;uniform int uKernel;
flat out float vDensity;
float quadratic(float d,int k){return k==0?.5*(.5-d)*(.5-d):(k==1?.75-d*d:.5*(.5+d)*(.5+d));}
void main(){
  int stride=uKernel==2?27:8,id=gl_VertexID/stride,corner=gl_VertexID%stride;
  ivec2 pixel=ivec2(id%uStateSize.x,id/uStateSize.x);vec4 p=texelFetch(uPosition,pixel,0),m=texelFetch(uMass,pixel,0);
  vec3 q=p.xyz-.5;ivec3 cell;float weight;
  if(uKernel==2){
    ivec3 k=ivec3(corner%3,(corner/3)%3,corner/9);cell=ivec3(floor(q+.5));vec3 d=q-vec3(cell);
    weight=quadratic(d.x,k.x)*quadratic(d.y,k.y)*quadratic(d.z,k.z);cell+=k-1;
  }else{
    ivec3 k=ivec3(corner&1,(corner>>1)&1,(corner>>2)&1);cell=ivec3(floor(q));vec3 d=q-vec3(cell),w=mix(1.-d,d,vec3(k));weight=w.x*w.y*w.z;cell+=k;
  }
  bool valid=m.x>0.&&all(greaterThanEqual(cell,ivec3(0)))&&all(lessThan(cell,ivec3(uGridDim)));
  int tx=int(uTiles.x);vec2 at=vec2((cell.z%tx)*int(uGridDim.x)+cell.x,(cell.z/tx)*int(uGridDim.y)+cell.y)+.5;
  gl_Position=valid?vec4(at/(uGridDim.xy*uTiles)*2.-1.,0.,1.):vec4(3.,3.,0.,1.);gl_PointSize=1.;vDensity=valid?weight*m.x:0.;
}`;
export const FS_SMOKE_PARTICLE_DEPOSIT=`#version 300 es
precision highp float;flat in float vDensity;out vec4 o;
void main(){o=vec4(0.,0.,0.,vDensity);}`;
export const FS_SMOKE_PARTICLE_NORMALIZE=VOL_HEAD+`
uniform sampler2D uOriginal,uParticles,uTotal;uniform vec3 uColor;uniform float uBlend;
void main(){
  ivec3 v=vox();if(!valid(v)){o=vec4(0.);return;}
  vec2 mass=texelFetch(uTotal,ivec2(0),0).rg;
  if(mass.y<=1e-10){o=F(uOriginal,v);return;}
  float density=max(F(uParticles,v).a,0.)*max(mass.x,0.)/mass.y;
  o=mix(F(uOriginal,v),vec4(uColor*density,density),clamp(uBlend,0.,1.));
}`;
