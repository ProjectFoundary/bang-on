import { api, type Project } from './api';
import { loadVolumeScene, sceneKey } from './volumeScene';
import { setVolumeFXScene, renderVolumeElement, pruneVolumeFX } from './volumeFX';
import { volumeOptions } from './elementOptions';

async function request(url:string,options?:RequestInit){const r=await fetch(url,options);if(!r.ok){const t=await r.json().catch(()=>({detail:r.statusText}));throw new Error(typeof t.detail==='string'?t.detail:JSON.stringify(t.detail));}return r.json();}
export const elementRequest=request;
const png=(canvas:HTMLCanvasElement)=>new Promise<Blob>((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('Could not capture the guide.')),'image/png'));
export async function prepareElement(p:Project,effectId:string,paths:Map<string,Float32Array>,progress:(done:number,total:number)=>void,valid:()=>boolean=()=>true){
  const prefix=`/api/projects/${p.id}`,plan=await request(`${prefix}/elements/plan?effect_id=${encodeURIComponent(effectId)}`);
  const frozenKey=sceneKey(p,plan.width,plan.height),effect=p.effects.find(e=>e.id===effectId)!;
  setVolumeFXScene(await loadVolumeScene(p,960,540));
  const form=new FormData();let sourceDepth:number|undefined;
  try{
    for(let i=0;i<plan.active_frames;i++){
      await new Promise(requestAnimationFrame);
      if(!valid()||sceneKey(p,plan.width,plan.height)!==frozenKey)throw new Error('The shot changed. Prepare a new guide.');
      const f=plan.source_frames[i],options=volumeOptions(p,effect,f,paths,plan.width,plan.height);
      const rendered=renderVolumeElement(effect.id,effect.preset,{...options,sourceTimeSeconds:plan.start_time+i/24,quality:'high'},f,'capture');
      if(!rendered)throw new Error('The simulation renderer is unavailable.');
      const depth=Number(rendered.metadata.sourceDepth);if(sourceDepth===undefined&&Number.isFinite(depth)&&depth>0)sourceDepth=depth;
      const [rgb,vis,alpha]=await Promise.all([png(rendered.rgb),png(rendered.visibility),png(rendered.alpha)]);
      const suffix=String(i).padStart(4,'0');form.append(`guide_${suffix}.png`,rgb,`guide_${suffix}.png`);form.append(`visibility_${suffix}.png`,vis,`visibility_${suffix}.png`);
      if(alpha)form.append(`guide_alpha_${suffix}.png`,alpha,`guide_alpha_${suffix}.png`);
      progress(i+1,plan.active_frames);
    }
    if(!valid())throw new Error('The shot changed before upload.');
    form.append('metadata',JSON.stringify({signature:plan.signature,...(sourceDepth===undefined?{}:{source_depth:sourceDepth})}));
    return await request(`${prefix}/elements/${effectId}/prepare`,{method:'POST',body:form});
  }finally{pruneVolumeFX(new Set(),'capture');}
}
export const startElement=(pid:string,tid:string)=>request(`/api/projects/${pid}/element-takes/${tid}/generate`,{method:'POST'});
export async function refreshMatteSupport(p:Project,effectId:string,takeId:string,paths:Map<string,Float32Array>,progress:(done:number,total:number)=>void,valid:()=>boolean){
  const plan=await request(`/api/projects/${p.id}/elements/${effectId}/matte-support/plan?take_id=${encodeURIComponent(takeId)}`),effect=p.effects.find(e=>e.id===effectId)!;
  setVolumeFXScene(await loadVolumeScene(p,960,540));const form=new FormData();
  try{
    for(let i=0;i<plan.active_frames;i++){
      await new Promise(requestAnimationFrame);if(!valid())throw new Error('The effect changed during matte capture.');
      const frame=plan.source_frames[i],options=volumeOptions(p,effect,frame,paths,plan.width,plan.height);
      const rendered=renderVolumeElement(effectId,effect.preset,{...options,sourceTimeSeconds:plan.start_time+i/24,quality:'high'},frame,'capture');
      if(!rendered)throw new Error('Simulation opacity is unavailable.');
      const alpha=await png(rendered.alpha),name=`alpha_${String(i).padStart(4,'0')}.png`;form.append(name,alpha,name);progress(i+1,plan.active_frames);
    }
    if(!valid())throw new Error('The effect changed before the matte was saved.');
    form.append('metadata',JSON.stringify({signature:plan.signature,plan_signature:plan.plan_signature}));
    return await request(`/api/projects/${p.id}/elements/${effectId}/matte-support/${takeId}`,{method:'POST',body:form});
  }finally{pruneVolumeFX(new Set(),'capture');}
}
export const startBatchElement=(pid:string,tid:string)=>request(`/api/projects/${pid}/element-takes/${tid}/generate-batch`,{method:'POST'});
export const compositeElements=(pid:string)=>request(`/api/projects/${pid}/elements/composite`,{method:'POST'});
export const reviewCompositeElements=(pid:string,takeIds:string[])=>request(`/api/projects/${pid}/elements/review-composite`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({take_ids:takeIds})});
export async function waitForProject(pid:string,done:(p:Project)=>boolean,onupdate:(p:Project)=>void){
  for(;;){const p=await api.project(pid);onupdate(p);if(done(p))return p;await new Promise(resolve=>setTimeout(resolve,1200));}
}
