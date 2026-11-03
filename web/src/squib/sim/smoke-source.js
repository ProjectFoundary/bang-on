// Preset-authored source air temperature, in the existing solver units. Times
// are seconds since emission starts. Only newly emitted air reads this curve;
// older smoke keeps its simulated temperature and motion.
function curveValue(curve,age,fallback){
  if(!curve?.length)return fallback;
  if(age<=curve[0][0])return curve[0][1];
  for(let n=1;n<curve.length;n++){
    const [t,b]=curve[n],[start,a]=curve[n-1];
    if(age<=t){const u=(age-start)/(t-start),w=u*u*(3-2*u);return a+(b-a)*w;}
  }
  return curve.at(-1)[1];
}
export function smokeSourceTemperature(smoke,mode,age){
  const settings=smoke[mode];
  return curveValue(settings?.sourceTempCurve,age,settings?.sourceTemp??smoke.temp);
}
export function smokeDraftStrength(smoke,mode,age){
  const settings=smoke[mode];
  return curveValue(settings?.draftKCurve,age,settings?.draftK??smoke.draft?.k??0);
}
