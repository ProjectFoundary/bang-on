// Expected density of the born-once Gaussian cloud, warped by the same
// area/volume-preserving shears as its sub-grid grains. Inverting shears in
// reverse order avoids a coarse set of spherical parcel footprints.
import { FINE_WAVE_GLSL } from '../sim/fine-wave.js';
export const FINE_DISTRIBUTION=`
uniform vec3 uFineSigma,uFineOffset,uFineColor;
uniform vec4 uFinePhaseA;
uniform vec2 uFinePhaseB,uFineTail;
uniform float uFineAge,uFineEddy,uFinePeak;
uniform float uFineClustered;uniform int uFineCount;uniform vec4 uFineCenters[32];
`+FINE_WAVE_GLSL+`
float fineWave(float a,float b,float p,float q){
  return fineWaveValue(a,b,p,q,uFineAge);
}
vec4 fineDensity(vec3 local,bool world,out vec3 air){
  vec3 p=local-uFineOffset;
  if(world)p.z-=uFineEddy*fineWave(p.x,p.y,uFinePhaseB.x,uFinePhaseB.y);
  p.y-=uFineEddy*.75*fineWave(world?p.z:0.,p.x,uFinePhaseA.z,uFinePhaseA.w);
  p.x-=uFineEddy*fineWave(p.y,world?p.z:0.,uFinePhaseA.x,uFinePhaseA.y);
  air=local-p;
  vec3 q=p/uFineSigma;
  float power=q.x*q.x+q.y*q.y+(world?q.z*q.z:0.);
  float tail=max(0.,abs(p.y)-uFineTail.x)/max(uFineTail.y,1e-5);
  float density=exp(-.5*power);
  if(uFineClustered>.5){
    density=0.;
    for(int i=0;i<32;i++){
      if(i>=uFineCount)break;
      vec3 q=(p-uFineCenters[i].xyz)/uFineSigma;
      density+=uFineCenters[i].w*exp(-.5*(q.x*q.x+q.y*q.y+(world?q.z*q.z:0.)));
    }
  }
  float d=uFinePeak*density*exp(-.5*tail*tail);
  return vec4(uFineColor*d,d);
}
`;
