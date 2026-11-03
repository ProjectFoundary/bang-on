import { F16, P, Sprites,drawSprites, freeFBO, gl, makeFBO, makeFBO2, pass, sprFx } from '../gl/context.js';
import { PRESETS } from '../presets.js';
import { app, state } from '../state.js';
import { DEG, FLOW_PERIOD, lin } from '../util.js';
import { driftUniforms, pushDriftGrains, pushSnowFlakes } from './snow.js';
import { is3D } from '../sim/Sim3D.js';
import { camAt, project } from '../io/camera.js';
import { matteTex } from '../io/plate.js';
import { dustGrains } from './dust.js';
import { detailNoise } from '../gl/detail-noise.js';
import { drawFineGrains } from './fine-grains.js';
import {renderBakedSheets} from '../baked/render2d.js';
import {usesBakedSmoke} from '../baked/library.js';
import {cachedParticles} from '../baked/particles.js';

/* weight of flow-noise field A at sim time t: zero just after it resets, full half a period later (B gets the rest) */
const flowWeight=t=>{ const f=(t/FLOW_PERIOD)%1; return 1-Math.abs(2*f-1); };

/* fire chroma, measured from a reference fireball (it holds this hue at every brightness) */
const FIRE_COL='#ff8f4d';
const frontDebris=new Sprites();

/* =========================================================================
   Layer rendering (smoke / debris / sparks / snow) at any resolution
   ========================================================================= */
export function makeTargets(w,h,withExport){
  const [S,F]=makeFBO2(w,h,F16());   // smoke (with fire) and fire emission alone, written in one pass
  const t={w,h,S,F,D:makeFBO(w,h,F16()),DB:makeFBO(w,h,F16()),DF:makeFBO(w,h,F16()),K:makeFBO(w,h,F16()),N:makeFBO(w,h,F16())};
  if (withExport){ t.C=makeFBO(w,h,F16()); t.C8=makeFBO(w,h,[gl.RGBA8,gl.RGBA,gl.UNSIGNED_BYTE]); }
  return t;
}
export function freeTargets(t){ if(!t) return; for (const k of ['S','F','D','DB','DF','K','N','C','C8']) freeFBO(t[k]); }

/* held: the holdout matte for this frame is on matteTex (Scene > Matte: Holdout or Collider + holdout) */
export function renderLayers(T, frame, held){
  const S=state.scene, W=state.plate.w, H=state.plate.h, scale=T.w/W, fps=state.fps;
  const la=S.lightAngle*DEG, le=S.lightElev*DEG;
  const key=lin(S.lightCol), sky=lin(S.ambCol), amb=S.ambient;
  const lighting={key, sky, amb, sinE:Math.sin(le), fire:lin(FIRE_COL)};
  const vol=is3D(), parts=vol ? app.sim3d.parts : app.sim.parts;
  for(const inst of state.instances)if(usesBakedSmoke(inst)&&PRESETS[inst.preset].kind==='impact')parts.set(inst.id,cachedParticles(inst,frame,vol));
  // 3D particles are in world units: project position, velocity (for motion blur) and size to plate px
  const cam3=vol ? camAt(frame) : null;
  const scr=p=>{
    if (!vol) return p;
    const a=project([p.x,p.y,p.z],cam3), e=1e-3, b=a && project([p.x+p.vx*e,p.y+p.vy*e,p.z+p.vz*e],cam3); if (!a||!b) return null;
    const k=H/(2*a[2]*cam3.tanV);
    return {...p, x:a[0], y:a[1], vx:(b[0]-a[0])/e, vy:(b[1]-a[1])/e, size:p.size*k, k3:k,depth:a[2]};
  };
  const tint=[0,1,2].map(i=>sky[i]*amb+key[i]*(1-amb));   // flat light for small particles (debris, flakes)
  gl.disable(gl.BLEND);
  if (vol) app.sim3d.render(T,frame,lighting,app.exporting);
  else {
    gl.bindFramebuffer(gl.FRAMEBUFFER,T.S.fbo);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT);
    renderBakedSheets(T,frame,lighting);
    gl.enable(gl.BLEND);gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);
    const sheets=app.sim.sheets;
    for(const sheet of [...sheets].reverse()){
      const field=sheet.renderField(frame),soft=sheet.shadeField(field),opticalScale=Math.max(1,Math.max(sheet.gw,sheet.gh)/768);gl.enable(gl.BLEND);gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);
      pass(P.smoke,T.S,T.w,T.h,{
      uDetailNoise:detailNoise(),uDye:field,uPhysicalDye:sheet.dye.read.tex,uMaterial:sheet.material.read.tex,uSoftDye:soft,uFineFlow:sheet.combinedFlow.tex,uHasFines:sheet.fineActive?1:0,uHeat:sheet.heat.read.tex,uObs:sheet.obs,uTexel:sheet.texel.map(x=>x*opticalScale),uTime:frame/fps,uAspect:[W/H,1],uFlow:sheet.flow.read.tex,uDetail:S.detail2d??0,uDetailF:sheet.gh/(12*opticalScale),uWA:flowWeight(frame/fps),uSlice:1/sheets.length,
      uLight:[Math.sin(la)*1.5*opticalScale/sheet.gw, Math.cos(la)*1.5*opticalScale/sheet.gh],uLdir:[Math.sin(la),Math.cos(la)],uCosE:Math.cos(le),uSinE:Math.sin(le),
      uShape:S.shape2d??0.55,uOpacitySoft:S.opacitySoft2d??.35,uDens:S.density,uShadow:S.shadow,uAmb:amb,uGlow:S.glow,uFire:lighting.fire,uKey:key,uSky:sky});
    }
    gl.disable(gl.BLEND);
  }
  // debris
  sprFx.clear();frontDebris.clear();
  const shutter=0.5/fps;
  for (const inst of state.instances){
    if (!inst.enabled) continue; const pr=PRESETS[inst.preset]; if (!pr.debris && !pr.grit) continue;
    const ps=parts.get(inst.id); if (!ps) continue;
    const shutter=(pr.debris?.shutter??.5)/fps;
    const c=lin(pr.debris ? pr.debris.color : inst.color).map((v,i)=>v*tint[i]);
    const cg=lin(inst.color).map((v,i)=>v*tint[i]), ga=pr.grit ? pr.grit.alpha : 0;   // grit is lit dust colour
    const originDepth=vol&&inst.w?project(inst.w,cam3)?.[2]:0;
    for (const q of ps.debris){
      if (q.age>=q.life || q.age<=0) continue;
      const p=scr(q); if (!p) continue;
      const fade=Math.min(1,(p.life-p.age)/0.4);
      const ax=p.vx*shutter*0.5, ay=p.vy*shutter*0.5, L=Math.hypot(ax,ay)*scale, hw=Math.max(p.size*scale,0.75);
      if (q.g){   // grit: tiny soft specks, streaked by motion blur; sub-pixel grains fade out rather than shimmer
        if (!q.vis) continue;   // most grit only carries dust
        const a=ga*fade*Math.min(1,p.size*scale/0.75)*Math.min(1,hw/(L+hw)*1.6), k=p.shade*a;
        sprFx.push(p.x-ax,p.y-ay,ax,ay,p.size,0,0,0,cg[0]*k,cg[1]*k,cg[2]*k,a);
        continue;
      }
      const a=fade*Math.min(1,hw/(L+hw)*1.3);
      // 2D keeps the spray's depth for ordering; 3D uses current camera depth.
      // Near chips can silhouette against the cloud instead of disappearing under it.
      const near=vol?originDepth-p.depth:(q.z||0),unit=vol?inst.wscale:H;
      const front=pr.debris.frontDepth!=null&&near>pr.debris.frontDepth*unit*inst.scale;
      const tumble=front ? .05+.95*Math.max(0,Math.cos(p.rot+p.k))**4 : 1,shade=p.shade*a*tumble;
      (front?frontDebris:sprFx).push(p.x-ax,p.y-ay,ax,ay,p.size,2,p.rot,p.k,c[0]*shade,c[1]*shade,c[2]*shade,a);
    }
  }
  for(const inst of state.instances){
    if(!inst.enabled)continue;const pr=PRESETS[inst.preset];
    if(!pr.grains||usesBakedSmoke(inst)||(vol&&!inst.w))continue;
    const color=lin(inst.color).map((v,n)=>v*tint[n]);
    for(const grain of dustGrains(inst,pr,((app.freezeEffectsFrame??frame)-inst.frame)/fps,vol?inst.wscale:H,vol)){
      const p=scr(grain);if(!p)continue;
      const ax=p.vx*shutter*.25,ay=p.vy*shutter*.25;
      const a=p.alpha*Math.min(1,(p.size*scale/.75)**2),c=a*p.shade;
      sprFx.push(p.x-ax,p.y-ay,ax,ay,p.size,0,0,0,color[0]*c,color[1]*c,color[2]*c,a);
    }
  }
  gl.bindFramebuffer(gl.FRAMEBUFFER,T.DB.fbo); gl.viewport(0,0,T.w,T.h); gl.clearColor(0,0,0,0); gl.clear(gl.COLOR_BUFFER_BIT);
  gl.enable(gl.BLEND); gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);
  drawSprites(T.DB,T.w,T.h,scale,sprFx.a,sprFx.n,false);
  drawFineGrains(T.DB,frame,cam3,tint);
  gl.bindFramebuffer(gl.FRAMEBUFFER,T.DF.fbo);gl.clear(gl.COLOR_BUFFER_BIT);
  drawSprites(T.DF,T.w,T.h,scale,frontDebris.a,frontDebris.n,false);
  // sparks + flash
  sprFx.clear();
  const red=lin('#ff5a1e');
  for (const inst of state.instances){
    if (!inst.enabled) continue; const pr=PRESETS[inst.preset];
    if (pr.flash && inst.flash){
      const k=frame-inst.frame;
      if (k>=0 && k<pr.flash.frames){
        const I=pr.flash.intensity*Math.pow(0.33,k), c=lin(pr.flash.color);
        let fx=inst.x, fy=inst.y, rr=pr.flash.size*H*inst.scale*(1+0.25*k);
        if (vol && inst.w){   // 3D: the flash sits at the effect's world position, sized for its depth
          const cam=camAt(frame), p=project(inst.w,cam); if (!p) continue;
          fx=p[0]; fy=p[1]; rr=pr.flash.size*inst.wscale*inst.scale*(1+0.25*k)/(2*p[2]*cam.tanV)*H;
        }
        sprFx.push(fx,fy,0,0,rr,3,0,0,c[0]*I,c[1]*I,c[2]*I,Math.min(1,0.7*Math.pow(0.4,k)));
      }
    }
    if (!pr.sparks) continue;
    const ps=parts.get(inst.id); if (!ps) continue;
    const K=pr.sparks, c=lin(K.color), sh=0.75/fps, width2=K.width*H*Math.sqrt(inst.scale);
    for (const q of ps.sparks){
      if (q.age>=q.life || q.age<=0) continue;
      const p=scr(q); if (!p) continue;
      const width=vol ? K.width*inst.wscale*Math.sqrt(inst.scale)*p.k3 : width2;
      const t=p.age/p.life, fade=Math.pow(1-t,1.5);
      const ax=p.vx*sh*0.5, ay=p.vy*sh*0.5, L=Math.hypot(ax,ay)*scale, hw=Math.max(width*scale,0.75);
      const en=Math.sqrt(hw/(L+hw)), I=K.intensity*p.heat*fade*en;
      const cr=c[0]+(red[0]-c[0])*t, cg=c[1]+(red[1]-c[1])*t, cb=c[2]+(red[2]-c[2])*t;
      sprFx.push(p.x-ax,p.y-ay,ax,ay,width,1,0,0,cr*I,cg*I,cb*I,Math.min(1,0.6*fade*en*2));
    }
  }
  gl.bindFramebuffer(gl.FRAMEBUFFER,T.K.fbo); gl.clear(gl.COLOR_BUFFER_BIT);
  gl.blendFunc(gl.ONE,gl.ONE);
  drawSprites(T.K,T.w,T.h,scale,sprFx.a,sprFx.n,false);
  // snow: spindrift plumes first, blowing flakes over them
  gl.bindFramebuffer(gl.FRAMEBUFFER,T.N.fbo); gl.clear(gl.COLOR_BUFFER_BIT);
  gl.blendFunc(gl.ONE,gl.ONE_MINUS_SRC_ALPHA);
  sprFx.clear();
  for (const inst of state.instances){
    if (!inst.enabled) continue; const pr=PRESETS[inst.preset];
    if (pr.kind==='drift'){ const u=driftUniforms(inst,pr,frame,T,lighting); if (u) pass(P.drift,T.N,T.w,T.h,u); pushDriftGrains(inst,pr,frame,sprFx,scale,tint); }
    else if (pr.kind==='snow') pushSnowFlakes(inst,pr,frame,sprFx,scale,tint);
  }
  drawSprites(T.N,T.w,T.h,scale,sprFx.a,sprFx.n,false);
  // holdout: every layer multiplied by (1 - matte) in place, so the effects sit behind whatever the matte covers
  if (held){
    gl.blendFunc(gl.ZERO,gl.ONE_MINUS_SRC_ALPHA);
    for (const L of [T.S.only||T.S,T.F,T.DB,T.DF,T.K,T.N]) pass(P.holdout,L,T.w,T.h,{uMatte:matteTex,uInvert:state.matte.invert?1:0});
  }
  gl.disable(gl.BLEND);
  // Merge after holdout so the raw particle layer remains exactly reconstructible
  // from its front/back layers, including partially transparent matte edges.
  pass(P.over,T.D,T.w,T.h,{uFront:T.DF.tex,uBack:T.DB.tex});
}
