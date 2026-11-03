import type { Project } from './api';
import { api } from './api';
import { elementRequest } from './elementFlow';
import { elementOnset, elementTime, volumeKind, volumeOptions } from './elementOptions';
import { loadVolumeScene, sceneKey } from './volumeScene';
import { setVolumeFXScene, renderVolumeElement, pruneVolumeFX } from './volumeFX';
import { image } from './effects';
import { renderFlashPreview } from './flashPreview';

export const previewRecipe=(p:Project)=>JSON.stringify([p.id,p.effects,p.geometry,p.flashes,p.settings]);
const jpeg=(canvas:HTMLCanvasElement)=>new Promise<Blob>((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('Could not capture the preview.')),'image/jpeg',.92));

/** Sequential offline capture: GPU speed cannot drop/reorder video frames. */
export async function downloadSimulationPreview(p:Project,paths:Map<string,Float32Array>,quality:'draft'|'high',
  progress:(done:number,total:number)=>void,valid:()=>boolean,signal:AbortSignal){
  const prefix=`/api/projects/${p.id}/simulation-preview`,plan=await elementRequest(`${prefix}/plan?quality=${quality}`);
  const check=()=>{signal.throwIfAborted();if(!valid())throw new Error('The shot changed. Download a new preview.');};
  const canvas=document.createElement('canvas');canvas.width=plan.capture_width;canvas.height=plan.capture_height;
  const g=canvas.getContext('2d')!,frozenScene=sceneKey(p,960,540);
  const scene=await loadVolumeScene(p,960,540);check();setVolumeFXScene(scene);
  if(p.effects.some(e=>!volumeKind(e)))throw new Error('This shot contains an effect without an LTX simulation preview.');
  if(p.flashes?.length){const stamp=image(api.stampUrl('muzzle_side','flash.png'));await stamp.decode();check();}
  const form=new FormData();form.append('metadata',JSON.stringify({signature:plan.signature,quality}));let bytes=0;
  try{
    pruneVolumeFX(new Set(),'preview');
    for(let i=0;i<plan.frames;i++){
      await new Promise(requestAnimationFrame);check();
      if(sceneKey(p,960,540)!==frozenScene)throw new Error('The interaction changed. Capture a new preview.');
      const frame=plan.source_frames[i],response=await fetch(api.frameUrl(p.id,frame,'plate'),{signal});
      if(!response.ok)throw new Error(`Source preview frame ${frame} is missing.`);
      const plate=await createImageBitmap(await response.blob());
      try{check();g.globalCompositeOperation='source-over';g.globalAlpha=1;g.filter='none';g.drawImage(plate,0,0,canvas.width,canvas.height);}finally{plate.close();}
      setVolumeFXScene(scene);
      for(const effect of p.effects){
        const time=elementTime(p,frame),start=Math.max(0,elementTime(p,elementOnset(effect)));
        if(time<start||time>=start+(effect.element?.duration_s??2))continue;
        const result=renderVolumeElement(effect.id,volumeKind(effect)!,{...volumeOptions(p,effect,frame,paths,canvas.width,canvas.height),quality},frame,'download');
        if(!result)throw new Error('The simulation renderer is unavailable.');
        g.save();try{g.globalAlpha=effect.element?.opacity??1;g.filter=`brightness(${effect.element?.gain??1})`;g.drawImage(result.composite,0,0,canvas.width,canvas.height);}finally{g.restore();}
      }
      renderFlashPreview(g,p.flashes??[],frame,()=>{});
      const blob=await jpeg(canvas);bytes+=blob.size;if(bytes>256*1024*1024)throw new Error('Preview capture exceeds256MB. Use a shorter shot.');
      const name=`frame_${String(i).padStart(4,'0')}.jpg`;form.append(name,blob,name);progress(i+1,plan.frames);
    }
    check();return await elementRequest(prefix,{method:'POST',body:form,signal});
  }finally{pruneVolumeFX(new Set(),'download');}
}
