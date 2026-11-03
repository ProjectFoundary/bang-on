// Analytic gradient of the same quintic gradient noise used by the solver.
// Curl of three scalar potentials supplies all three components of a rotating
// field without the vertical squash of the legacy component-noise force.
export const CURL_NOISE3=`
vec3 gradientNoise(vec3 p){
  vec3 i=floor(p),f=fract(p),u=f*f*f*(f*(f*6.-15.)+10.);
  vec3 du=30.*f*f*(f*(f-2.)+1.),gradient=vec3(0.);
  for(int n=0;n<8;n++){
    vec3 k=vec3(n&1,(n>>1)&1,(n>>2)&1),g=g3(i+k),w=mix(1.-u,u,k),dw=mix(-du,du,k);
    float d=dot(g,f-k);
    gradient+=g*(w.x*w.y*w.z)+d*vec3(dw.x*w.y*w.z,w.x*dw.y*w.z,w.x*w.y*dw.z);
  }
  return gradient*.9;
}
vec3 curlPotential(vec3 p,float t){
  vec3 phase=p+vec3(0.,0.,t);
  vec3 a=gradientNoise(phase+vec3(17.1,5.3,2.7)),b=gradientNoise(phase+vec3(3.9,31.7,9.2)),c=gradientNoise(phase+vec3(8.7,1.3,47.9));
  return vec3(c.y-b.z,a.z-c.x,b.x-a.y);
}
`;
