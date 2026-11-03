import { gl, program, setUniforms } from '../gl/context.js';
import { VS_FINE_GRAINS, FS_FINE_GRAINS } from '../gl/fine-grains.js';
import { PRESETS } from '../presets.js';
import { app, state } from '../state.js';
import { camAt } from '../io/camera.js';
import { lin, rng, seedMix } from '../util.js';
import { fineCohorts,fineBirthCohorts } from '../sim/fine-cohorts.js';
import { fineSettings,fineParticleFadeUniforms } from '../sim/fines.js';
import {usesBakedSmoke} from '../baked/library.js';

const shader=program(FS_FINE_GRAINS,VS_FINE_GRAINS),vao=gl.createVertexArray();
export function drawFineGrains(target,frame,cam,tint){
  const physical=app.freezeEffectsFrame??frame,world=!!cam;
  gl.bindFramebuffer(gl.FRAMEBUFFER,target.fbo);gl.viewport(0,0,target.w,target.h);gl.bindVertexArray(vao);
  for(const inst of state.instances){
    const pr=PRESETS[inst.preset],G=pr.fineGrains,F=fineSettings(pr.dust?.fines,world),gain=inst.fineGrain??1;
    if(!inst.enabled||usesBakedSmoke(inst)||!G||!F?.analytic||inst.intensity<=0||gain<=0||(world&&!inst.w))continue;
    const t=(physical-inst.frame)/state.fps,rate=(inst.dissipate??1.35)/1.35,linger=(inst.linger??.4)-.4;
    if(t<.15||t>Math.max(5,10/Math.max(.1,rate))+linger)continue;
    const phase=rng(seedMix(inst.seed,0,inst.id*7+89)),ph=Array.from({length:6},()=>phase()*Math.PI*2);
    const particles=world?app.sim3d?.vols.get(inst.id)?.fineParticles:app.sim.fineParticles.get(inst.id);
    const right=world?camAt(inst.frame).right:[1,0,0],color=lin(inst.color).map((v,n)=>v*tint[n]),cohorts=fineCohorts(inst,F.cohorts??5);
    setUniforms(shader,{...fineParticleFadeUniforms(inst,F,t),
      uSeed:{i:inst.seed|0},uId:{i:inst.id},uWorld:{i:world?1:0},uFloor:{i:inst.floor?1:0},uBurstSpread:{i:F.burstSpread?1:0},
      uOrigin:world?inst.w:[inst.x,inst.y,0],uRes:[target.w,target.h],uPlate:[state.plate.w,state.plate.h],
      uCam:cam?.pos??[0,0,0],uRight:cam?.right??[1,0,0],uUp:cam?.up??[0,1,0],uBack:cam?.back??[0,0,1],uTan:cam?[cam.tanH,cam.tanV]:[1,1],
      uPlane:particles?particles.plane:[right[0],right[2]],uPhaseA:ph.slice(0,4),uPhaseB:ph.slice(4),uF:[F.src,F.spread,F.tau,F.wander||0],
      uCohorts:{v4:cohorts.flat()},uBirthCohorts:{v4:(F.birthWidth<1?fineBirthCohorts(inst,cohorts):cohorts).flat()},uFineCount:{i:cohorts.length},uFineResidual:Math.max(.2,Math.min(1,F.cohortWidth??1)),uFineFallResidual:Math.max(.1,Math.min(1,F.cohortFallWidth??F.cohortWidth??1)),uFineBirth:Math.max(.2,Math.min(1,F.birthWidth??1)),uFall:[F.fallKick??.25,F.settle??.02],
      uTime:t,uUnit:(world?inst.wscale:state.plate.h)*inst.scale,uGravity:inst.gravityMul??1,uTurb:Math.min(3,(inst.turb??.2)/.2),
      uMeanFall:world?.75:.8,uSize:G.size,uAlpha:G.alpha*inst.intensity*gain*G.count/(particles?.count??G.count),uFade:G.fade,uRate:rate,uLinger:linger,
      uTint:color,uGround:state.cam?.groundY??-1e10,uShutter:G.shutter/state.fps,uObs:app.sim.obs,
      uParticleMode:{i:particles?1:0},uParticlePos:particles?particles.position.read.tex:app.sim.obs,uParticleVel:particles?particles.velocity.read.tex:app.sim.obs,uParticleStateSize:particles?[particles.w,particles.h]:[1,1],
      ...(particles?particles.airUniforms(t):{uParticleOrigin:[0,0,0],uParticlePlane:[1,0],uParticleUnit:1,uParticleAir:app.sim.obs,uParticleAirMin:[0,0,0],uParticleAirDim:[1,1,1],uParticleAirTiles:[1,1],uParticleAirVox:1,uParticleSheet:{i:0},uParticlePlate:[1,1],uParticleEddy:[0,.05,0,0]}),
    });
    gl.drawArrays(gl.POINTS,0,particles?particles.count:G.count);
  }
  gl.bindVertexArray(null);
}
