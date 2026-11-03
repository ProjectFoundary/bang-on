import type {Project,Render} from './api';

type Take=NonNullable<Project['element_takes']>[number];
export type BatchProgress={index:number;total:number;effectId:string;label:string;stage:string;done?:number;frames?:number};
export type BatchHooks={onUpdate:(p:Project)=>void;onProgress:(p:BatchProgress)=>void;valid:()=>boolean};
export type BatchDependencies={
  project:(pid:string)=>Promise<Project>;
  prepare:(p:Project,eid:string,paths:Map<string,Float32Array>,progress:(done:number,total:number)=>void,valid:()=>boolean)=>Promise<Take>;
  start:(pid:string,tid:string)=>Promise<unknown>;
  wait:(pid:string,done:(p:Project)=>boolean,onupdate:(p:Project)=>void)=>Promise<Project>;
  composite:(pid:string,takeIds:string[])=>Promise<Render>;
};

/** Approval pins always win over newer variations. Completed unapproved
 * versions can be reviewed again without buying another model run. */
export function effectBatchPlan(p:Project){
  return p.effects.map(effect=>{
    const candidates=p.element_takes?.filter(t=>t.effect_id===effect.id&&t.current&&!t.needs_alpha_core)??[];
    const approved=candidates.find(t=>t.status==='done'&&t.approved);
    const take=approved??candidates.find(t=>t.status==='done')??candidates.find(t=>t.status==='prepared'&&t.backend_current!==false);
    const name=effect.preset.replaceAll('_',' '),label=name.charAt(0).toUpperCase()+name.slice(1);
    return {effect,take,approved:!!approved,ready:take?.status==='done',label};
  });
}
export function batchConfiguration(p:Project){
  return JSON.stringify([p.id,p.effects,p.geometry,p.settings,p.native_profile,p.source,p.start_s]);
}

/** Sequential, explicitly user-started queue. A failure stops later submissions.
 * Reload/retry discovers completed immutable takes instead of resubmitting them.
 * Review compositing never changes a manual approval. */
export async function runEffectBatch(snapshot:Project,paths:Map<string,Float32Array>,hooks:BatchHooks,deps:BatchDependencies){
  if(!snapshot.effects.length)throw new Error('Place an effect before generating LTX elements.');
  const pid=snapshot.id,key=batchConfiguration(snapshot),ids=snapshot.effects.map(e=>e.id),chosen=new Map<string,string>();
  let generated=0,reused=0,index=0,label='';
  const valid=()=>hooks.valid();
  const check=(p?:Project)=>{
    if(!valid())throw new Error('Batch stopped because the shot changed. Completed versions are retained.');
    if(p&&batchConfiguration(p)!==key)throw new Error('The effect setup changed. Batch stopped; completed versions are retained.');
  };
  const update=(p:Project)=>{if(valid())hooks.onUpdate(p);};
  const status=(effectId:string,stage:string,extra:Partial<BatchProgress>={})=>hooks.onProgress({index,total:ids.length,effectId,label,stage,...extra});
  let fresh=snapshot;
  try{
    for(const id of ids){
      index++;check();fresh=await deps.project(pid);check(fresh);update(fresh);
      const item=effectBatchPlan(fresh).find(i=>i.effect.id===id)!;label=item.label;
      let take=item.take;
      if(item.ready){reused++;chosen.set(id,take!.id);status(id,item.approved?'Keeping approved version':'Reusing ready version');continue;}
      if(!take){
        status(id,'Capturing guide');
        take=await deps.prepare(fresh,id,paths,(done,frames)=>status(id,'Capturing guide',{done,frames}),valid);
      }
      check();fresh=await deps.project(pid);check(fresh);update(fresh);
      const pinned=effectBatchPlan(fresh).find(i=>i.effect.id===id);
      if(pinned?.approved){reused++;chosen.set(id,pinned.take!.id);status(id,'Keeping approved version');continue;}
      const prepared=fresh.element_takes?.find(t=>t.id===take!.id&&t.current&&t.backend_current!==false);
      if(!prepared||prepared.status!=='prepared')throw new Error('The prepared version changed before generation. Refresh its status before continuing.');
      status(id,'LTX and alpha');const started=await deps.start(pid,take!.id);
      if(started&&typeof started==='object'&&'skipped_approved' in started&&started.skipped_approved){
        fresh=await deps.project(pid);check(fresh);update(fresh);
        const pin=effectBatchPlan(fresh).find(i=>i.effect.id===id&&i.approved);
        if(!pin)throw new Error('The approved version changed. Refresh before continuing.');
        reused++;chosen.set(id,pin.take!.id);continue;
      }
      fresh=await deps.wait(pid,p=>p.element_takes?.some(t=>t.id===take!.id&&['done','error'].includes(t.status))??false,p=>{
        update(p);const t=p.element_takes?.find(t=>t.id===take!.id);
        if(t)status(id,t.status);
      });
      check(fresh);update(fresh);
      const finished=fresh.element_takes?.find(t=>t.id===take!.id);
      if(!finished||finished.status!=='done'||!finished.current)throw new Error(finished?.error??'This generated version is not current.');
      generated++;chosen.set(id,finished.id);
    }
    check();fresh=await deps.project(pid);check(fresh);update(fresh);
    const takeIds=effectBatchPlan(fresh).map(item=>{
      const id=item.approved?item.take!.id:chosen.get(item.effect.id);
      if(!fresh.element_takes?.some(t=>t.id===id&&t.effect_id===item.effect.id&&t.current&&t.status==='done'))throw new Error('An element changed before compositing. Completed versions are retained.');
      return id!;
    });
    label='All effects';status('','Building review composite');check();
    const render=await deps.composite(pid,takeIds);
    fresh=await deps.wait(pid,p=>p.renders.some(r=>r.id===render.id&&['done','error'].includes(r.status)),update);
    check(fresh);update(fresh);
    const result=fresh.renders.find(r=>r.id===render.id);
    if(!result||result.status!=='done')throw new Error(result?.error??'Review composite did not complete.');
    return {project:fresh,render:result,generated,reused};
  }catch(error){
    const detail=error instanceof Error?error.message:String(error);
    throw new Error(`${label?`${label}: `:''}${detail} Batch stopped; no later effects were submitted. Ready versions are retained.`);
  }
}
