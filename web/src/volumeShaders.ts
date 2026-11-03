/** Finite XYZ obstacles, porous moving-body drag, and depth ray visibility.
 * Native vendor shader strings stay intact; source guards fail on incompatible updates. */
import {smokeParticleShader} from './smokeParticleCollision';
// Mask grids have their own world transform. Scalar density can be finer than
// velocity, and lighting also changes its working grid inside the same pass.
const maskHeader=`
uniform sampler2D uVFXSolid,uVFXHoldout,uVFXMotion;
uniform float uVFXEnabled,uVFXRay;
uniform vec3 uVFXMaskDim,uVFXMaskBoxMin;
uniform vec2 uVFXMaskTiles;
uniform float uVFXMaskVox;
vec4 vfxFieldWorld(sampler2D field,vec3 world){
  if(uVFXMaskVox<=0.)return vec4(0.);
  ivec3 v=ivec3(floor((world-uVFXMaskBoxMin)/uVFXMaskVox));
  if(any(lessThan(v,ivec3(0)))||any(greaterThanEqual(v,ivec3(uVFXMaskDim))))return vec4(0.);
  int tx=int(uVFXMaskTiles.x);
  ivec2 at=ivec2((v.z%tx)*int(uVFXMaskDim.x)+v.x,(v.z/tx)*int(uVFXMaskDim.y)+v.y);
  return texelFetch(field,at,0);
}
float vfxSolidWorld(vec3 world){return uVFXEnabled>.5?vfxFieldWorld(uVFXSolid,world).r:0.;}
`;
const collision=maskHeader+`
uniform vec3 uVFXSource;
float volumeSolid(ivec3 v){return vfxSolidWorld(uBoxMin+(vec3(v)+.5)*uVox);}
vec4 volumeMotion(ivec3 v){return vfxFieldWorld(uVFXMotion,uBoxMin+(vec3(v)+.5)*uVox);}
float solidV(ivec3 v){return (v.y<0||uBoxMin.y+(float(v.y)+.5)*uVox<uGround)?1.:volumeSolid(v);}
vec3 volumeTrace(vec3 a,vec3 b){
  if(uVFXEnabled<.5)return b;
  vec3 delta=b-a;float len=length(delta);int n=min(256,max(1,int(ceil(len/.4))));
  if(len>102.4)delta*=102.4/len;
  vec3 previous=a;
  for(int j=1;j<=256;j++){if(j>n)break;vec3 p=a+delta*(float(j)/float(n));if(solidV(ivec3(floor(p)))>.5)return previous;previous=p;}
  return a+delta;
}
bool volumeBirthVisible(vec3 p){
  if(uVFXEnabled<.5)return true;
  vec3 delta=p-uVFXSource;int n=min(256,max(1,int(ceil(length(delta)/.4))));
  for(int j=0;j<=256;j++){if(j>n)break;if(volumeSolid(ivec3(floor(uVFXSource+delta*(float(j)/float(n)))))>.5)return false;}
  return true;
}
`;
function once(source:string,needle:string,replacement:string){if(!source.includes(needle))throw new Error(`Volume shader source mismatch: ${needle.slice(0,70)}`);return source.replace(needle,replacement);}
export function volumeShader(name:string,input:string):string{
  if(name.startsWith('smokeParticle'))return smokeParticleShader(name,input,collision,maskHeader);
  if(!input.includes('float solidV('))return input;
  let source=once(input,'// trilinear: ',collision+'\n// trilinear: ');
  source=once(source,'float solidV(ivec3 v){ return (v.y<0 || uBoxMin.y+(float(v.y)+0.5)*uVox < uGround) ? 1.0 : 0.0; }','// finite XYZ solidV supplied by Bang On');
  source=source.replaceAll('return x-dt*v;','return volumeTrace(x,x-dt*v);');
  if(name==='splat'){
    source=once(source,'vec4 c=texelFetch(uSrc, ivec2(gl_FragCoord.xy), 0); vec3 p=vec3(v)+0.5;',
      'if(solidV(v)>.5){o=vec4(0.);return;} vec4 c=texelFetch(uSrc,ivec2(gl_FragCoord.xy),0);vec3 p=vec3(v)+.5;bool checked=false;bool allowed=true;');
    source=once(source,'if (q>1.0) continue;','if(q>1.)continue;if(!checked){allowed=volumeBirthVisible(p);checked=true;}if(!allowed)continue;');
  }
  if(name==='forces'){
    source=once(source,'uniform float uDt, uBuoy, uWindK, uTime, uTurbK, uCurlK, uNoiseK,uCurlPotential; uniform vec3 uWind;',
      'uniform float uDt,uBuoy,uWindK,uTime,uTurbK,uCurlK,uNoiseK,uCurlPotential;uniform vec3 uWind;uniform sampler2D uVFXWindHeat;uniform vec3 uVFXWind;uniform float uVFXWindK,uVFXWindDispersion,uVFXWindAge,uVFXWindUnit,uVFXWindSeed,uVFXWindHasFire;');
    source=once(source,'V.xyz += uDt*uWindK*(uWind - V.xyz);',`
      vec3 air=uVFXWindK>0.?uVFXWind/uVox:uWind;float response=uVFXWindK>0.?uVFXWindK:uWindK;
      if(uVFXWindK>0.&&uVFXWindDispersion>0.){
        float late=smoothstep(.45,1.25,uVFXWindAge),cool=1.-smoothstep(.15,.75,max(V.w,0.));
        if(uVFXWindHasFire>.5){float heat=scalarField(uVFXWindHeat,vec3(v)+.5).r/(max(d,0.)+1e-5);cool*=1.-smoothstep(.8,1.5,max(heat,0.));}
        if(late*cool>0.){
          vec3 axis=normalize(uVFXWind),world=uBoxMin+(vec3(v)+.5)*uVox;
          vec3 q=world/max(.01,uVFXWindUnit*.2)+uVFXWindSeed;
          vec3 eddy=curlPotential(q,uTime*.55);eddy-=axis*dot(eddy,axis)*.7;
          air+=eddy*length(uVFXWind)/uVox*.3*uVFXWindDispersion*late*cool;
        }
      }
      V.xyz+=uDt*response*(air-V.xyz);`);
    source=once(source,'o=V*(1.0-solidV(v));','vec4 body=volumeMotion(v);V.xyz=mix(V.xyz,body.xyz,1.-exp(-max(body.w,0.)*uDt));o=V*(1.-solidV(v));');
  }
  if(name==='divergence')source=once(source,'if (solidV(n)>0.5) return 0.;','if(solidV(n)>.5)return volumeMotion(n)[a];');
  if(name==='gradient')source=once(source,'o=V*(1.0-solidV(v));',`
    for(int a=0;a<3;a++){ivec3 d=ivec3(0);d[a]=1;
      if(solidV(v+d)>.5)V[a]=min(V[a],volumeMotion(v+d)[a]);
      if(solidV(v-d)>.5)V[a]=max(V[a],volumeMotion(v-d)[a]);}
    o=V*(1.-solidV(v));`);
  if(name==='opticalReduce')source=once(source,'if(valid(v))sum+=','if(valid(v)&&solidV(v)<.5)sum+=');
  if(name==='lightVol'){
    source=once(source,'tau+=max(fieldSample(uDye,p).a,0.)*st;','if(volumeSolid(ivec3(floor(p)))>.5){tau+=80.;break;}tau+=max(fieldSample(uDye,p).a,0.)*st;');
    source=once(source,'up+=max(fieldSample(uDye,q).a,0.)*st;','if(volumeSolid(ivec3(floor(q)))>.5){up+=80.;break;}up+=max(fieldSample(uDye,q).a,0.)*st;');
  }
  if(name==='raymarch'){
    source=once(source,'vec3 acc=vec3(0.), accF=vec3(0.); float Tv=1.0;','vec3 acc=vec3(0.),accF=vec3(0.);float Tv=1.,volumeAlpha=0.,volumeVisibility=1.;');
    source=once(source,'float t=ta+ign(gl_FragCoord.xy)*uStep;',`bool volumeHeld=false;
      if(uVFXRay>.5&&uVFXExactCG>.5&&dir.z<-.00001){
        float cameraDepth=texture(uVFXCGDepth,vec2(ndc.x*.5+.5,.5-ndc.y*.5)).r;
        tb=min(tb,(-cameraDepth-uBoxMin.z-ro.z*uVox)/(dir.z*uVox));
      }
      float t=ta+ign(gl_FragCoord.xy)*uStep;`);
    source=once(source,'vec3 p=ro+dir*t;',`vec3 p=ro+dir*t;
      if(uVFXRay>.5&&inside(ivec3(floor(p)))){float coverage=vfxFieldWorld(uVFXHoldout,uBoxMin+p*uVox).r;
        if(coverage>.002&&!volumeHeld){volumeVisibility*=1.-coverage;volumeHeld=true;if(volumeVisibility<.004)break;}
        if(coverage<=.002)volumeHeld=false;}`);
    source=once(source,'acc+=Tv*a*','acc+=volumeVisibility*Tv*a*');
    source=once(source,'accF+=Tv*a*em;','accF+=volumeVisibility*Tv*a*em;volumeAlpha+=volumeVisibility*Tv*a;');
    source=once(source,'o=vec4(acc,1.0-Tv); oF=vec4(accF,1.0-Tv);','o=vec4(acc,volumeAlpha);oF=vec4(accF,volumeAlpha);');
    // Rays follow the original camera homography, evaluated inside the march:
    // no clipped reference render is stretched to cover later plate borders.
    source=once(source,'uniform vec2 uRes;','uniform sampler2D uVFXCGDepth;uniform float uVFXExactCG;uniform vec3 uVFXH0,uVFXH1,uVFXH2;uniform float uVFXCamera;uniform vec2 uRes;');
    source=once(source,'vec2 ndc=gl_FragCoord.xy/uRes*2.0-1.0;',`vec2 ndc=gl_FragCoord.xy/uRes*2.-1.;if(uVFXCamera>.5){vec3 q=vec3(gl_FragCoord.x/uRes.x,1.-gl_FragCoord.y/uRes.y,1.);vec3 p=vec3(dot(uVFXH0,q),dot(uVFXH1,q),dot(uVFXH2,q));ndc=vec2(p.x/p.z*2.-1.,1.-p.y/p.z*2.);}`);
  }
  return source;
}

/** Pairwise finite-volume exchange of cold smoke, not an image-space blur.
 * Symmetric face coefficients conserve premultiplied density/colour and all
 * material attributes. A solid face has zero flux; heat is transported with
 * exactly the same coefficients, so mixing cannot manufacture bright fire. */
export function windSmokeMixShader(head:string){return volumeShader('windSmokeMix',head+`
uniform sampler2D uMixSource,uMixDye,uMixHeat;
uniform vec3 uMixDiffusion;
uniform float uMixDt,uMixFade,uMixHasFire;
float coolAt(ivec3 v){
  if(uMixHasFire<.5)return 1.;
  float density=max(F(uMixDye,v).a,0.),heat=max(F(uMixHeat,v).r,0.)/(density+1e-5);
  // Native fire emission is zero below0.8; keep bright flame cores (>=1.5)
  // intact while their cooled soot can exchange with surrounding air.
  return 1.-smoothstep(.8,1.5,heat);
}
void main(){
  ivec3 v=vox();if(!valid(v)||solidV(v)>.5){o=vec4(0.);return;}
  vec4 center=F(uMixSource,v),mixed=center;float cold=coolAt(v);
  vec3 weights=uMixDiffusion*uMixDt/(uVox*uVox);
  weights*=min(1.,.8/max(1e-8,2.*(weights.x+weights.y+weights.z)));
  for(int axis=0;axis<3;axis++)for(int side=-1;side<=1;side+=2){
    ivec3 delta=ivec3(0);delta[axis]=side;ivec3 n=v+delta;
    if(!inside(n)||solidV(n)>.5)continue;
    float exchange=weights[axis]*min(cold,coolAt(n));mixed+=exchange*(F(uMixSource,n)-center);
  }
  // Material age is signed (negative during the authored linger grace). The
  // convex exchange preserves nonnegative density without clamping that age.
  o=mixed*exp(-uMixFade*uMixDt*cold);
}`);}
