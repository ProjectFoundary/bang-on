import { integrateParticle } from '../sim/particles.js';
import { gauss, rng, seedMix } from '../util.js';

// Fine ballistic dust adds granular motion below the fluid grid. Evaluate each
// grain from its birth state, so playback, scrubbing and export use the same path.
export function dustGrains(inst, pr, seconds, unit, world=false){
  const G=pr.grains;if(!G||seconds<0||seconds>G.life+.3)return [];
  const out=[],size=unit*inst.scale,gravity=(inst.gravityMul??1)*G.gravity*size;
  for(let n=0;n<Math.round(G.count*inst.intensity);n++){
    const r=rng(seedMix(inst.seed,n,inst.id*7+61));
    const birth=r()*.18,t=seconds-birth,life=G.life*(.4+.6*r());
    const x=gauss(r)*.024*size,y=gauss(r)*.015*size,z=gauss(r)*.025*size;
    const vx=gauss(r)*.09*size,vy=(r()-.6)*.3*size,vz=gauss(r)*.06*size;
    const radius=G.size*size*(.4+1.4*r()),shade=.55+.5*r(),drag=G.drag*(.6+1.2*r());
    if(t<0||t>life)continue;
    const p=world?{x:inst.w[0]+x,y:inst.w[1]-y,z:inst.w[2]+z,vx,vy:-vy,vz,age:0}
                 :{x:inst.x+x,y:inst.y+y,vx,vy,age:0};
    integrateParticle(p,t,drag,world?-gravity:gravity,world);
    if(inst.floor&&(world?p.y<inst.w[1]:p.y>inst.y))continue;
    out.push({...p,size:radius,shade,alpha:G.alpha*Math.min(1,t/.04,(life-t)/.25)});
  }
  return out;
}
