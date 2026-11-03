import { FINE_DISTRIBUTION } from './fine-distribution.js';
import { DETAIL_NOISE } from './detail-glsl.js';
export const VS_QUAD = `#version 300 es
layout(location=0) in vec2 aPos; out vec2 vUv;
void main(){ vUv=aPos*0.5+0.5; gl_Position=vec4(aPos,0.,1.); }`;

export const FS_HEAD = `#version 300 es
precision highp float; precision highp sampler2D;
in vec2 vUv; layout(location=0) out vec4 o;
uniform vec2 uTexel; uniform sampler2D uObs, uObsVel;
uniform int uRk2;
float solid(vec2 uv){ return step(0.5, texture(uObs, vec2(uv.x, 1.0-uv.y)).r); }
vec2 wallVelocity(vec2 uv){ return texture(uObsVel,vec2(uv.x,1.0-uv.y)).rg; }
bool outside(vec2 uv){ return uv.x<0.0||uv.x>1.0||uv.y<0.0||uv.y>1.0; }
vec2 traceAir(sampler2D velocity,vec2 x,float dt){
  vec2 v=texture(velocity,x).xy;
  if(uRk2==1)v=texture(velocity,x-.5*dt*v*uTexel).xy;
  return x-dt*v*uTexel;
}
`;

export const NOISE = `
// gradient noise with quintic fade: no value-noise lattice creases when it's stretched hard along the wind
vec3 g3(vec3 p){ p=fract(p*vec3(.1031,.1030,.0973)); p+=dot(p,p.yxz+33.33); return normalize(fract((p.xxy+p.yxx)*p.zyx)*2.0-1.0); }
float gnoise(vec3 p){
  vec3 i=floor(p), f=fract(p), u=f*f*f*(f*(f*6.0-15.0)+10.0);
  #define GD(o) dot(g3(i+o), f-o)
  float n = mix(mix(mix(GD(vec3(0,0,0)),GD(vec3(1,0,0)),u.x), mix(GD(vec3(0,1,0)),GD(vec3(1,1,0)),u.x),u.y),
                mix(mix(GD(vec3(0,0,1)),GD(vec3(1,0,1)),u.x), mix(GD(vec3(0,1,1)),GD(vec3(1,1,1)),u.x),u.y), u.z);
  #undef GD
  return n*0.9+0.5;
}
float fbm(vec3 p){
  float s=0., a=0.5; mat2 r=mat2(0.8,-0.6,0.6,0.8);   // rotate each octave to break up grid alignment
  for (int i=0;i<5;i++){ s+=a*gnoise(p); p.xy=r*p.xy; p=p*vec3(2.03,2.01,1.7)+vec3(1.7,9.2,3.1); a*=0.5; }
  return s/0.97;
}
`;

/* Fire, shared by the 2D and 3D paths. Heat lives in its own field as heat x smoke density (like attr), so heat per
   element survives dissipation and mixing and only changes by cooling. It drives the glow only; buoyancy stays with the
   velocity field's temperature, so a white-hot fireball can still climb slowly. */
export const FIRE = `
// The reference fireball keeps one orange chroma at every brightness and reads white-hot only because it clips, so
// emission is one colour with an HDR intensity: nothing below heat 0.8, x5 per 0.4 of heat (heat 2.6 is ~100x heat 1.2).
// Dim fire is a touch redder.
vec3 fireEm(float h, vec3 col){
  float I = max(exp(4.02*(min(h,3.8)-1.2))-0.2, 0.);
  return col*vec3(1.0, mix(0.85,1.0,clamp(I*0.25,0.,1.)), 1.0)*I;
}
// Cooling per second, fitted to the reference's peak brightness: white-hot holds only while fuel keeps burning, then
// collapses to orange within ~0.15 s, then a slow glow that's gone by ~2.5 s. Thin smoke has mixed with more air, so it
// cools faster: fireball edges and wisps go dark first.
// Render-time flame detail the grid can't hold (needs NOISE): billowy lobes split by dark creases. Creases are pulled
// much cooler and lobes keep their heat, so it reads as bright flame over darker soot rather than an even glow.
// Cooler flame gets less of it, or its creases drop out of the glow entirely and it breaks into spots. amp > 1 in 3D,
// where the emission of several samples along the ray averages the creases out.
float fireDetail(vec3 q, float h, float amp){
  float b = 0.65*abs(gnoise(q)*2.0-1.0) + 0.35*abs(gnoise(q*2.3+3.7)*2.0-1.0);
  return -amp*mix(0.15, 0.5, smoothstep(1.4, 2.2, h))*(1.0-smoothstep(0.08, 0.5, b));
}
float fireCool(float h, float dens){ float x=max(h-1.7,0.); return (0.6+10.0*x*x)*(0.5+1.2/(1.0+dens*4.0)); }
`;

const SETTLING = `
uniform sampler2D uDye, uMaterial; uniform float uGridH,uDragAvg,uFallReach;
vec2 transport(vec2 p, vec2 air){
  float d=max(texture(uDye,p).a,0.);
  vec2 material=texture(uMaterial,p).rg;
  if(d<1e-4){
    for(int j=1;j<=16;j++){
      if(float(j)>uFallReach)break;
      vec2 step=vec2(0.,float(j)*uTexel.y);
      d+=max(texture(uDye,p+step).a,0.)+max(texture(uDye,p-step).a,0.);
      material+=texture(uMaterial,p+step).rg+texture(uMaterial,p-step).rg;
    }
  }
  vec2 speed=material/(d+1e-5);
  float fall=(speed.g+(speed.r-speed.g)*uDragAvg)*uGridH;
  return air-vec2(0.,fall);
}
vec2 traceMaterial(sampler2D velocity,vec2 x,float dt){
  vec2 v=transport(x,texture(velocity,x).xy);
  if(uRk2==1){vec2 m=x-.5*dt*v*uTexel;v=transport(m,texture(velocity,m).xy);}
  return x-dt*v*uTexel;
}
`;

export const FS = {
advect: FS_HEAD+SETTLING+`
uniform int uScalar; uniform sampler2D uVel, uSrc; uniform float uDt; uniform vec4 uKeep;
void main(){
  vec2 p=uScalar==1?traceMaterial(uVel,vUv,uDt):traceAir(uVel,vUv,uDt);
  vec4 r = outside(p) ? vec4(0.) : texture(uSrc, p)*uKeep;
  o = r*(1.0-solid(vUv));
}`,
curl: FS_HEAD+`
uniform sampler2D uVel;
void main(){
  float L=texture(uVel,vUv-vec2(uTexel.x,0.)).y, R=texture(uVel,vUv+vec2(uTexel.x,0.)).y;
  float T=texture(uVel,vUv+vec2(0.,uTexel.y)).x, B=texture(uVel,vUv-vec2(0.,uTexel.y)).x;
  o = vec4(0.5*(R-L-T+B),0.,0.,1.);
}`,
forces: FS_HEAD+NOISE+`
#ifdef PARTICLE_FINES
uniform sampler2D uParticleForces;
#endif
uniform sampler2D uVel, uCurl, uDye, uAttr; uniform float uDt, uBuoy, uWindK, uTime, uTurbK,uFineTurb,uSoftColliderDrag; uniform vec2 uWind, uAspect;
// divergence-free large-scale eddies: curl of an evolving noise potential
vec2 curlNoise(vec2 p, float t){
  float e=0.05;
  #define PSI(q) (gnoise(vec3(q,t)) + 0.5*gnoise(vec3((q)*2.1+7.3,t*1.4)))
  float dx=PSI(p+vec2(e,0.))-PSI(p-vec2(e,0.)), dy=PSI(p+vec2(0.,e))-PSI(p-vec2(0.,e));
  #undef PSI
  return vec2(dy,-dx)/(2.0*e);
}
void main(){
  float d = texture(uDye,vUv).a;
  // per-element curl: attr carries curl x density, so this is the density-weighted curl of whatever smoke is here
  #ifdef PARTICLE_FINES
  vec4 fine=texture(uParticleForces,vUv);
  vec2 at=(texture(uAttr,vUv).rg+fine.rg)/(max(d,0.)+fine.a+0.004);
  #else
  vec2 at = texture(uAttr,vUv).rg/(max(d,0.)+0.004);
  #endif
  float amt = clamp(at.x, 0., 80.), turb = clamp(at.y, 0., 6.);
  float L=texture(uCurl,vUv-vec2(uTexel.x,0.)).x, R=texture(uCurl,vUv+vec2(uTexel.x,0.)).x;
  float T=texture(uCurl,vUv+vec2(0.,uTexel.y)).x, B=texture(uCurl,vUv-vec2(0.,uTexel.y)).x;
  float C=texture(uCurl,vUv).x;
  vec2 f = 0.5*vec2(abs(T)-abs(B), abs(R)-abs(L));
  f = f/(length(f)+1e-4)*amt*C; f.y=-f.y;
  vec4 v = texture(uVel,vUv);
  v.xy += f*uDt;
  if (turb>0.01){
    vec2 large=curlNoise(vUv*uAspect*4.5,uTime*0.35);
    vec2 billow=curlNoise(vUv*uAspect*14.0+7.1,uTime*0.5);
    v.xy+=(large+billow*0.55)*turb*uTurbK*uDt;
    if(uFineTurb>0.){
      float frequency=min(48.,1./uTexel.y/8.);
      vec2 small=curlNoise(vUv*uAspect*frequency+vec2(21.3,9.7),uTime*1.1);
      float warm=smoothstep(.003,.025,max(v.z,0.))*(1.-smoothstep(.15,.3,v.z));
      v.xy+=small*uFineTurb*warm*turb*uTurbK*uDt;
    }
  }
  v.y += uDt*(uBuoy*max(v.z,0.0));   // ambient smoke is neutral; mineral dust settles in transport
  v.xy += uDt*uWindK*(uWind - v.xy);
  v.xy = clamp(v.xy, vec2(-4000.), vec2(4000.));
  if(uSoftColliderDrag>0.){
    float coverage=clamp(texture(uObs,vec2(vUv.x,1.-vUv.y)).r*(255./127.),0.,1.);
    float coupling=1.-exp(-uSoftColliderDrag*uDt*coverage);
    v.xy=mix(v.xy,wallVelocity(vUv),coupling);
  }
  o = solid(vUv)>.5 ? vec4(wallVelocity(vUv),0.,0.) : v;
}`,
divergence: FS_HEAD+`
uniform sampler2D uVel;
float vx(vec2 uv,float c){ if(outside(uv)) return c; if(solid(uv)>0.5) return wallVelocity(uv).x; return texture(uVel,uv).x; }
float vy(vec2 uv,float c){ if(outside(uv)) return c; if(solid(uv)>0.5) return wallVelocity(uv).y; return texture(uVel,uv).y; }
void main(){
  vec2 C=texture(uVel,vUv).xy;
  float L=vx(vUv-vec2(uTexel.x,0.),C.x), R=vx(vUv+vec2(uTexel.x,0.),C.x);
  float T=vy(vUv+vec2(0.,uTexel.y),C.y), B=vy(vUv-vec2(0.,uTexel.y),C.y);
  o = vec4(0.5*(R-L+T-B),0.,0.,1.);
}`,
pressure: FS_HEAD+`
uniform sampler2D uP, uDiv;
float P(vec2 uv,float c){ if(outside(uv)) return 0.; if(solid(uv)>0.5) return c; return texture(uP,uv).x; }
void main(){
  float c=texture(uP,vUv).x, d=texture(uDiv,vUv).x;
  float s=P(vUv-vec2(uTexel.x,0.),c)+P(vUv+vec2(uTexel.x,0.),c)+P(vUv+vec2(0.,uTexel.y),c)+P(vUv-vec2(0.,uTexel.y),c);
  o = vec4((s-d)*0.25,0.,0.,1.);
}`,
gradient: FS_HEAD+`
uniform sampler2D uP, uVel;
float P(vec2 uv,float c){ if(outside(uv)) return 0.; if(solid(uv)>0.5) return c; return texture(uP,uv).x; }
void main(){
  float c=texture(uP,vUv).x;
  float L=P(vUv-vec2(uTexel.x,0.),c), R=P(vUv+vec2(uTexel.x,0.),c), T=P(vUv+vec2(0.,uTexel.y),c), B=P(vUv-vec2(0.,uTexel.y),c);
  vec4 v=texture(uVel,vUv); v.xy -= 0.5*vec2(R-L,T-B);
  vec2 l=vUv-vec2(uTexel.x,0.),r=vUv+vec2(uTexel.x,0.),b=vUv-vec2(0.,uTexel.y),t=vUv+vec2(0.,uTexel.y);
  float sl=solid(l),sr=solid(r),sb=solid(b),st=solid(t);
  if(sl+sr>0.)v.x=(sl*wallVelocity(l).x+sr*wallVelocity(r).x)/(sl+sr);
  if(sb+st>0.)v.y=(sb*wallVelocity(b).y+st*wallVelocity(t).y)/(sb+st);
  o = solid(vUv)>.5 ? vec4(wallVelocity(vUv),0.,0.) : v;
}`,
advectFade: FS_HEAD+SETTLING+`
// Advects dye (uOut 0) or attr (uOut 1) with per-element dissipation. attr = (curl, turb, rate, age-linger) x density,
// so rate and time-since-linger are density-weighted averages of whatever smoke ended up here.
uniform sampler2D uVel, uAttr; uniform float uDt; uniform int uOut;
void main(){
  vec2 p=traceMaterial(uVel,vUv,uDt);
  if (outside(p)){ o = vec4(0.); return; }
  vec4 D = texture(uDye,p), A = texture(uAttr,p);
  float dens = max(D.a,0.), inv = 1.0/(dens+1e-5);
  float rate = max(A.z*inv,0.)*(0.6+0.8/(1.0+dens*4.0)), since = A.w*inv;   // thin wisps go first, cores last
  float keep = exp(-rate*uDt*clamp(since/0.75,0.,1.));   // eases in over 0.75 s once the linger time is up
  vec4 r = uOut==0 ? D*keep : (A + vec4(0.,0.,0.,uDt*dens))*keep;
  o = r*(1.0-solid(vUv));
}`,
// Scalar material is reconstructed against corrected dye so its mass ratio survives MacCormack.
material: FS_HEAD+SETTLING+`
uniform sampler2D uVel, uDyeNew; uniform float uDt;
void main(){
  vec2 p=traceMaterial(uVel,vUv,uDt);
  vec4 material=outside(p)?vec4(0.):texture(uMaterial,p)/(max(texture(uDye,p).a,0.)+1e-5);
  vec2 speed=material.rg;
  // Exact constant-gravity/linear-drag update. A dense burst can start upward
  // and accelerate down; shed fines can instead slow toward a low terminal speed.
  float fall=speed.g+(speed.r-speed.g)*exp(-uDt/0.5);
  o=vec4(fall,speed.g,material.ba)*max(texelFetch(uDyeNew,ivec2(gl_FragCoord.xy),0).a,0.);
}`,
heat: FS_HEAD+NOISE+FIRE+`
// Advects fire heat (heat x density) and cools it. Runs after the dye pass: heat per element comes from a plain step of
// both fields (a density-weighted average, so it can't overshoot even when the dye is MacCormack-advected), and is
// written back against the new dye (uDyeNew), so heat/density is exactly the cooled heat. Cooling is uneven (air
// mixing in turbulent pockets), so dark soot patches form inside the flame and then ride the flow.
uniform sampler2D uVel, uDye, uHeat, uDyeNew; uniform float uDt, uTime; uniform vec2 uAspect;
void main(){
  vec2 p=traceAir(uVel,vUv,uDt);
  if (outside(p)){ o = vec4(0.); return; }
  float h = max(texture(uHeat,p).r/(max(texture(uDye,p).a,0.)+1e-5), 0.);
  float dn = max(texelFetch(uDyeNew, ivec2(gl_FragCoord.xy), 0).a, 0.);
  vec2 q = vUv*uAspect*14.0;
  float mixing = 0.35 + 1.5*smoothstep(0.3, 0.75, 0.65*gnoise(vec3(q, uTime*0.8)) + 0.35*gnoise(vec3(q*2.3+4.1, uTime*1.3)));
  h = max(h - uDt*fireCool(h,dn)*mixing, 0.);
  o = vec4(h*dn*(1.0-solid(vUv)), 0., 0., 1.);
}`,
flow: FS_HEAD+SETTLING+`
// Flow noise for 2D render detail. Two displacement fields (xy and zw, in plate heights, y up) say how far the smoke at
// each texel has moved since that field was last reset, so detail noise looked up at (position + displacement) moves
// with the flow. Displacement rather than position keeps half floats precise. Each field resets to zero in turn
// (uReset); the render crossfades them so a reset never shows.
uniform sampler2D uVel, uSrc; uniform float uDt, uH; uniform vec2 uReset;
void main(){
  vec2 p=traceMaterial(uVel,vUv,uDt);
  vec4 d = outside(p) ? vec4(0.) : texture(uSrc, p);
  vec2 s = (vUv-p)/uTexel*uH;       // this step's move in plate heights
  d -= vec4(s, s);
  if (uReset.x > 0.5) d.xy = vec2(0.);
  if (uReset.y > 0.5) d.zw = vec2(0.);
  o = d;
}`,
mc: FS_HEAD+SETTLING+`
// MacCormack correction (sharper advection). uFwd holds the plain semi-Lagrangian step; stepping it back and comparing
// with the source estimates the blur that step added, which is put back. Where that would overshoot the four source
// texels the step read from, the plain step is kept instead (revert limiter), so it can't ring or plateau. uMode 0: multiply by uKeep (velocity).
// 1/2: dye/attr with per-element dissipation, from the advected dye (uFwdD) and attr (uFwdA) at this texel.
uniform sampler2D uVel, uSrc, uFwd, uFwdD, uFwdA; uniform float uDt; uniform vec4 uKeep; uniform int uMode;
void main(){
  ivec2 fc = ivec2(gl_FragCoord.xy);
  vec2 pb=uMode==0?traceAir(uVel,vUv,uDt):traceMaterial(uVel,vUv,uDt);
  vec2 pf=uMode==0?traceAir(uVel,vUv,-uDt):traceMaterial(uVel,vUv,-uDt);
  vec4 fwd = texelFetch(uFwd, fc, 0), r = fwd;
  if (!outside(pb) && !outside(pf)){
    r = fwd + (uMode==0 ? 0.5 : 0.35)*(texelFetch(uSrc, fc, 0) - texture(uFwd, pf));
    vec2 i = floor(pb/uTexel - 0.5);
    vec4 a=texture(uSrc,(i+vec2(0.5,0.5))*uTexel), b=texture(uSrc,(i+vec2(1.5,0.5))*uTexel);
    vec4 c=texture(uSrc,(i+vec2(0.5,1.5))*uTexel), d=texture(uSrc,(i+vec2(1.5,1.5))*uTexel);
    vec4 mn=min(min(a,b),min(c,d)), mx=max(max(a,b),max(c,d));
    r = mix(r, fwd, min(vec4(lessThan(r,mn))+vec4(greaterThan(r,mx)),1.0));   // revert limiter (see the 3D mc pass)
  }
  if (uMode==0){ o = r*uKeep*(1.0-solid(vUv)); return; }
  vec4 D = texelFetch(uFwdD, fc, 0), A = texelFetch(uFwdA, fc, 0);
  float dens = max(D.a,0.), inv = 1.0/(dens+1e-5);
  float rate = max(A.z*inv,0.)*(0.6+0.8/(1.0+dens*4.0)), since = A.w*inv;
  float keep = exp(-rate*uDt*clamp(since/0.75,0.,1.));
  o = (uMode==1 ? r*keep : (r + vec4(0.,0.,0.,uDt*dens))*keep)*(1.0-solid(vUv));
}`,
combine: FS_HEAD+`
uniform sampler2D uA,uB; uniform float uMask;
void main(){vec4 b=texture(uB,vUv);if(uMask>0.5)b*=1.0-solid(vUv);o=texture(uA,vUv)+b;}`,
fineDistribution: FS_HEAD+FINE_DISTRIBUTION+`
layout(location=1) out vec4 oMotion;
uniform vec2 uFineOrigin,uFinePlate;uniform float uFineUnit;
void main(){
  vec3 local=vec3((vec2(vUv.x,1.-vUv.y)*uFinePlate-uFineOrigin)/uFineUnit,0.),air;
  o=fineDensity(local,false,air);oMotion=vec4(vec2(-air.x,air.y)*uFineUnit/uFinePlate.y,0.,1.)*o.a;
}`,
combineFines: FS_HEAD+DETAIL_NOISE+`
layout(location=1) out vec4 oMotion;
uniform sampler2D uFluid,uFines,uMotion,uParticleFines; uniform float uGridH,uHasParticles; uniform vec2 uPhase;
void main(){
  vec4 d=texture(uFines,vUv),m=texture(uMotion,vUv);
  float fragment=1.;
  if(d.a>1e-7){
    vec2 move=m.xy/max(m.a,1e-7);
    vec3 p=vec3((gl_FragCoord.xy+move*uGridH-uPhase)*.3,4.7);
    float n=detailFbm(p*.7);
    // Suspended haze has soft density variation. A narrow threshold prints
    // sharp bright curls even when its broad lighting is prefiltered.
    fragment=.65+.7*smoothstep(.30,.70,n);
  }
  fragment*=1.0-solid(vUv);
  vec4 particle=uHasParticles>0.?texture(uParticleFines,vUv):vec4(0.);
  o=texture(uFluid,vUv)+d*fragment+particle*(1.-solid(vUv));oMotion=m*fragment+vec4(0.,0.,particle.a,particle.a);
}`,
scale: FS_HEAD+`
uniform sampler2D uSrc; uniform float uK;
void main(){ o = texture(uSrc,vUv)*uK; }`,
blurDensity: FS_HEAD+`
uniform sampler2D uSrc;uniform vec2 uStep;
void main(){
  vec4 d=vec4(0.);float total=0.;
  for(int j=-4;j<=4;j++){
    float w=exp(-float(j*j)/5.78);vec2 p=vUv+float(j)*uStep;
    if(!outside(p))d+=texture(uSrc,p)*w;total+=w;
  }
  o=d/total;
}`,
smoke: FS_HEAD+NOISE+FIRE+DETAIL_NOISE+`
layout(location=1) out vec4 oF;   // fire emission alone (the fire layer); it's also in o
uniform sampler2D uDye, uSoftDye, uHeat, uFlow, uFineFlow,uMaterial,uPhysicalDye; uniform float uHasFines; uniform float uTime, uDetail, uDetailF, uWA; uniform vec2 uAspect;
uniform vec2 uLight, uLdir;      // uLight: one march step toward the light in sim uv; uLdir: unit in-plane light direction (y up)
uniform float uCosE, uSinE;      // light front/back: sin>0 lit from the camera side, sin<0 backlit
uniform float uDens, uShadow, uAmb, uGlow, uShape, uSlice, uOpacitySoft; uniform vec3 uFire, uKey, uSky;
// Integrate the density footprint once on the simulation grid. The former
// four diagonal taps imprinted their stencil on thin, textured haze.
float rho(vec2 p){return max(texture(uSoftDye,p).a,0.);}
// sub-grid lumps riding the flow: two flow-noise phases (see the flow pass) crossfaded by uWA. Smooth noise, not ridged:
// creases read as bright veins, while soft lumps pick up light and shadow from the surface normal and read as puffs
float lumps(vec2 q){ return 0.6*gnoise(vec3(q,1.7))+0.28*gnoise(vec3(q*2.3+3.1,4.3))+0.12*gnoise(vec3(q*5.7,8.1)); }
vec4 materialFlow(vec2 p,out float phase){
  vec4 d=texture(uFlow,p);phase=uWA;
  vec4 material=texture(uMaterial,p);float physical=max(texture(uPhysicalDye,p).a,1e-5);
  float mineral=clamp(material.g/(physical*.015),0.,1.);
  vec2 birth=material.ba/physical,move=birth-p*uAspect;
  d=mix(d,vec4(move,move),mineral);phase=mix(phase,1.,mineral);
  if(uHasFines>.5){
    vec4 m=texture(uFineFlow,p)*(1.0-solid(p));
    float w=clamp(m.a/max(texture(uDye,p).a,1e-5),0.,1.);
    vec2 move=m.xy/max(m.a,1e-5);
    d=mix(d,vec4(move,move),w);phase=mix(phase,1.0,w);
  }
  return d;
}
float flowNoise(vec2 p,out float norm){
  float phase;vec4 d=materialFlow(p,phase);vec2 x=p*uAspect;
  norm=inversesqrt(phase*phase+(1.0-phase)*(1.0-phase));
  return mix(lumps((x+d.zw)*uDetailF+17.3),lumps((x+d.xy)*uDetailF),phase);
}
float flowN(vec2 p){float norm;return flowNoise(p,norm);}
// density with that detail: lumps and voids at grid scale and below, thin edges eaten first, dense cores mostly kept
vec4 opacityDye(vec2 p){
  vec4 raw=texture(uDye,p);
  if(uOpacitySoft<=0.)return raw;
  // A small mixture with the existing density footprint softens very sharp
  // transported curls. Keep hot flame density at its original resolution.
  float cold=1.-smoothstep(.001,.02,texture(uHeat,p).r);
  // Particle dust is already deposited with a smooth kernel. Preserve its
  // transported wisps while retaining the broader footprint for lighting.
  float particle=uHasFines>.5?clamp(texture(uFineFlow,p).z/max(raw.a,1e-5),0.,1.):0.;
  return mix(raw,texture(uSoftDye,p),clamp(uOpacitySoft,0.,1.)*cold*mix(1.,.45,particle));
}
float rhoD(vec2 p){
  // Filter the lighting footprint, not the opacity itself: using the broad
  // light field here erases transported wisps before they can be rendered.
  float r = max(opacityDye(p).a,0.);
  float particle=uHasFines>.5?clamp(texture(uFineFlow,p).z/max(texture(uDye,p).a,1e-5),0.,1.):0.;
  float detail=uDetail*(1.-particle);
  if (detail <= 0.0 || r < 1e-4) return r;
  float norm;float noise=flowNoise(p,norm);float n=(noise-.5)*norm;
  return max(0.0, r*(1.0 + detail*n*1.5) - detail*0.035*smoothstep(0.0,0.08,r)/(1.0 + r*4.0));
}
float opac(vec2 p){ return 1.0-exp(-rhoD(p)*uDens); }
float thickness(vec2 p){ return log(1.0+rho(p)*uDens); }
// density as the light sees it: equal to rho while thin, saturating once the smoke is opaque. Raw density kept summing
// past that, so a dense core (a black column, a heavy dust puff) went flat black instead of showing lit billows.
float rhoS(vec2 p){ return (1.0-exp(-rho(p)*uDens))/uDens; }   // coarse (no detail): it lights the large-scale shape
void main(){
  vec4 d = opacityDye(vUv);
  if (d.a < 1e-4) { o = vec4(0.); oF = vec4(0.); return; }
  vec3 alb = clamp(d.rgb/max(d.a,1e-4), 0., 4.);
  float r0 = rhoD(vUv);
  float a = 1.0 - exp(-r0*uDens*uSlice);
  // optical depth toward the light in the image plane. Steps grow so the march sees the local puff and the column behind it
  float tau = 0., st = 1.0; vec2 p = vUv;
  for (int i=0;i<16;i++){ p += uLight*st; tau += rhoS(p)*st; st *= 1.13; }
  // plus the smoke's own thickness along the view axis, which dominates when backlit (capped: past ~4x opaque it's all hidden)
  tau = (uCosE*tau*mix(0.16,0.23,uShape) + min(r0,4.0/uDens)*(uSinE<0. ? -uSinE*1.5 : uSinE*0.2))*uShadow;
  // two-lobe transmittance: a hard shadow plus a soft multiple-scatter term so dense cores don't go black
  float T = (exp(-tau) + 0.45*exp(-tau*0.2))/1.45;
  // pseudo surface normal from the opacity gradient gives each puff some form
  vec2 e = uTexel*2.0;
  vec2 gr = vec2(0.);
  // Shape the body using two coarse optical-thickness scales. Opacity saturates in
  // opaque cores, while log thickness retains billow relief without lighting noise as ridges.
  vec2 b=uTexel*3.0;
  vec2 body=vec2(thickness(vUv+vec2(b.x,0.))-thickness(vUv-vec2(b.x,0.)),
                 thickness(vUv+vec2(0.,b.y))-thickness(vUv-vec2(0.,b.y)));
  vec2 wide=vec2(thickness(vUv+vec2(b.x*3.0,0.))-thickness(vUv-vec2(b.x*3.0,0.)),
                 thickness(vUv+vec2(0.,b.y*3.0))-thickness(vUv-vec2(0.,b.y*3.0)))/3.0;
  gr=mix(body,wide,0.80)*mix(0.35,1.0,uShape);
  vec3 n = normalize(vec3(-gr*mix(2.0,10.0,uShape),1.0));
  vec3 L = vec3(uLdir*uCosE, uSinE);
  // thin smoke is translucent: only reasonably opaque puffs show surface form, or the edges of a thin sheet emboss
  float diff = mix(1.0, clamp((dot(n,L)+0.35)/1.35, 0., 1.)*1.5, mix(0.25,0.70,uShape)*smoothstep(0.15, 0.75, 1.0-exp(-r0*uDens)));
  // Henyey-Greenstein forward scatter: thin backlit edges glow (silver lining), front-lit smoke stays flat
  float g = 0.5, c = -uSinE;
  float phase = mix(1.0, pow(1.0+g*g, 1.5)/pow(1.0+g*g-2.0*g*c, 1.5), 0.4);
  // ambient occlusion from the surrounding density, and tops catch more sky
  float occ = 0.;
  for (int i=0;i<8;i++){ float an = float(i)*0.785398+0.39; vec2 dv = vec2(cos(an),sin(an))*uTexel; occ += rhoS(vUv+dv*5.0) + rhoS(vUv+dv*13.0); }
  float ao = exp(-occ*mix(0.06,0.10,uShape)*uShadow);
  float skyK = 0.85 + 0.25*n.y;
  float finePhase;vec4 fd=materialFlow(vUv,finePhase);vec2 x=vUv*uAspect;
  float fine=mix(gnoise(vec3((x+fd.zw)*uDetailF*9.0+17.3,7.4)),
                 gnoise(vec3((x+fd.xy)*uDetailF*9.0,7.4)),finePhase)-0.5;
  fine*=inversesqrt(finePhase*finePhase+(1.0-finePhase)*(1.0-finePhase));
  if(uHasFines>.5)fine*=1.-clamp(texture(uFineFlow,vUv).z/max(d.a,1e-5),0.,1.);
  // Very thin haze scatters through its depth; renewing a surface grain there
  // reads as brightness shimmer. Keep full relief and grain in opaque billows.
  float shadeDetail=clamp(uDetail,0.,1.)*smoothstep(.08,.4,1.0-exp(-rho(vUv)*uDens));
  float structure=mix(1.0,0.65+0.7*flowN(vUv),shadeDetail)*(1.0+shadeDetail*fine*0.9);
  vec3 col = alb*structure*(uSky*uAmb*ao*skyK + uKey*(1.0-uAmb)*T*diff*phase*1.15);
  float h = texture(uHeat, vUv).r/max(d.a,1e-4);
  if (h > 0.8) h += fireDetail(vec3(vUv*uAspect*20.0 + vec2(0.,-uTime*2.0), uTime*0.9), h, 1.0);   // lobes ~0.05 H, drifting up with the fire
  vec3 em = fireEm(h, uFire) * uGlow;
  o = vec4((col + em)*a, a);
  oF = vec4(em*a, a);
}`,
holdout: FS_HEAD+`
// Holdout: drawn with blendFunc(ZERO, ONE_MINUS_SRC_ALPHA), so the layer underneath is multiplied by (1 - matte) in place.
// The matte is an ImageBitmap (rows not flipped on upload), like the plate.
uniform sampler2D uMatte; uniform float uInvert;
void main(){ float m = texture(uMatte, vec2(vUv.x, 1.0-vUv.y)).r; o = vec4(0., 0., 0., uInvert>0.5 ? 1.0-m : m); }`,
over: FS_HEAD+`
uniform sampler2D uFront,uBack;
void main(){vec4 a=texture(uFront,vUv),b=texture(uBack,vUv);o=a+b*(1.-a.a);}`,
comp: FS_HEAD+`
uniform sampler2D uS, uDB,uDF, uK, uN, uF, uPlate; uniform int uMode, uView; uniform float uHasPlate, uShowObs;
vec3 toSrgb(vec3 c){ c=max(c,0.); return mix(c*12.92, 1.055*pow(c,vec3(1.0/2.4))-0.055, step(0.0031308,c)); }
void main(){
  vec4 S=texture(uS,vUv), DB=texture(uDB,vUv),DF=texture(uDF,vUv), K=texture(uK,vUv), N=texture(uN,vUv);
  if (uMode==0){
    if (!(uView==0||uView==5||uView==4)) N=vec4(0.);
    if (!(uView==0||uView==1||uView==4)) S=vec4(0.);
    if (!(uView==0||uView==2||uView==4)){DB=vec4(0.);DF=vec4(0.);}
    if (!(uView==0||uView==3||uView==4)) K=vec4(0.);
  }
  vec4 fx = DB;
  fx = S + fx*(1.0-S.a);
  fx = DF + fx*(1.0-DF.a);
  fx = N + fx*(1.0-N.a);
  fx.rgb += K.rgb; fx.a = fx.a + clamp(K.a,0.,1.)*(1.0-fx.a);
  fx.a = clamp(fx.a,0.,1.);
  if (uMode==1){ o = fx; return; }
  if (uMode==2){ vec3 c = fx.a>1e-5 ? fx.rgb/fx.a : vec3(0.); o = vec4(toSrgb(min(c,1.)), fx.a); return; }
  vec3 bg;
  if (uHasPlate>0.5) bg = texture(uPlate, vec2(vUv.x,1.0-vUv.y)).rgb;
  else { vec2 q=floor(gl_FragCoord.xy/16.); bg = vec3(mod(q.x+q.y,2.)<1. ? 0.035 : 0.05); }
  if (uMode==0 && uView==4){ o = vec4(vec3(fx.a),1.); return; }
  if (uMode==0 && uView==6){ o = vec4(toSrgb(min(texture(uF,vUv).rgb,1.)),1.); return; }   // fire emission alone
  vec3 c = fx.rgb + bg*(1.0-fx.a);
  if (uMode==0 && uShowObs>0.5){ float s=solid(vUv); c = mix(c, vec3(0.19,0.5,0.6), s*0.45); }
  o = vec4(toSrgb(min(c,1.)),1.);
}`,
drift: FS_HEAD+NOISE+`
// Procedural spindrift plume off a ridge line. Evaluated per output pixel in plate px, so it stays crisp at any
// resolution. Everything is a pure function of time, so preview and export match.
uniform vec2 uRes, uPlate, uC, uW, uRidge;   // uW: wind dir, uRidge: ridge dir (plate px, y down)
uniform float uHalf, uLen, uThick, uSpread, uLift, uT, uSpeed, uFront, uTail, uAmt, uDetail, uAmb, uSinE;
uniform vec3 uAlb, uKey, uSky, uSeed;
float sstep(float a, float b, float x){ float t=clamp((x-a)/(b-a),0.,1.); return t*t*(3.-2.*t); } // works for b<a too
void main(){
  vec2 p = vec2(gl_FragCoord.x, uRes.y-gl_FragCoord.y)*(uPlate.x/uRes.x);
  vec2 n = vec2(-uW.y, uW.x);
  vec2 q = p - uC;
  float u0 = max(dot(q,uW), 0.);
  q.y += uLift*0.4*u0*u0/uLen;                        // bend the plume up (lift>0) or down as it travels
  float u = dot(q,uW), v = dot(q,n);
  float lu = dot(uRidge,uW)*uHalf, lv = dot(uRidge,n)*uHalf;
  float s = clamp(v*lv/(lv*lv+1.0), -1.0, 1.0);       // where on the ridge this streamer left from
  float d = u - lu*s, vo = v - lv*s;                  // downwind distance from there, and cross-wind overshoot
  float sig = uThick + uSpread*max(d,0.);
  float env = sstep(-uThick, uThick+uLen*0.08, d) * exp(-max(d,0.)/(uLen*0.45)) * sstep(uLen*1.6, uLen*0.6, d);
  env *= sstep(uFront, uFront-uLen*0.15, d) * sstep(uTail, uTail+uLen*0.15, d);   // wind picking up / dying off
  float m = env*exp(-vo*vo/(sig*sig));
  if (m < 1e-3){ o = vec4(0.); return; }
  // wind-frame noise: long along the wind, fine across it (streaks fan out with the plume), carried downwind
  float X = (d - uSpeed*uT)/uLen*3.0, Y = v/(sig+uThick*2.0)*4.0;
  float warp = (fbm(vec3(X*0.6, Y*0.12, uT*0.15)+uSeed.yzx) - 0.5)*uDetail*6.0;
  float n1 = fbm(vec3(X, Y+warp, uT*0.25)+uSeed);
  float n2 = gnoise(vec3(X*7.0, (Y+warp)*2.5, uT*0.6)+uSeed.zxy);
  float k = clamp(uDetail,0.,1.)*(1.0-0.6*clamp(d/uLen,0.,1.));   // wisps soften as they diffuse downwind
  float wisp = mix(n1, sstep(0.45-0.08*uDetail, 0.8, n1), k);
  float r = m*uAmt*wisp*(0.8+0.4*n2);
  float a = 1.0 - exp(-r*1.2);
  float back = max(-uSinE, 0.);                       // thin backlit spindrift glows
  vec3 col = uAlb*(uSky*uAmb + uKey*(1.0-uAmb)*(0.85 + back*3.0*exp(-r*2.0)));
  o = vec4(col*a, a);
}`,
};

export const VS_SPRITE = `#version 300 es
layout(location=0) in vec2 aCorner;
layout(location=1) in vec4 aA; layout(location=2) in vec4 aB; layout(location=3) in vec4 aC;
uniform vec2 uRes; uniform float uScale;
out vec2 vP; out vec4 vCol; flat out vec4 vB; flat out float vL; flat out float vHw;
void main(){
  vec2 ax=aA.zw*uScale; float L=length(ax);
  vec2 dir = L>1e-5 ? ax/L : vec2(1.,0.);
  vec2 perp = vec2(-dir.y,dir.x);
  float hw = max(aB.x*uScale, 0.75);
  vec2 local = vec2(aCorner.x*(L+hw), aCorner.y*hw);
  vec2 p = aA.xy*uScale + dir*local.x + perp*local.y;
  vP=local; vL=L; vHw=hw; vB=aB; vCol=aC;
  gl_Position = vec4(p.x/uRes.x*2.-1., 1.-p.y/uRes.y*2., 0., 1.);
}`;
export const FS_SPRITE = `#version 300 es
precision highp float;
in vec2 vP; in vec4 vCol; flat in vec4 vB; flat in float vL; flat in float vHw;
uniform int uMix; uniform vec2 uFragmentOffset,uRes; out vec4 o;
`+NOISE+`
void main(){
  vec2 q = vec2(max(abs(vP.x)-vL,0.), vP.y)/vHw;
  float r2 = dot(q,q);
  if (r2>1.0) discard;
  int shape = int(vB.y+0.5);
  float w;
  if (shape==0 || shape==5){
    w = exp(-r2*3.0)*(1.0-smoothstep(0.65,1.0,r2));
    if (shape==5){
      vec3 p=vec3((gl_FragCoord.xy-uFragmentOffset)*0.3,4.7);
      float n=0.65*gnoise(p)+0.35*gnoise(p*2.13+7.1);
      w*=mix(1.0,0.15+1.7*smoothstep(0.25,0.72,n),vB.w);
    }
  }
  else if (shape==1) w = 1.0 - smoothstep(0.15, 1.0, sqrt(r2));
  else if (shape==2){
    float ang = atan(vP.y, vP.x) + vB.z;
    float edge = 0.74 + 0.16*sin(ang*3.0+vB.w) + 0.08*sin(ang*5.0+vB.w*1.7);
    w = 1.0 - smoothstep(edge-0.2, edge, sqrt(r2));
  } else w = exp(-r2*9.0) + 0.25*exp(-r2*2.5);
  vec4 source=vCol;
  // Optional source-temperature variation is born into the air field. It is
  // transported by the solver, never renewed while rendering smoke.
  if(uMix==1&&vB.w>0.){
    vec3 h=vec3(gl_FragCoord.xy/uRes.y*22.2+vB.z*vec2(.73,.51),vB.z*.33);
    float n=.7*gnoise(h)+.3*gnoise(h*2.03+17.7);
    source.z*=max(.05,1.+vB.w*(n-.5)*2.);
  }
  if(uMix==2)o=vec4(vCol.rg,gl_FragCoord.xy/uRes.y*vCol.b)*w;
  else o = uMix==1 ? vec4(source.rgb, vCol.a*w) : vCol*w;
}`;
