import type {Project} from './api';

export const PEOPLE_ALPHA_MAX_SECONDS=5;
export type PeopleAlphaTake={id:string;status:string;created:string;seed:number;signature:string;current:boolean;backend_current?:boolean;error?:string;plan?:{frames?:number;duration?:number};asset?:unknown};
export type PeopleAlphaProject=Project&{people_alpha_takes?:PeopleAlphaTake[]};
export function peopleHoldoutDuration(project:Pick<Project,'frames'|'native_profile'>):number{
  return project.native_profile?.duration??project.frames/24;
}
export function peopleHoldoutRangeIssue(project:Pick<Project,'frames'|'native_profile'>):string{
  const duration=peopleHoldoutDuration(project);
  if(!Number.isFinite(duration)||duration<=0)return 'The full-shot duration could not be read. Reopen the shot before preparing a holdout.';
  return duration>PEOPLE_ALPHA_MAX_SECONDS?`Full-shot AlphaGen holdouts currently support clips up to 5 seconds. This shot is ${duration.toFixed(2)}s.`:'';
}
export function hasCustomAlphaModels(models:Record<string,string>|undefined):boolean{
  return ['transformer','text_encoder','video_vae','audio_vae','alpha'].some(role=>!!models?.[role]?.trim());
}
type Check={id:string;status:string};
type ReadinessInput={configured:boolean;customModels:boolean;checkedProfile?:{checks?:Check[];error?:string}|null;verifiedReport?:{connected:boolean;checks:Check[]}|null};
export function peopleAlphaReadiness({configured,customModels,checkedProfile,verifiedReport}:ReadinessInput):{blocked:boolean;notice:string}{
  if(!configured)return {blocked:true,notice:'Connect Comfy to generate an AlphaGen foreground matte. Local preparation remains available.'};
  if(customModels&&!checkedProfile)return {blocked:true,notice:'Custom AlphaGen model names are selected. Check these models before generating a holdout.'};
  const alpha=(checkedProfile?.checks??(!customModels&&verifiedReport?.connected?verifiedReport.checks:[])).find(check=>check.id==='alpha');
  if(alpha?.status==='missing')return {blocked:true,notice:'AlphaGen model is not installed on this Comfy backend. Add it in model setup before generating a holdout.'};
  if(checkedProfile&&alpha?.status!=='ready')return {blocked:true,notice:checkedProfile.error??'AlphaGen availability could not be verified. Check the selected models before generating a holdout.'};
  return {blocked:false,notice:''};
}
export function peopleAlphaStatus(status:string):string{
  return ({prepared:'Input prepared',checking:'Checking AlphaGen',submitting:'Submitting AlphaGen',rendering:'Generating foreground matte',done:'Ready for review',failed:'Failed',uncertain:'Submission outcome uncertain'} as Record<string,string>)[status]??status;
}
export function canGeneratePeopleAlpha(take:PeopleAlphaTake|undefined):boolean{
  return !!take&&take.status==='prepared'&&take.current&&take.backend_current===true;
}
export function canUsePeopleAlpha(take:PeopleAlphaTake|undefined,reviewed:boolean):boolean{
  return !!take&&take.status==='done'&&take.current&&!!take.asset&&reviewed;
}
