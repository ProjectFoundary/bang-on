import { FIRE, NOISE } from './shaders.js';
import { CURL_NOISE3 } from './curl-noise.js';
import { FINE_DISTRIBUTION } from './fine-distribution.js';
import { DETAIL_NOISE } from './detail-glsl.js';

/* =========================================================================
   3D volume sim shaders. The volume (uDim voxels) is stored as a 2D atlas of
   z-slices, uTiles.x slices across, so every pass is one fullscreen draw over
   the atlas and the whole 2D pass()/FBO/snapshot machinery keeps working.
   Voxel i has its centre at i+0.5 in voxel space. World = uBoxMin + p*uVox.
   ========================================================================= */
export const VOL_HEAD = `#version 300 es
precision highp float; precision highp sampler2D;precision highp sampler3D; precision highp int;
in vec2 vUv; layout(location=0) out vec4 o;
uniform vec3 uDim; uniform vec2 uTiles; uniform vec3 uBoxMin; uniform float uVox, uGround;
uniform vec3 uVelocityDim,uFieldDim;uniform vec2 uVelocityTiles,uFieldTiles;uniform float uVelocityVox,uFieldVox;
uniform float uRefineTurb,uRefineK,uRefineTime,uRefinePhase;
uniform sampler3D uRefineNoise;
uniform int uRk2;uniform float uEdgeStep,uSourceStep;
ivec3 D3(){ return ivec3(uDim); }
ivec3 vox(){ ivec2 fc=ivec2(gl_FragCoord.xy), d=ivec2(uDim.xy), t=fc/d; return ivec3(fc-t*d, t.y*int(uTiles.x)+t.x); }
bool valid(ivec3 v){ return v.z < int(uDim.z); }
ivec2 atl(ivec3 v){ int tx=int(uTiles.x); return ivec2((v.z%tx)*int(uDim.x)+v.x, (v.z/tx)*int(uDim.y)+v.y); }
bool inside(ivec3 v){ return all(greaterThanEqual(v,ivec3(0))) && all(lessThan(v,D3())); }
vec4 F(sampler2D s, ivec3 v){ return texelFetch(s, atl(clamp(v,ivec3(0),D3()-1)), 0); }
// trilinear: hardware bilinear inside two neighbouring slices, mixed in z. Clamped so it never bleeds across tiles.
vec4 sampleAtlas(sampler2D s,vec3 p,vec3 dim,vec2 tiles){
  p = clamp(p, vec3(0.5), dim-0.5);
  float z=p.z-0.5, k0=floor(z), f=z-k0;
  int ka=int(k0), kb=min(ka+1,int(dim.z)-1), tx=int(tiles.x);
  vec2 asz=dim.xy*tiles;
  vec2 oa=vec2(float(ka%tx),float(ka/tx))*dim.xy, ob=vec2(float(kb%tx),float(kb/tx))*dim.xy;
  return mix(texture(s,(oa+p.xy)/asz), texture(s,(ob+p.xy)/asz), f);
}
vec4 S(sampler2D s,vec3 p){return sampleAtlas(s,p,uDim,uTiles);}
// Distinct layouts share world coordinates, including rounded atlas dimensions.
// Preserve exact texel fetches when the scalar and velocity grids coincide.
vec4 C(sampler2D s,vec3 p){return sampleAtlas(s,p*uVox/uVelocityVox,uVelocityDim,uVelocityTiles);}
float carrierNoise(vec3 p){return textureLod(uRefineNoise,p/32.,0.).r;}
// A finite-difference curl of three independent band-limited potentials. This
// supplies irregular small eddies rather than a few repeated plane-wave ribs.
vec3 refineVelocity(vec3 p){
  vec3 q=p*uRefineK+uRefinePhase+uRefineTime*vec3(.13,-.07,.11);
  vec3 x=vec3(.2,0.,0.),y=vec3(0.,.2,0.),z=vec3(0.,0.,.2);
  float zy=carrierNoise(q+y+57.9)-carrierNoise(q-y+57.9);
  float yz=carrierNoise(q+z+31.7)-carrierNoise(q-z+31.7);
  float xz=carrierNoise(q+z+13.1)-carrierNoise(q-z+13.1);
  float zx=carrierNoise(q+x+57.9)-carrierNoise(q-x+57.9);
  float yx=carrierNoise(q+x+31.7)-carrierNoise(q-x+31.7);
  float xy=carrierNoise(q+y+13.1)-carrierNoise(q-y+13.1);
  return vec3(zy-yz,xz-zx,yx-xy)*3.;
}
vec3 velocity(sampler2D s,vec3 p){
  if(uVox==uVelocityVox)return F(s,ivec3(p)).xyz;
  vec3 v=C(s,p).xyz*uVelocityVox/uVox;
  if(uRefineTurb>0.)v+=uRefineTurb*refineVelocity(p);
  return v;
}
vec3 velocitySample(sampler2D s,vec3 p){
  vec3 v=C(s,p).xyz*uVelocityVox/uVox;
  if(uVox!=uVelocityVox&&uRefineTurb>0.)v+=uRefineTurb*refineVelocity(p);
  return v;
}
vec3 traceAir(sampler2D s,vec3 x,float dt){
  vec3 v=velocity(s,x);
  if(uRk2==1)v=velocitySample(s,x-.5*dt*v);
  return x-dt*v;
}
vec4 scalarField(sampler2D s,vec3 p){
  if(uVox==uFieldVox)return F(s,ivec3(p));
  return sampleAtlas(s,p*uVox/uFieldVox,uFieldDim,uFieldTiles);
}
vec4 fieldSample(sampler2D s,vec3 p){return sampleAtlas(s,p*uVox/uFieldVox,uFieldDim,uFieldTiles);}
// Smooth tricubic B-spline sample from 8 trilinear taps (Sigg & Hadwiger). Trilinear has a crease at every voxel
// boundary, and once voxels are several pixels wide the lighting picks those creases out as a visible grid.
vec4 S3(sampler2D s, vec3 p){
  vec3 c=p-0.5, i=floor(c), f=c-i, f2=f*f, f3=f2*f;
  vec3 w0=(1.0-3.0*f+3.0*f2-f3)/6.0, w1=(4.0-6.0*f2+3.0*f3)/6.0, w2=(1.0+3.0*f+3.0*f2-3.0*f3)/6.0, w3=f3/6.0;
  vec3 g0=w0+w1, g1=w2+w3, h0=i-0.5+w1/g0, h1=i+1.5+w3/g1;
  vec4 a=mix(S(s,vec3(h1.x,h0.y,h0.z)), S(s,vec3(h0.x,h0.y,h0.z)), g0.x);
  vec4 b=mix(S(s,vec3(h1.x,h1.y,h0.z)), S(s,vec3(h0.x,h1.y,h0.z)), g0.x);
  vec4 c0=mix(b,a,g0.y);
  a=mix(S(s,vec3(h1.x,h0.y,h1.z)), S(s,vec3(h0.x,h0.y,h1.z)), g0.x);
  b=mix(S(s,vec3(h1.x,h1.y,h1.z)), S(s,vec3(h0.x,h1.y,h1.z)), g0.x);
  return mix(mix(b,a,g0.y), c0, g0.z);
}
// Rounded container: 1 inside, falling to 0 toward the cylinder wall and the lid (never the floor). Smoke is absorbed
// in this sponge layer, so it thins away instead of being cut flat, and leftovers never read as boxes.
float edgeFade(vec3 p){
  vec3 q=p/uDim*2.0-1.0;
  return (1.0-smoothstep(0.7,0.98,length(q.xz)))*(1.0-smoothstep(0.7,0.98,q.y));
}
// The sponge is calibrated per physical second, independently of substeps.
float edgeKeep(vec3 p){float base=mix(.8,1.,edgeFade(p));return uEdgeStep>0.&&uEdgeStep!=1.?pow(base,uEdgeStep):base;}
// solid below the ground plane, and below the bottom of the box
float solidV(ivec3 v){ return (v.y<0 || uBoxMin.y+(float(v.y)+0.5)*uVox < uGround) ? 1.0 : 0.0; }
`;

// Mineral material carries instantaneous and terminal fall speeds weighted by
// density, so the main burst and later shed dust retain distinct motion.
const SETTLING3 = `
uniform sampler2D uMaterial,uSettleDye;
uniform float uHasMaterial,uSettle,uHref,uDragAvg,uFallReach;
vec3 transport(vec3 p,vec3 air){
  float fall=uSettle;
  if(uHasMaterial>.5){
    float density=max(S(uSettleDye,p).a,0.);vec2 material=S(uMaterial,p).rg;
    // A backtrace starts at the destination. Empty cells ahead of the front
    // need the neighbouring dust velocity, otherwise the front pins in place.
    if(density<1e-4){
      for(int j=1;j<=16;j++){
        if(float(j)>uFallReach)break;
        vec3 step=vec3(0.,float(j),0.);
        density+=max(S(uSettleDye,p+step).a,0.)+max(S(uSettleDye,p-step).a,0.);
        material+=S(uMaterial,p+step).rg+S(uMaterial,p-step).rg;
      }
    }
    vec2 speed=material/(density+1e-5);
    fall=(speed.g+(speed.r-speed.g)*uDragAvg)*uHref;
  }
  return air-vec3(0.,fall,0.);
}
vec3 traceMaterial(sampler2D s,vec3 x,float dt){
  vec3 v=transport(x,velocity(s,x));
  if(uRk2==1){vec3 m=x-.5*dt*v;v=transport(m,velocitySample(s,m));}
  return x-dt*v;
}
`;

export const FS3 = {
opticalDensity:VOL_HEAD+DETAIL_NOISE+`
uniform sampler2D uDye,uFlowA,uFlowB,uMacroFlow;uniform float uDetail,uDetailK,uFlowWeight,uDetailEdge,uMacro;
uniform sampler2D uBirth;uniform int uBirthMode,uBirthMacro,uPersistentFine;uniform float uBirthK;
vec3 birthCoordinate(ivec3 v,vec3 fallback){vec4 b=F(uBirth,v);return b.a>1e-7?b.xyz/b.a:fallback;}
void main(){
  ivec3 v=vox();if(!valid(v)){o=vec4(0.);return;}
  vec4 raw=F(uDye,v);float rho=max(raw.a,0.);
  if(rho<1e-7||uDetail<=0.){o=raw;return;}
  vec3 p=vec3(v)+.5,q=vec3(0.);float footprint=0.,n;
  bool born=uBirthMode==1&&F(uBirth,v).a>1e-7;
  if(born){
    q=birthCoordinate(v,vec3(0.));
    vec3 dx=.5*(birthCoordinate(v+ivec3(1,0,0),q)-birthCoordinate(v-ivec3(1,0,0),q));
    vec3 dy=.5*(birthCoordinate(v+ivec3(0,1,0),q)-birthCoordinate(v-ivec3(0,1,0),q));
    vec3 dz=.5*(birthCoordinate(v+ivec3(0,0,1),q)-birthCoordinate(v-ivec3(0,0,1),q));
    footprint=uBirthK*max(length(dx),max(length(dy),length(dz)));
    n=detailFilteredFbm(q*uBirthK,footprint)-.5;
  }else if(uPersistentFine==1){
    // The existing non-renewing air map also carries fine variation. Include
    // its deformation in the footprint to avoid aliasing old stretched folds.
    q=p+F(uMacroFlow,v).xyz;
    vec3 dx=vec3(1.,0.,0.)+.5*(F(uMacroFlow,v+ivec3(1,0,0)).xyz-F(uMacroFlow,v-ivec3(1,0,0)).xyz);
    vec3 dy=vec3(0.,1.,0.)+.5*(F(uMacroFlow,v+ivec3(0,1,0)).xyz-F(uMacroFlow,v-ivec3(0,1,0)).xyz);
    vec3 dz=vec3(0.,0.,1.)+.5*(F(uMacroFlow,v+ivec3(0,0,1)).xyz-F(uMacroFlow,v-ivec3(0,0,1)).xyz);
    footprint=uDetailK*max(length(dx),max(length(dy),length(dz)));
    n=detailFilteredFbm(q*uDetailK,footprint)-.5;
  }else{
    vec3 qa=(p+F(uFlowA,v).xyz)*uDetailK,qb=(p+F(uFlowB,v).xyz)*uDetailK+17.3;
    float norm=inversesqrt(uFlowWeight*uFlowWeight+(1.-uFlowWeight)*(1.-uFlowWeight));
    n=mix(detailFilteredFbm(qb,uDetailK)-.5,detailFilteredFbm(qa,uDetailK)-.5,uFlowWeight)*norm;
  }
  float body=mix(smoothstep(.08,.6,rho),1.,uDetailEdge);
  // Redistribute density instead of removing it with a fixed render threshold.
  // The following reduction preserves the object's integrated extinction.
  float factor=max(.01,1.+uDetail*n*3.*body);
  if(uMacro>0.){
    float broad;
    if(born&&uBirthMacro==1)broad=detailFilteredFbm(q*uBirthK*.12,footprint*.12)-.5;
    else{
    // This material map is never renewed. Filter its stretching explicitly so
    // old billows retain broad variation without a clock-driven phase change.
    vec3 mx=vec3(1.,0.,0.)+.5*(F(uMacroFlow,v+ivec3(1,0,0)).xyz-F(uMacroFlow,v-ivec3(1,0,0)).xyz);
    vec3 my=vec3(0.,1.,0.)+.5*(F(uMacroFlow,v+ivec3(0,1,0)).xyz-F(uMacroFlow,v-ivec3(0,1,0)).xyz);
    vec3 mz=vec3(0.,0.,1.)+.5*(F(uMacroFlow,v+ivec3(0,0,1)).xyz-F(uMacroFlow,v-ivec3(0,0,1)).xyz);
    float macroFootprint=uDetailK*.12*max(length(mx),max(length(my),length(mz)));
    broad=detailFilteredFbm((p+F(uMacroFlow,v).xyz)*uDetailK*.12,macroFootprint)-.5;
    }
    factor*=max(.01,1.+uMacro*broad*3.);
  }
  o=raw*factor;
}`,
opticalReduce:VOL_HEAD+`
uniform sampler2D uOriginal,uModified;uniform vec2 uInputSize;uniform int uFirst;
void main(){
  ivec2 base=ivec2(gl_FragCoord.xy)*2;vec2 sum=vec2(0.);
  for(int n=0;n<4;n++){
    ivec2 p=base+ivec2(n&1,n>>1);if(any(greaterThanEqual(p,ivec2(uInputSize))))continue;
    if(uFirst==1){
      ivec2 d=ivec2(uDim.xy),tile=p/d;ivec3 v=ivec3(p-tile*d,tile.y*int(uTiles.x)+tile.x);
      if(valid(v))sum+=vec2(texelFetch(uOriginal,p,0).a,texelFetch(uModified,p,0).a)*edgeFade(vec3(v)+.5);
    }else sum+=texelFetch(uOriginal,p,0).rg;
  }
  o=vec4(sum,0.,1.);
}`,
opticalNormalize:VOL_HEAD+`
uniform sampler2D uOriginal,uModified,uMass;
void main(){
  ivec3 v=vox();if(!valid(v)){o=vec4(0.);return;}
  vec2 mass=texelFetch(uMass,ivec2(0),0).rg;
  vec4 original=F(uOriginal,v);
  if(mass.g<=1e-12){o=original;return;}
  float density=F(uModified,v).a*(mass.r/mass.g);
  // Reconstruct premultiplied colour from the original material, avoiding a
  // second independent HALF rounding of the temporary field's RGB channels.
  o=original.a>0.?vec4(original.rgb*(density/original.a),density):vec4(0.);
}`,
refineCarrier:VOL_HEAD+`
uniform sampler2D uVel;
void main(){ivec3 v=vox();o=valid(v)?vec4(velocity(uVel,vec3(v)+.5),0.):vec4(0.);}`,
// Analytic density and material displacement share the same kernel weights.
// Evaluate breakup once after the batches rather than once per parcel batch.
fineDistribution: VOL_HEAD+FINE_DISTRIBUTION+`
layout(location=1) out vec4 oMotion;
uniform vec3 uFineOrigin;uniform vec2 uFinePlane;uniform float uFineUnit;
void main(){
  ivec3 v=vox();if(!valid(v)){o=vec4(0.);oMotion=vec4(0.);return;}
  vec3 p=(uBoxMin+(vec3(v)+.5)*uVox-uFineOrigin)/uFineUnit;
  float planeNorm=max(dot(uFinePlane,uFinePlane),1e-6);
  vec3 local=vec3(dot(p.xz,uFinePlane)/planeNorm,-p.y,dot(p.xz,vec2(-uFinePlane.y,uFinePlane.x))/planeNorm),air;
  o=fineDensity(local,true,air);
  vec3 move=vec3(uFinePlane.x*air.x-uFinePlane.y*air.z,-air.y,uFinePlane.y*air.x+uFinePlane.x*air.z)*uFineUnit/uVox;
  oMotion=vec4(move,1.)*o.a;
}`,
fineSplat: VOL_HEAD+`
layout(location=1) out vec4 oMotion;
uniform sampler2D uSrc,uMotion; uniform int uN;
uniform vec4 uP[32],uC[32],uM[32];
void main(){
  ivec3 v=vox();if(!valid(v)){o=vec4(0.);oMotion=vec4(0.);return;}
  ivec2 fc=ivec2(gl_FragCoord.xy);vec3 p=vec3(v)+.5;
  vec4 c=texelFetch(uSrc,fc,0),m=texelFetch(uMotion,fc,0);
  for(int i=0;i<32;i++){
    if(i>=uN)break;
    vec3 delta=p-uP[i].xyz;float q=dot(delta,delta)/(uP[i].w*uP[i].w);
    if(q>1.)continue;
    float w=exp(-q*3.)*(1.-smoothstep(.65,1.,q))*uC[i].a;
    c+=vec4(uC[i].rgb,1.)*w;m+=vec4(uM[i].xyz,1.)*w;
  }
  o=c;oMotion=m;
}`,
shapeFines: VOL_HEAD+DETAIL_NOISE+`
layout(location=1) out vec4 oMotion;
uniform sampler2D uSrc,uMotion;uniform float uBreakup,uBreakupK;
void main(){
  ivec3 v=vox();if(!valid(v)){o=vec4(0.);oMotion=vec4(0.);return;}
  ivec2 fc=ivec2(gl_FragCoord.xy);vec4 c=texelFetch(uSrc,fc,0),m=texelFetch(uMotion,fc,0);
  float fragment=1.;
  if(c.a>1e-7&&uBreakup>0.){
    vec3 q=(vec3(v)+.5-m.xyz/max(m.a,1e-7))*uBreakupK;
    float n=detailFbm(q*.7);
    fragment=mix(1.,.4+1.2*smoothstep(.42,.58,n),uBreakup);
  }
  o=c*fragment;oMotion=m*fragment;
}`,
// Emitter splats: uMode 0 blends velocity+temp toward targets (like the 2D alpha-blended velocity sprites),
// 1 adds dye, 2 adds the per-element attributes (curl, turb, rate, -linger) x density, 3 adds fire heat x density.
splat: VOL_HEAD+NOISE+`
uniform sampler2D uSrc; uniform int uN, uMode; uniform float uBreakup, uBreakupK; uniform vec3 uFragmentOffset;
uniform vec3 uBirthOrigin,uBirthOffset;uniform float uBirthUnit;
uniform float uThermalBreakup,uThermalK;uniform vec3 uThermalOffset;
uniform vec4 uP[32], uA[32], uB[32], uC[32], uE[32],uM[32];   // pos+radius | vel+temp | blend,dye,heat,temp-only | colour | attrs | material
void main(){
  ivec3 v=vox(); if (!valid(v)){ o=vec4(0.); return; }
  vec4 c=texelFetch(uSrc, ivec2(gl_FragCoord.xy), 0); vec3 p=vec3(v)+0.5;
  float fragment=1.0;
  if (uBreakup>0.0 && uMode!=0){
    vec3 q=(p-uFragmentOffset)*uBreakupK;
    float n=0.65*gnoise(q)+0.35*gnoise(q*2.13+7.1);
    fragment=mix(1.0,0.12+1.85*smoothstep(0.28,0.70,n),uBreakup);
  }
  for (int i=0;i<32;i++){
    if (i>=uN) break;
    vec3 d=p-uP[i].xyz; float q=dot(d,d)/(uP[i].w*uP[i].w);
    if (q>1.0) continue;
    float w=exp(-q*3.0)*(1.0-smoothstep(0.65,1.0,q))*fragment;
    if (uMode==0){
      vec4 target=uA[i];
      if(uThermalBreakup>0.){
        vec3 q=(p-uBirthOrigin)*uThermalK+uThermalOffset;
        float n=.7*gnoise(q)+.3*gnoise(q*2.03+17.7);
        target.w*=max(.05,1.+uThermalBreakup*(n-.5)*2.);
      }
      float blendWeight=uB[i].x*w;
      if(uSourceStep>0.&&uSourceStep!=1.)blendWeight=1.-pow(max(0.,1.-blendWeight),uSourceStep);
      vec4 m=mix(c,target,blendWeight);c=uB[i].w>0.5?vec4(c.xyz,m.w):m;
    }   // uB.w: temperature only (smoke draft)
    else if (uMode==1) c+=vec4(uC[i].rgb,1.0)*uB[i].y*w;
    else if (uMode==2) c+=uE[i]*uB[i].y*w;
    else if(uMode==3) c.r+=uB[i].z*w;
    else if(uMode==5)c+=vec4((p-uBirthOrigin)/uBirthUnit+uBirthOffset,1.)*uB[i].y*w;
    else c.rg+=uM[i].rg*uB[i].y*w;
  }
  o=c;
}`,
curl: VOL_HEAD+`
uniform sampler2D uVel;
void main(){
  ivec3 v=vox(); if (!valid(v)){ o=vec4(0.); return; }
  vec3 L=F(uVel,v-ivec3(1,0,0)).xyz, R=F(uVel,v+ivec3(1,0,0)).xyz, B=F(uVel,v-ivec3(0,1,0)).xyz;
  vec3 T=F(uVel,v+ivec3(0,1,0)).xyz, K=F(uVel,v-ivec3(0,0,1)).xyz, N=F(uVel,v+ivec3(0,0,1)).xyz;
  vec3 w=0.5*vec3((T.z-B.z)-(N.y-K.y), (N.x-K.x)-(R.z-L.z), (R.y-L.y)-(T.x-B.x));
  o=vec4(w,length(w));
}`,
forces: VOL_HEAD+NOISE+CURL_NOISE3+`
uniform sampler2D uVel, uCurl, uDye, uAttr;
#ifdef PARTICLE_FINES
uniform sampler2D uFineDensity;uniform vec2 uFineAir;
#endif
uniform float uDt, uBuoy, uWindK, uTime, uTurbK, uCurlK, uNoiseK,uCurlPotential; uniform vec3 uWind;
void main(){
  ivec3 v=vox(); if (!valid(v)){ o=vec4(0.); return; }
  vec4 V=F(uVel,v); float d=scalarField(uDye,vec3(v)+.5).a;
  #ifdef PARTICLE_FINES
  float fd=max(F(uFineDensity,v).a,0.);
  vec4 at=(scalarField(uAttr,vec3(v)+.5)+vec4(uFineAir*fd,0.,0.))/(max(d,0.)+fd+0.004);
  #else
  vec4 at=scalarField(uAttr,vec3(v)+.5)/(max(d,0.)+0.004);
  #endif
  float amt=clamp(at.x,0.,80.)*uCurlK, turb=clamp(at.y,0.,6.);
  // vorticity confinement: push along N x w, N = direction of increasing |w|
  vec3 N=0.5*vec3(F(uCurl,v+ivec3(1,0,0)).w-F(uCurl,v-ivec3(1,0,0)).w, F(uCurl,v+ivec3(0,1,0)).w-F(uCurl,v-ivec3(0,1,0)).w,
                  F(uCurl,v+ivec3(0,0,1)).w-F(uCurl,v-ivec3(0,0,1)).w);
  N/=length(N)+1e-4;
  V.xyz += amt*cross(N, F(uCurl,v).xyz)*uDt;
  // large eddies (the projection step makes them divergence-free)
  if (turb>0.01){
    vec3 q=(vec3(v)+0.5)*uNoiseK; float t=uTime*0.35;
    // mostly sideways: vertical eddies fight the rise and turn the plume back over on itself
    vec3 n;
    // a smaller second layer ruffles column edges, so a clean advection doesn't leave laminar, straight-sided sheets
    vec3 q2=q*3.1+vec3(5.2,1.3,8.7); float t2=t*2.3;
    if(uCurlPotential>0.)n=.3*uCurlPotential*(curlPotential(q,t)+.6*curlPotential(q2,t2));
    else{
      n=(vec3(gnoise(vec3(q.xy,q.z+t)), gnoise(vec3(q.yz+17.,q.x+t)), gnoise(vec3(q.zx+31.,q.y+t)))-0.5)*2.;
      n+=.6*(vec3(gnoise(vec3(q2.xy,q2.z+t2)),gnoise(vec3(q2.yz+17.,q2.x+t2)),gnoise(vec3(q2.zx+31.,q2.y+t2)))-.5)*2.;
      n*=vec3(1.,.35,1.);
    }
    V.xyz += n*turb*uTurbK*uDt;
  }
  // Buoyancy moves warm air; dust has a separate terminal settling speed in transport.
  V.y += uDt*(uBuoy*max(V.w,0.0));
  V.xyz += uDt*uWindK*(uWind - V.xyz);
  V.xyz = clamp(V.xyz, vec3(-4000.), vec3(4000.));
  o=V*(1.0-solidV(v));
}`,
divergence: VOL_HEAD+`
uniform sampler2D uVel;
float comp(ivec3 n, int a, float c){ if (n.y<0) return 0.; if (!inside(n)) return c; if (solidV(n)>0.5) return 0.; return F(uVel,n)[a]; }
void main(){
  ivec3 v=vox(); if (!valid(v)){ o=vec4(0.); return; }
  vec3 C=F(uVel,v).xyz;
  float dv=comp(v+ivec3(1,0,0),0,C.x)-comp(v-ivec3(1,0,0),0,C.x)+comp(v+ivec3(0,1,0),1,C.y)-comp(v-ivec3(0,1,0),1,C.y)
          +comp(v+ivec3(0,0,1),2,C.z)-comp(v-ivec3(0,0,1),2,C.z);
  o=vec4(0.5*dv,0.,0.,1.);
}`,
pressure: VOL_HEAD+`
uniform sampler2D uP, uDiv;
float P(ivec3 n, float c){ if (n.y<0 || (inside(n) && solidV(n)>0.5)) return c; if (!inside(n)) return 0.; return F(uP,n).x; }
void main(){
  ivec3 v=vox(); if (!valid(v)){ o=vec4(0.); return; }
  float c=F(uP,v).x;
  float s=P(v+ivec3(1,0,0),c)+P(v-ivec3(1,0,0),c)+P(v+ivec3(0,1,0),c)+P(v-ivec3(0,1,0),c)+P(v+ivec3(0,0,1),c)+P(v-ivec3(0,0,1),c);
  o=vec4((s-F(uDiv,v).x)/6.0,0.,0.,1.);
}`,
gradient: VOL_HEAD+`
uniform sampler2D uP, uVel;
float P(ivec3 n, float c){ if (n.y<0 || (inside(n) && solidV(n)>0.5)) return c; if (!inside(n)) return 0.; return F(uP,n).x; }
void main(){
  ivec3 v=vox(); if (!valid(v)){ o=vec4(0.); return; }
  float c=F(uP,v).x;
  vec4 V=F(uVel,v);
  V.xyz -= 0.5*vec3(P(v+ivec3(1,0,0),c)-P(v-ivec3(1,0,0),c), P(v+ivec3(0,1,0),c)-P(v-ivec3(0,1,0),c), P(v+ivec3(0,0,1),c)-P(v-ivec3(0,0,1),c));
  o=V*(1.0-solidV(v));
}`,
scale: VOL_HEAD+`
uniform sampler2D uSrc; uniform float uK;
void main(){ o=texelFetch(uSrc, ivec2(gl_FragCoord.xy), 0)*uK; }`,
advect: VOL_HEAD+SETTLING3+`
uniform sampler2D uVel, uSrc; uniform float uDt; uniform vec4 uKeep;uniform int uScalar;
void main(){
  ivec3 v=vox(); if (!valid(v)){ o=vec4(0.); return; }
  vec3 x=vec3(v)+.5,p=uScalar==1?traceMaterial(uVel,x,uDt):traceAir(uVel,x,uDt);
  bool out_=any(lessThan(p,vec3(0.)))||any(greaterThan(p,uDim));
  o=(out_ ? vec4(0.) : S(uSrc,p)*uKeep)*(1.0-solidV(v));
}`,
// Two staggered material displacement fields keep fine detail attached to the fluid.
flow: VOL_HEAD+SETTLING3+`
uniform sampler2D uVel, uSrc; uniform float uDt, uReset;
void main(){
  ivec3 v=vox(); if (!valid(v)){ o=vec4(0.); return; }
  vec3 x=vec3(v)+.5,p=traceMaterial(uVel,x,uDt);
  vec3 d=S(uSrc,p).xyz+(p-x);
  o=vec4(uReset>0.5 ? vec3(0.) : d,0.);
}`,
advectFade: VOL_HEAD+SETTLING3+`
uniform sampler2D uVel, uDye, uAttr; uniform float uDt; uniform int uOut;
void main(){
  ivec3 v=vox(); if (!valid(v)){ o=vec4(0.); return; }
  vec3 x=vec3(v)+.5,p=traceMaterial(uVel,x,uDt);
  if (any(lessThan(p,vec3(0.)))||any(greaterThan(p,uDim))){ o=vec4(0.); return; }
  vec4 Dy=S(uDye,p), A=S(uAttr,p);
  float dens=max(Dy.a,0.), inv=1.0/(dens+1e-5);
  // 3D plumes are optically dense, so a plain exponential fade stays invisible until most of the density has gone.
  // Dense smoke decays proportionally faster here, so Dissipate reads as visible fall-off within a shot.
  float rate=max(A.z*inv,0.)*(1.0+0.12*dens), since=A.w*inv;
  float keep=exp(-rate*uDt*clamp(since/0.75,0.,1.))*edgeKeep(vec3(v)+.5);
  o=(uOut==0 ? Dy*keep : (A+vec4(0.,0.,0.,uDt*dens))*keep)*(1.0-solidV(v));
}`,
// MacCormack correction, as in the 2D mc pass, limited by the 8 source voxels the plain step read from
mc: VOL_HEAD+SETTLING3+`
uniform sampler2D uVel, uSrc, uFwd, uFwdD, uFwdA; uniform float uDt; uniform vec4 uKeep; uniform int uMode;
void main(){
  ivec3 v=vox(); if (!valid(v)){ o=vec4(0.); return; }
  ivec2 fc=ivec2(gl_FragCoord.xy);
  vec3 x=vec3(v)+.5,pb=uMode==0?traceAir(uVel,x,uDt):traceMaterial(uVel,x,uDt);
  vec3 pf=uMode==0?traceAir(uVel,x,-uDt):traceMaterial(uVel,x,-uDt);
  vec4 fwd=texelFetch(uFwd,fc,0), r=fwd;
  if (all(greaterThanEqual(pb,vec3(0.))) && all(lessThanEqual(pb,uDim)) && all(greaterThanEqual(pf,vec3(0.))) && all(lessThanEqual(pf,uDim))){
    r=fwd+0.5*(texelFetch(uSrc,fc,0)-S(uFwd,pf));
    ivec3 i=ivec3(floor(pb-0.5)); vec4 mn=vec4(1e9), mx=vec4(-1e9);
    for (int k=0;k<8;k++){ vec4 c=F(uSrc,i+ivec3(k&1,(k>>1)&1,(k>>2)&1)); mn=min(mn,c); mx=max(mx,c); }
    // revert limiter: where the correction would overshoot, keep the plain step for that channel. Clamping instead
    // leaves flat plateaus that line up with the grid and read as straight-edged fronts.
    r=mix(r,fwd,min(vec4(lessThan(r,mn))+vec4(greaterThan(r,mx)),1.0));
  }
  if (uMode==0){ o=r*uKeep*(1.0-solidV(v)); return; }
  vec4 Dy=texelFetch(uFwdD,fc,0), A=texelFetch(uFwdA,fc,0);
  float dens=max(Dy.a,0.), inv=1.0/(dens+1e-5);
  float rate=max(A.z*inv,0.)*(1.0+0.12*dens), since=A.w*inv;
  float keep=exp(-rate*uDt*clamp(since/0.75,0.,1.))*edgeKeep(x);
  o=(uMode==1 ? r*keep : (r+vec4(0.,0.,0.,uDt*dens))*keep)*(1.0-solidV(v));
}`,
material: VOL_HEAD+SETTLING3+`
uniform sampler2D uVel,uDyeNew;uniform float uDt;
void main(){
  ivec3 v=vox();if(!valid(v)){o=vec4(0.);return;}
  vec3 x=vec3(v)+.5,p=traceMaterial(uVel,x,uDt);
  bool out_=any(lessThan(p,vec3(0.)))||any(greaterThan(p,uDim));
  vec2 speed=out_?vec2(0.):S(uMaterial,p).rg/(max(S(uSettleDye,p).a,0.)+1e-5);
  float fall=speed.g+(speed.r-speed.g)*exp(-2.*uDt);
  o=vec4(vec2(fall,speed.g)*max(F(uDyeNew,v).a,0.),0.,1.);
}`,
// Birth coordinates weighted by density. Reconstruct the material against the
// corrected new dye, so dissipation changes mass without changing its coordinates.
birth:VOL_HEAD+`
uniform sampler2D uVel,uBirth,uDyeNew;uniform float uDt;
void main(){
  ivec3 v=vox();if(!valid(v)){o=vec4(0.);return;}
  vec3 x=vec3(v)+.5,p=traceAir(uVel,x,uDt);
  if(any(lessThan(p,vec3(0.)))||any(greaterThan(p,uDim))){o=vec4(0.);return;}
  vec4 b=S(uBirth,p);float rho=max(F(uDyeNew,v).a,0.);
  o=b.a>1e-7?vec4(b.xyz/b.a,1.)*rho:vec4(0.);
}`,
// Fire heat (heat x density): runs after the dye pass and writes heat back against the new dye, so heat/density is the
// cooled heat whichever advection the dye used (see the 2D heat pass)
heat: VOL_HEAD+SETTLING3+NOISE+FIRE+`
uniform sampler2D uVel, uDye, uHeat, uDyeNew; uniform float uDt, uTime, uNoiseK;
void main(){
  ivec3 v=vox(); if (!valid(v)){ o=vec4(0.); return; }
  vec3 x=vec3(v)+.5,p=traceMaterial(uVel,x,uDt);
  if (any(lessThan(p,vec3(0.)))||any(greaterThan(p,uDim))){ o=vec4(0.); return; }
  float h=max(S(uHeat,p).r/(max(S(uDye,p).a,0.)+1e-5),0.);
  float dn=max(texelFetch(uDyeNew,ivec2(gl_FragCoord.xy),0).a,0.);
  vec3 q=(vec3(v)+0.5)*uNoiseK;
  float mixing=0.35+1.5*smoothstep(0.3,0.75,0.65*gnoise(q+vec3(0.,0.,uTime*0.8))+0.35*gnoise(q*2.3+vec3(4.1,0.,uTime*1.3)));
  h=max(h-uDt*fireCool(h,dn)*mixing,0.);
  o=vec4(h*dn*(1.0-solidV(v)),0.,0.,1.);
}`,
// optical depth from each voxel toward the sun, once per frame, so the render march doesn't need a shadow march per sample
lightVol: VOL_HEAD+`
uniform sampler2D uDye; uniform vec3 uL; uniform float uK;uniform int uSunSteps,uSkySteps;
void main(){
  ivec3 v=vox(); if (!valid(v)){ o=vec4(0.); return; }
  // x: toward the sun. y: straight up, for sky occlusion (dense cores and undersides see less sky)
  vec3 p=vec3(v)+0.5; float tau=0., st=1.0;
  for (int i=0;i<40;i++){
    if(i>=uSunSteps)break;
    p+=uL*st; if (any(lessThan(p,vec3(0.)))||any(greaterThan(p,uDim))) break;
    tau+=max(fieldSample(uDye,p).a,0.)*st; st*=1.1;
  }
  vec3 q=vec3(v)+0.5; float up=0.; st=1.0;
  for (int i=0;i<32;i++){
    if(i>=uSkySteps)break;
    q.y+=st; if (q.y>uDim.y) break;
    up+=max(fieldSample(uDye,q).a,0.)*st; st*=1.12;
  }
  o=vec4(tau*uK,up*uK,0.,1.);
}`,
};

/* Ray-march the volume from the tracked camera into the smoke layer (premultiplied, linear). */
export const FS_RAYMARCH = VOL_HEAD+NOISE+FIRE+DETAIL_NOISE+`
layout(location=1) out vec4 oF;   // fire emission alone (the fire layer); it's also in o
uniform sampler2D uDye,uCoreDye, uHeat, uLightV, uFlowA, uFlowB, uFineFlow; uniform float uFlowWeight; uniform float uDetail, uDetailK, uTime, uSharp, uFireK, uErode, uDetailEdge, uDetailShade,uCoreErode,uCoreScale,uCoreShade,uCoreWarp; uniform int uSmooth, uHasFire,uFineParticles;
uniform vec2 uRes; uniform vec3 uCam, uRight, uUp, uBack; uniform float uTanH, uTanV;
uniform vec3 uLightDim;uniform vec2 uLightTiles;uniform float uLightVox;
uniform vec3 uL; uniform float uStep, uK, uDens, uShadow, uAmb, uGlow; uniform vec3 uKey, uSky, uFire;
uniform vec3 uAlbedo;uniform int uUniformAlbedo;
float ign(vec2 p){ return fract(52.9829189*fract(dot(p,vec2(0.06711056,0.00583715)))); }   // interleaved gradient noise: evenly spread, less grainy than white noise
#ifdef ANALYTIC_FINES
vec2 dustDetail(vec3 q,float rho,float erosion,float shade){
  if(rho<1e-4)return vec2(rho,1.);
  float micro=detailFbm(q)-.5,base=detailBase(q);
  float body=mix(smoothstep(.08,.6,rho),1.,uDetailEdge);
  rho=max(0.,rho*max(0.,1.+uDetail*micro*3.*body)-uDetail*erosion/(1.+rho));
  float slope=2.*(detailBase(q+uL*.3)-base);
  return vec2(rho,1.+clamp(uDetail,0.,1.)*shade*(micro*.7+slope*.8));
}
#endif
void main(){
  vec2 ndc=gl_FragCoord.xy/uRes*2.0-1.0;
  vec3 dir=normalize(uRight*ndc.x*uTanH + uUp*ndc.y*uTanV - uBack);
  vec3 ro=(uCam-uBoxMin)/uVox;
  vec3 inv=1.0/dir, t0=(vec3(0.)-ro)*inv, t1=(uDim-ro)*inv;
  vec3 tmin=min(t0,t1), tmax=max(t0,t1);
  float ta=max(max(max(tmin.x,tmin.y),tmin.z),0.), tb=min(min(tmax.x,tmax.y),tmax.z);
  if (tb<=ta){ o=vec4(0.); oF=vec4(0.); return; }
  float g=0.5, c=dot(uL,dir);
  float phase=mix(1.0, pow(1.0+g*g,1.5)/pow(1.0+g*g-2.0*g*c,1.5), 0.4);
  vec3 acc=vec3(0.), accF=vec3(0.); float Tv=1.0;
  // Blending independent noise fields without this factor cyclically reduces variance,
  // and a nonlinear density mapping turns that into a visible whole-volume pulse.
  float flowNorm=inversesqrt(uFlowWeight*uFlowWeight+(1.0-uFlowWeight)*(1.0-uFlowWeight));
  float t=ta+ign(gl_FragCoord.xy)*uStep;          // per-pixel start offset hides slice banding
  for (int i=0;i<600;i++){
    if (t>tb || Tv<0.004) break;
    vec3 p=ro+dir*t;
    vec4 Dy=S(uDye,p);
    if (uSmooth==1 && Dy.a>1e-4){
      vec4 smoothDy=S3(uDye,p);
      // B-spline sampling is an approximation filter: using it exclusively softens
      // resolved smoke billows. Retain some interpolatory detail; fire keeps its matched heat sampling.
      Dy=uHasFire==1 ? smoothDy : mix(Dy,smoothDy,0.5);
    }
    float rho=max(Dy.a,0.)*edgeFade(p), grain=1.;
    if (rho>1e-4 && uDetail>0.){
      #ifdef ANALYTIC_FINES
      vec4 fine=S(uFineFlow,p);
      // Match the combined density filter before separating the populations.
      // Blending their coordinates changes texture scale as the haze emerges;
      // shape each material independently, then combine extinction and light.
      if(uSmooth==1)fine=mix(fine,S3(uFineFlow,p),uHasFire==1?1.:.5);
      float fineRho=clamp(fine.a,0.,max(Dy.a,0.))*edgeFade(p);
      vec3 move=fine.xyz/max(fine.a,1e-5);
      float coreRho=max(0.,rho-fineRho);vec3 material=p+S(uFlowA,p).xyz;
      if(uCoreWarp>0.&&coreRho>1e-4){
        vec3 q=material*uDetailK*1.2;
        vec3 warp=(vec3(detailBase(q+13.1),detailBase(q+31.7),detailBase(q+57.9))-.5)*6.*uCoreWarp*min(uDetail,1.);
        vec3 wp=p+warp;vec4 coreDye=S(uCoreDye,wp);
        if(uSmooth==1)coreDye=mix(coreDye,S3(uCoreDye,wp),.5);
        coreRho=max(coreDye.a,0.)*edgeFade(p);material=wp+S(uFlowA,wp).xyz;
      }
      vec2 core=dustDetail(material*uDetailK*uCoreScale,coreRho,uCoreErode,uCoreShade);
      vec2 haze=uFineParticles==1?vec2(fineRho,1.):dustDetail((p-move)*uDetailK,fineRho,uErode,uDetailShade);
      rho=core.x+haze.x;
      grain=(core.x*core.y+haze.x*haze.y)/max(rho,1e-7);
      #else
      float weight=uFlowWeight,detailNorm=flowNorm;
      vec3 qa=(p+S(uFlowA,p).xyz)*uDetailK;
      vec3 qb=(p+S(uFlowB,p).xyz)*uDetailK+17.3;
      float baseA=detailBase(qa),baseB=detailBase(qb);
      float na=detailFbm(qa);
      float nb=detailFbm(qb);
      float micro=mix(nb-0.5,na-0.5,weight)*detailNorm;
      // Only fine contrast is renewed. Large-scale shape and optical depth belong to
      // the simulated density, not a crossfade between unrelated broad noise patterns.
      // Leave optically thin wisps to the simulation; detail sculpts the body.
      // Fixed erosion preserves the former haze falloff without time-varying clipping.
      float body=mix(smoothstep(0.08,0.6,rho),1.0,uDetailEdge);
      rho=max(0.0,rho*max(0.0,1.0+uDetail*micro*3.0*body)-uDetail*uErode/(1.0+rho));
      // Reuse the density noise for a forward light gradient (two fewer noise evaluations).
      float slopeA=2.0*(detailBase(qa+uL*0.3)-baseA);
      float slopeB=2.0*(detailBase(qb+uL*0.3)-baseB);
      float slope=mix(slopeB,slopeA,weight)*detailNorm;
      grain=1.0+clamp(uDetail,0.,1.)*uDetailShade*(micro*0.7+slope*0.8);
      #endif
    }
    // edge sharpening: pull thin density toward zero so silhouettes read as defined billows rather than fuzz
    if (uSharp>0.) rho*=smoothstep(0.0, 0.25*uSharp, rho);
    if (rho>1e-4){
      float a=1.0-exp(-rho*uDens*uK*uStep);
      vec3 alb=uUniformAlbedo==1?uAlbedo:clamp(Dy.rgb/max(Dy.a,1e-4),0.,4.);
      vec2 lt=sampleAtlas(uLightV,p*uVox/uLightVox,uLightDim,uLightTiles).xy*uShadow;
      float Tl=(exp(-lt.x)+0.45*exp(-lt.x*0.2))/1.45, sky=mix(0.3,1.0,exp(-lt.y*0.5));
      vec3 em=vec3(0.);
      if (uHasFire==1){   // heat per element, sampled the same way as the density it's divided by
        float h=(uSmooth==1 ? S3(uHeat,p) : S(uHeat,p)).r/max(Dy.a,1e-4);
        if (h>0.8) h+=fireDetail(p*uFireK+vec3(0.,-uTime*2.0,uTime*0.9),h,1.8);
        em=fireEm(h,uFire)*uGlow;
      }
      acc+=Tv*a*(alb*grain*(uSky*uAmb*sky + uKey*(1.0-uAmb)*Tl*phase*1.15) + em); accF+=Tv*a*em;
      Tv*=1.0-a;
    }
    t+=uStep;
  }
  o=vec4(acc,1.0-Tv); oF=vec4(accF,1.0-Tv);   // same alpha, so far-to-near 'over' attenuates the fire layer too
}`;
