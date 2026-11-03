// Shared cache controls operate on local coordinates measured in plate heights.
// Source-motion changes require a new bake; these are playback/fade controls.
export const CACHE_CONTROL=`
uniform float uAge,uStop,uLinger,uFadeRate,uOpacity,uAfter,uMirror,uWind,uDust;
float cacheFade(vec3 p){
 float parcelAge=mix(clamp(max(0.,p.y)/.075,0.,30.),max(0.,uAge),uDust);
 float end=1.-smoothstep(-.15,max(.15,uLinger),uAge-uStop-parcelAge);
 return clamp(exp(-uFadeRate*parcelAge),0.,20.)*end*uOpacity*uAfter;
}
vec3 cachePosition(vec3 p){p.x=(p.x-uWind*max(0.,p.y)/.075)*uMirror;return p;}
`;
export const CACHE_SAMPLE=`
#ifdef GAUSSIAN_CACHE
uniform highp sampler2D uDensityA,uDensityB;uniform float uParamK;
#else
uniform highp sampler3D uDensityA,uDensityB;
#endif
uniform vec3 uMinA,uSizeA,uMinB,uSizeB,uRecoverCamera;uniform float uWeight,uCacheLod,uRecover;
#ifdef GAUSSIAN_CACHE
float cacheRead(highp sampler2D tex,vec3 p,vec3 mn,vec3 size){
 vec3 q=(p-mn)/size;if(any(lessThan(q,vec3(0.)))||any(greaterThan(q,vec3(1.))))return 0.;
 vec4 field=textureLod(tex,q.xy,uCacheLod);float z=(p.z-field.b)/max(field.g,1e-5);
 return max(0.,field.r)*field.a/uParamK*exp(-.5*z*z);
}
#else
float cacheRead(highp sampler3D tex,vec3 p,vec3 mn,vec3 size){
 vec3 q=(p-mn)/size;if(any(lessThan(q,vec3(0.)))||any(greaterThan(q,vec3(1.))))return 0.;
 return textureLod(tex,q,uCacheLod).r;
}
#endif
float cacheDensity(vec3 p){
 vec3 q=cachePosition(p);
 float cosine=1.;
 if(uRecover>.5){
  q.xy=uRecoverCamera.xy+(q.xy-uRecoverCamera.xy)/(1.-q.z/uRecoverCamera.z);
  cosine=abs(uRecoverCamera.z)/length(vec3(q.xy-uRecoverCamera.xy,uRecoverCamera.z));
 }
 return mix(cacheRead(uDensityA,q,uMinA,uSizeA),cacheRead(uDensityB,q,uMinB,uSizeB),uWeight)*cacheFade(p)*cosine;
}
`;
const HEAD=`#version 300 es
precision highp float;precision highp int;precision highp sampler3D;precision highp sampler2D;
in vec2 vUv;layout(location=0) out vec4 o;
`;
export const FS_CACHE_LIGHT=HEAD+CACHE_CONTROL+CACHE_SAMPLE+`
uniform vec3 uDim,uGridMin,uL;uniform vec2 uTiles;uniform float uVox,uK;
void main(){
 ivec2 fc=ivec2(gl_FragCoord.xy),dim=ivec2(uDim.xy),tile=fc/dim;
 ivec3 v=ivec3(fc-tile*dim,tile.y*int(uTiles.x)+tile.x);
 if(v.z>=int(uDim.z)){o=vec4(0.);return;}
 vec3 p=uGridMin+(vec3(v)+.5)*uVox,q=p;float tau=0.,sky=0.,stepSize=uVox;
 for(int i=0;i<32;i++){p+=uL*stepSize;tau+=cacheDensity(p)*stepSize;stepSize*=1.1;}
 stepSize=uVox;
 for(int i=0;i<24;i++){q.y+=stepSize;sky+=cacheDensity(q)*stepSize;stepSize*=1.12;}
 o=vec4(tau*uK,sky*uK,0.,1.);
}`;
export const FS_CACHE_RAY=HEAD+CACHE_CONTROL+CACHE_SAMPLE+`
layout(location=1)out vec4 oF;
uniform sampler2D uLight;uniform vec3 uLightDim,uGridMin,uBoxMin,uBoxMax;uniform vec2 uLightTiles,uRes;
uniform float uLightVox,uTanH,uTanV,uStep,uK,uShadow,uAmb;
uniform vec3 uCam,uRight,uUp,uBack,uL,uKey,uSky,uAlbedo;
vec2 lightSample(vec3 p){
 vec3 c=clamp((p-uGridMin)/uLightVox,vec3(.5),uLightDim-.5);float z=c.z-.5,k=floor(z),f=z-k;
 int a=int(k),b=min(a+1,int(uLightDim.z)-1),tx=int(uLightTiles.x);
 vec2 size=uLightDim.xy*uLightTiles,oa=vec2(a%tx,a/tx)*uLightDim.xy,ob=vec2(b%tx,b/tx)*uLightDim.xy;
 return mix(texture(uLight,(oa+c.xy)/size).xy,texture(uLight,(ob+c.xy)/size).xy,f);
}
float ign(vec2 p){return fract(52.9829189*fract(dot(p,vec2(.06711056,.00583715))));}
void main(){
 vec2 uv=gl_FragCoord.xy/uRes*2.-1.;vec3 dir=normalize(uRight*uv.x*uTanH+uUp*uv.y*uTanV-uBack);
 vec3 ia=1./dir,a=(uBoxMin-uCam)*ia,b=(uBoxMax-uCam)*ia;
 vec3 low=min(a,b),high=max(a,b);float first=max(0.,max(low.x,max(low.y,low.z))),last=min(high.x,min(high.y,high.z));
 if(last<=first){o=vec4(0.);oF=vec4(0.);return;}
 float c=dot(uL,dir),g=.5,phase=mix(1.,pow(1.+g*g,1.5)/pow(1.+g*g-2.*g*c,1.5),.4);
 float t=first+ign(gl_FragCoord.xy)*uStep,T=1.;vec3 sum=vec3(0.);
 for(int n=0;n<900;n++){
  if(t>last||T<.004)break;vec3 p=uCam+dir*t;float rho=cacheDensity(p);
  if(rho>1e-5){
   float alpha=1.-exp(-rho*uK*uStep);vec2 tau=lightSample(p)*uShadow;
   float key=(exp(-tau.x)+.45*exp(-tau.x*.2))/1.45,sky=mix(.3,1.,exp(-tau.y*.5));
   sum+=T*alpha*uAlbedo*(uSky*uAmb*sky+uKey*(1.-uAmb)*key*phase*1.15);T*=1.-alpha;
  }t+=uStep;
 }
 o=vec4(sum,1.-T);oF=vec4(0.,0.,0.,1.-T);
}`;
export const FS_CACHE_SHEET=HEAD+CACHE_CONTROL+`
layout(location=1)out vec4 oF;
uniform sampler2D uTauA,uTauB,uCoeffA0,uCoeffA1,uCoeffB0,uCoeffB1;
uniform vec2 uMinA,uSizeA,uMinB,uSizeB,uPlate,uOrigin;uniform float uWeight,uScale,uAngle,uShadow,uAmb,uShape,uCacheLod;
uniform vec3 uL,uKey,uSky,uAlbedo;
void sheet(vec2 p,vec2 mn,vec2 size,sampler2D tau,sampler2D c0,sampler2D c1,out float thickness,out vec3 rgb0,out vec4 rgb1){
 vec2 q=(p-mn)/size;
 if(any(lessThan(q,vec2(0.)))||any(greaterThan(q,vec2(1.)))){thickness=0.;rgb0=vec3(1.);rgb1=vec4(1.);return;}
 thickness=textureLod(tau,q,uCacheLod).r;rgb0=textureLod(c0,q,uCacheLod).gba;rgb1=textureLod(c1,q,uCacheLod);
}
void main(){
 vec2 p=(vUv*uPlate-uOrigin)/(uPlate.y*uScale),r=vec2(cos(uAngle)*p.x-sin(uAngle)*p.y,sin(uAngle)*p.x+cos(uAngle)*p.y);
 r=cachePosition(vec3(r,0.)).xy;
 float ta,tb;vec3 a0,b0;vec4 a1,b1;
 sheet(r,uMinA,uSizeA,uTauA,uCoeffA0,uCoeffA1,ta,a0,a1);sheet(r,uMinB,uSizeB,uTauB,uCoeffB0,uCoeffB1,tb,b0,b1);
 float tau=mix(ta,tb,uWeight),gain=cacheFade(vec3(r,0.));float alpha=1.-exp(-tau*gain);
 if(alpha<1e-5){o=vec4(0.);oF=vec4(0.);return;}
 float aa=1.-exp(-ta),ab=1.-exp(-tb),wa=(1.-uWeight)*aa,wb=uWeight*ab,total=max(wa+wb,1e-8);
 vec3 c0=(a0*wa+b0*wb)/total;vec4 c1=(a1*wa+b1*wb)/total;
 vec3 L=uL;L.x*=uMirror;vec3 w=abs(L);w/=max(w.x+w.y+w.z,1e-6);
 float direct=w.x*(L.x>0.?c0.x:c0.y)+w.y*(L.y>0.?c0.z:c1.x)+w.z*(L.z>0.?c1.y:c1.z);
 float power=max(0.,uShadow*gain*pow(2.,(uShape-.55)*2.));direct=pow(max(direct,1e-5),power);
 float sky=pow(max(c1.w,.001),uShadow*gain),g=.5,phase=mix(1.,pow(1.+g*g,1.5)/pow(1.+g*g+2.*g*L.z,1.5),.4);
 vec3 color=uAlbedo*(uSky*uAmb*sky+uKey*(1.-uAmb)*direct*phase*1.15);
 o=vec4(color*alpha,alpha);oF=vec4(0.,0.,0.,alpha);
}`;
