import {gl,program,pass} from '../gl/context.js';
import {state} from '../state.js';
import {lin,DEG} from '../util.js';
import {getSmokeCache,usesBakedSmoke,cacheClock,cacheUniforms} from './library.js';
import {gpuFrame} from './gpu.js';
import {FS_CACHE_SHEET} from './shaders.js';
let shader;
export function renderBakedSheets(T,frame,lighting){
 shader??=program(FS_CACHE_SHEET);
 const S=state.scene,az=S.lightAngle*DEG,el=S.lightElev*DEG,L=[Math.sin(az)*Math.cos(el),Math.cos(az)*Math.cos(el),Math.sin(el)];
 gl.enable(gl.BLEND);gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);
 for(const inst of state.instances){
  const clock=cacheClock(inst,frame),cache=getSmokeCache(inst);if(!cache||!usesBakedSmoke(inst)||clock.elapsed<0||inst.intensity<=0)continue;
  const pair=cache.pair('sheet',clock.time),a=gpuFrame(cache,pair.a,'sheet'),b=gpuFrame(cache,pair.b,'sheet'),angle=inst.angle*DEG,c=Math.cos(angle),s=Math.sin(angle);
  pass(shader,T.S,T.w,T.h,{...cacheUniforms(inst,frame),uPlate:[state.plate.w,state.plate.h],uOrigin:[inst.x,state.plate.h-inst.y],uScale:Math.max(.01,inst.scale/(cache.meta.baseScale??2)),uAngle:angle,
   uTauA:a.tau,uTauB:b.tau,uCoeffA0:a.maps[0],uCoeffA1:a.maps[1],uCoeffB0:b.maps[0],uCoeffB1:b.maps[1],uMinA:a.meta.min,uSizeA:a.meta.size,uMinB:b.meta.min,uSizeB:b.meta.size,uWeight:pair.weight,
   uL:[c*L[0]-s*L[1],s*L[0]+c*L[1],L[2]],uAlbedo:lin(inst.color).map(x=>x*(cache.meta.albedoScale??1)),uKey:lighting.key,uSky:lighting.sky,uAmb:lighting.amb,uShadow:S.shadow,uShape:S.shape2d??.55,uCacheLod:(S.opacitySoft2d??.6)*.4});
 }
}
