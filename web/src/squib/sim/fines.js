import { gauss, lin, rng, seedMix } from '../util.js';
import { fineWave } from './fine-wave.js';
import { fineCohorts,fineBirthCohorts } from './fine-cohorts.js';

export const fineSettings=(F,world)=>({...F,
  ...(world&&F?.fallKick3d!=null?{fallKick:F.fallKick3d}:{}),...F?.[world?'volume':'sheet']});

// Optical ageing changes mass without changing trajectories. Normal Dissipate
// and Linger controls retime both populations.
export function fineOpticalAge(inst,t){
 const rate=Math.max(0,(inst.dissipate??1.35)/1.35),linger=(inst.linger??.4)-.4;
 // Shorter linger accelerates ageing continuously, rather than jumping the
 // optical clock immediately after the protected initial burst.
 return t<=.2?t:.2+Math.max(0,t-.2-Math.max(0,linger))*rate*(1+Math.max(0,-linger)/.4);
}
export function fineParticleFadeUniforms(inst,F,t){
 const [begin,end]=F.particleFastFade??[0,0];
 return {uDustFade:[begin,end,F.particleFallModes?.[0]??0,fineOpticalAge(inst,t)],uDustSeed:{i:inst.seed|0}};
}

// Optional authored extinction mass for a born-once particle cloud. This is
// independent of the rendered image; injection, reveal and fade use physical age.
export function fineParticleEnvelope(inst,F,t,world){
  const curve=F.particleMassCurve;if(!curve)return null;
  if(t<0)return 0;
  const age=fineOpticalAge(inst,t);
  if(age>=curve.at(-1)[0])return 0;
  let value=curve[0][1];
  for(let i=1;i<curve.length;i++)if(age<curve[i][0]){
    const a=curve[i-1],b=curve[i];value=a[1]+(b[1]-a[1])*(age-a[0])/(b[0]-a[0]);break;
  }
  return Math.max(0,value)*F.amount*inst.intensity*F.r**(world?3:2)*(world?.880028*1.16:.956609*1.053333);
}

// Each shear changes one coordinate using only its perpendicular coordinates.
// Their composition preserves area/volume while providing a smooth, seeded
// eddy map that can be evaluated at any time without a CPU simulation history.
export function stirFine([x,y,z], a, t, ph, world=true){
  const wave=(u,v,p,q)=>fineWave(u,v,p,q,t);
  x+=a*wave(y,world?z:0,ph[0],ph[1]);
  y+=a*.75*wave(world?z:0,x,ph[2],ph[3]);
  if(world)z+=a*wave(x,y,ph[4],ph[5]);
  return [x,y,z];
}

// Suspended dust is one born cloud, not a succession of fresh clouds painted
// over its old locations. Drag slows its initial fall; the fine material then
// drifts with air and settles slowly. Expansion is compensated in density; an
// extinction envelope brings fines out of the dense burst before fading them.
export function fineCloud(inst, F, t, unit, world=false, right=[1,0,0], samples=true){
  F=fineSettings(F,world);
  const rate=(inst.dissipate??1.35)/1.35,linger=(inst.linger??.4)-.4;
  const end=Math.max(5,10/Math.max(.1,rate))+linger;
  if(!F?.analytic||inst.intensity<=0||t<0||t>end)return {parcels:[],offset:[0,0,0]};
  const meanFall=world ? .75 : .8;
  const kick=F.fallKick??.25;
  const H=unit*inst.scale,fall=kick*.65*(1-Math.exp(-t/.65))*(inst.gravityMul??1),terminal=((F.settle??.02)*t+.01*Math.max(0,t-3))*(inst.gravityMul??1);
  const tail=Math.max(0,t-3);
  const drift=-.16*.4*(1-Math.exp(-t/.4))+.012*t+.026*tail;
  const offset=world?[right[0]*drift*H,-(fall*meanFall+terminal)*H,right[2]*drift*H]:[drift*H,(fall*meanFall+terminal)*H,0];
  const grow=1+.25*t,fade=Math.min(1,(end-t)/2)*Math.exp(-rate*(Math.max(0,t-linger-.2)/4.0+Math.max(0,t-linger-3)/5.0)),ramp=Math.max(0,Math.min(1,(t-.1)/.5));
  const blend=Math.max(0,Math.min(1,(t-.9)/2.1));
  const mix=blend*blend*(3-2*blend);
  const emergence=1+.6*Math.exp(-(((t-1)/.65)**2));
  const amount=emergence*(world ? (.95+1.75*mix)*1.0666667:.95+.75*mix)*(world?1-.3*mix+.3*Math.min(1,tail/3):1-.25*mix);
  const spread=F.tau*(1-Math.exp(-t/F.tau))*(F.burstSpread?1:(.4+.75*(1-Math.exp(-t/2.5)))),col=lin(inst.color),parcels=[];
  const phase=rng(seedMix(inst.seed,0,inst.id*7+89)),ph=Array.from({length:6},()=>phase()*Math.PI*2);
  const residual=Math.max(.2,Math.min(1,F.cohortWidth??1)),cohortGain=Math.sqrt(1-residual*residual),cohorts=fineCohorts(inst,F.cohorts??5);
  const birthResidual=Math.max(.2,Math.min(1,F.birthWidth??1)),birthGain=Math.sqrt(1-birthResidual*birthResidual);
  const births=birthResidual<1?fineBirthCohorts(inst,cohorts):cohorts;
  const fallResidual=Math.max(.1,Math.min(1,F.cohortFallWidth??residual)),fallGain=Math.sqrt(1-fallResidual*fallResidual);
  const clustered=residual<1||fallResidual<1||birthResidual<1;
  const eddy=(F.wander||0)*Math.min(3,(inst.turb??.2)/.2)*(1-Math.exp(-t/.45));
  // Overlap more kernels at lower density so the haze stays connected as
  // individual parcels expand. The finite cloud still has one seeded birth.
  const count=samples?F.n*(world?4:2):0,dimension=world?3:2;
  for(let n=0;n<count;n++){
    const q=rng(seedMix(inst.seed,n,inst.id*7+79));
    const cohort=cohorts[n%cohorts.length];
    const born=births[n%cohorts.length];
    const px=(gauss(q)*birthResidual+born[0]*birthGain)*F.src,pz=(gauss(q)*birthResidual+born[2]*birthGain)*F.src,py=(gauss(q)*birthResidual+born[1]*birthGain)*F.src*.7;
    const vx=(gauss(q)*residual+cohort[0]*cohortGain)*F.spread*(world?1.25:1),vz=(gauss(q)*residual+cohort[2]*cohortGain)*F.spread*(world?1.25:1);
    const local=[px+vx*spread,py+fall*Math.max(-1.7,Math.min(1.7,(gauss(q)*fallResidual+cohort[1]*fallGain)*.8)),pz+vz*spread];
    const stirred=stirFine(local,eddy,t,ph,world);
    const x=stirred[0]+drift,y=stirred[1]+fall*meanFall+terminal,z=stirred[2];
    const radius=F.r*H*(.6+.8*q())*grow;
    const d=F.amount*inst.intensity*ramp*fade/count/Math.pow(grow,dimension)*amount*(clustered?cohort[3]*cohorts.length:1);
    const p=world?[inst.w[0]+(right[0]*x-right[2]*z)*H,inst.w[1]-y*H,inst.w[2]+(right[2]*x+right[0]*z)*H]
      :[inst.x+x*H,inst.y+y*H,0];
    const move=world?[(right[0]*(x-px)-right[2]*(z-pz))*H,-(y-py)*H,(right[2]*(x-px)+right[0]*(z-pz))*H]
      :[(x-px)*H,(y-py)*H,0];
    // The breakup rides the carrier air. Initial ballistic dispersion widens
    // the parcel distribution but should not stretch microscopic texture into
    // the same few large patches for the rest of the shot.
    const ax=stirred[0]-local[0]+drift,ay=stirred[1]-local[1]+fall*meanFall+terminal,az=stirred[2]-local[2];
    const airMove=world?[(right[0]*ax-right[2]*az)*H,-ay*H,(right[2]*ax+right[0]*az)*H]:[ax*H,ay*H,0];
    parcels.push({p,move,airMove,r:radius,d,col,v:[0,0,0],vw:0,curl:0,turb:0,rate:0,linger:0});
  }
  // Approximate expectation of the same birth law, convolved with the small
  // density kernel. Expansion conserves its total integral in 2D and 3D.
  const kernel=F.r*grow,variance=kernel*kernel*(world?.156924:.161615);
  const sx=Math.sqrt((F.src*birthResidual)**2+(F.spread*(world?1.25:1)*spread*residual)**2+variance);
  const sy=Math.sqrt((F.src*.7*birthResidual)**2+(fall*.8*fallResidual)**2+variance);
  const integral=world?.880028*1.16:.956609*1.053333;
  const mass=F.amount*inst.intensity*ramp*fade*amount*F.r**dimension*integral;
  return {parcels,offset,field:{uFineAge:t,uFineEddy:eddy,uFineOffset:[drift,fall*meanFall+terminal,0],
    uFineTail:[fall*1.7,Math.sqrt((F.src*.7)**2+variance)],uFineSigma:[sx,sy,sx],uFinePeak:mass/(Math.pow(2*Math.PI,dimension/2)*sx*sy*(world?sx:1)),
    uFinePhaseA:ph.slice(0,4),uFinePhaseB:ph.slice(4),uFineColor:col,uFineClustered:clustered?1:0,uFineCount:{i:cohorts.length},
    uFineCenters:{v4:cohorts.flatMap((c,n)=>[births[n][0]*F.src*birthGain+c[0]*cohortGain*F.spread*(world?1.25:1)*spread,births[n][1]*F.src*.7*birthGain+c[1]*fallGain*fall*.8,births[n][2]*F.src*birthGain+c[2]*cohortGain*F.spread*(world?1.25:1)*spread,c[3]])}}};
}
