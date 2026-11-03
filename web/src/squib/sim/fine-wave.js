// A compact irregular Fourier field shared by CPU particles, the inverse
// density map and GPU grains. Mixed directions avoid the obvious repeating
// two-wave curls; total spectral power retains the former displacement RMS.
const modes=[
  [.446,19,7,.31,1,0], [.397,-11,29,-.43,0,1],
  [.347,37,-17,.67,1.7,.37], [.238,53,41,-.81,.43,1.31],
  [.178,-71,23,1.07,1.13,-.71], [.119,97,-83,-1.31,-.61,1.73],
];
export function fineWave(a,b,p,q,time){
  let sum=0;
  for(const [weight,x,y,speed,u,v]of modes)sum+=weight*Math.sin(x*a+y*b+speed*time+u*p+v*q);
  return sum;
}
// Return value and analytic time derivative. da/db include the trajectories
// through previous shears, so GPU motion blur follows the same carrier field.
export const FINE_WAVE_GLSL=`
vec2 fineWaveMotion(float a,float b,float da,float db,float p,float q,float time){
  vec2 result=vec2(0.);
`+modes.map(([w,x,y,s,u,v])=>`  {float t=${x.toFixed(1)}*a+${y.toFixed(1)}*b+${s}*time+${u.toFixed(2)}*p+${v.toFixed(2)}*q;
   result+=${w}*vec2(sin(t),cos(t)*(${x.toFixed(1)}*da+${y.toFixed(1)}*db+${s}));}`).join('\n')+`
  return result;
}
float fineWaveValue(float a,float b,float p,float q,float time){return fineWaveMotion(a,b,0.,0.,p,q,time).x;}
`;
