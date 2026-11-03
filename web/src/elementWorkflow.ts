import type {Project,Render} from './api';

/** Enter the live preview when only historical results match an older setup. */
export function preferredEffectView(project:Pick<Project,'renders'>):'preview'|'result'{
  return project.renders.some(r=>['effect','final'].includes(r.kind)&&r.status==='done'&&r.source_current!==false)?'result':'preview';
}

export function collisionMethod(geometry:Project['geometry']):'matte2d'|'depth3d'{
  return geometry?.collision_mode??(geometry?.depth?'depth3d':'matte2d');
}
export function currentDepth(geometry:Project['geometry']):boolean{
  const depth=geometry?.depth as {reference_frame?:number}|null|undefined;
  return !!depth&&(depth.reference_frame??geometry?.reference_frame)===geometry?.reference_frame;
}
export function interactionIssue(geometry:Project['geometry']):string{
  return collisionMethod(geometry)==='depth3d'&&!currentDepth(geometry)?'Depth 3D needs a depth pass for the selected reference frame. Generate or import it, or choose 2D matte collision.':'';
}
export function approvedEffectCount(project:Project):number{
  return project.effects.filter(effect=>project.element_takes?.some(take=>take.effect_id===effect.id&&take.current&&take.approved&&take.status==='done')).length;
}
export function geometryJob(renders:Render[],kind:'depth'|'matte'):Render|undefined{
  return renders.find(render=>render.kind==='geometry'&&render.geometry_kind===kind);
}
export function geometryJobStatus(job:Render|undefined):string{
  if(!job)return '';
  return ({queued:'Queued',checking:'Checking required Comfy models',uploading:'Uploading the source to Comfy',rendering:'Generating in Comfy',validating:'Validating the result',downloading:'Downloading the result',done:'Complete',error:'Failed'} as Record<string,string>)[job.status]??job.status;
}
export function matteTrackingBody(name:string,soft:boolean,x:number,y:number){
  return {kind:'matte',points:[{frame:0,person:0,x,y,label:1}],name,soft};
}
export function elementTakeStatus(status:string):string{
  return ({prepared:'Guide prepared',queued:'Queued',awaiting_rgb_qc:'Awaiting Fluid input review',awaiting_alpha_qc:'Awaiting alpha input review',rgb:'Rendering LTX',alpha:'Extracting alpha',validating:'Checking assets',done:'Ready',error:'Failed'} as Record<string,string>)[status]??status;
}

/** Historical pixels remain inspectable without being labelled current. */
export function finishTakePresentation(take:Render|undefined,elementMode:boolean,allElementsApproved:boolean){
  if(take?.review_only)return {heading:'REVIEW COMPOSITE',notice:take.source_current===false?'This review uses earlier settings. Generate effects with LTX to update it.':allElementsApproved?'Build a final take from the approved versions in Finish.':'Review and approve individual elements before building the final take.'};
  if(take?.source_current!==false)return {heading:'CURRENT TAKE',notice:''};
  const next=elementMode
    ?allElementsApproved?'Update composite from cached elements, then build the final take.':'Review the changed elements in Effects, then build the final take.'
    :'Generate an updated effects take in Effects, then finish again.';
  return {heading:'EARLIER TAKE',notice:`This result uses earlier settings. ${next}`};
}
