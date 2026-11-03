import { gauss,rng,seedMix } from '../util.js';

// A burst sheds unequal dust masses with different ballistic velocities. Seeded
// overlapping cohorts retain that heterogeneity as the cloud spreads. Weighted
// centering and variance normalization preserve the unclipped velocity moments.
export function fineCohorts(inst,count=5){
  count=Math.max(2,Math.min(32,Math.round(count)));
  const random=rng(seedMix(inst.seed,0,inst.id*7+127));
  let weights=[.28,.23,.20,.17,.12];
  if(count!==5){
    const mass=rng(seedMix(inst.seed,0,inst.id*7+131));
    weights=Array.from({length:count},()=>.5-Math.log(Math.max(1e-6,mass())));
    const total=weights.reduce((s,w)=>s+w,0);weights=weights.map(w=>w/total);
  }
  const rows=weights.map(w=>[gauss(random),gauss(random),gauss(random),w]);
  for(let axis=0;axis<3;axis++){
    const mean=rows.reduce((s,c)=>s+c[axis]*c[3],0);
    const variance=rows.reduce((s,c)=>s+(c[axis]-mean)**2*c[3],0);
    for(const c of rows)c[axis]=(c[axis]-mean)/Math.sqrt(Math.max(variance,1e-5));
  }
  return rows;
}

// Birth pockets are separate from velocity cohorts. With enough cohorts,
// weighted orthogonalization prevents a texture change from adding a spurious
// position/velocity covariance and inflating the whole burst's variance.
export function fineBirthCohorts(inst,velocity){
  const random=rng(seedMix(inst.seed,0,inst.id*7+149)),weights=velocity.map(c=>c[3]),basis=[];
  const dot=(a,b)=>a.reduce((sum,x,k)=>sum+x*b[k]*weights[k],0);
  const orthogonal=a=>{
    const mean=a.reduce((sum,x,k)=>sum+x*weights[k],0);a=a.map(x=>x-mean);
    if(velocity.length>=8)for(const q of basis){const projection=dot(a,q);a=a.map((x,k)=>x-projection*q[k]);}
    const scale=Math.sqrt(Math.max(dot(a,a),1e-12));return a.map(x=>x/scale);
  };
  if(velocity.length>=8)for(let axis=0;axis<3;axis++)basis.push(orthogonal(velocity.map(c=>c[axis])));
  const axes=[];
  for(let axis=0;axis<3;axis++){const a=orthogonal(velocity.map(()=>gauss(random)));axes.push(a);basis.push(a);}
  return velocity.map((c,n)=>[axes[0][n],axes[1][n],axes[2][n],c[3]]);
}
