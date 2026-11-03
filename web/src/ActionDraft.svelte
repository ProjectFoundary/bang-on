<script lang="ts">
  import {api,type Project} from './api';
  let {project,onchange,onerror}: {project:Project;onchange:()=>void;onerror:(s:string)=>void}=$props();
  let draft=$state(''),seen=$state(''),discarded=$state('');
  let submitting=$state(false);
  const run=$derived(project.renders.find(r=>r.kind==='analysis'));
  const analysing=$derived(!!run&&!['done','error'].includes(run.status));
  const locked=$derived(project.renders.some(r=>r.kind==='pipeline'&&!['done','error'].includes(r.status)));
  $effect(()=>{if(run?.status==='done'&&run.draft&&seen!==`${project.id}:${run.id}`){draft=run.draft;seen=`${project.id}:${run.id}`;}});
  async function analyse(){submitting=true;try{const r=await api.render(project.id,'analysis');project.renders=[r,...project.renders.filter(old=>old.id!==r.id)];}catch(e){onerror(String(e));}finally{submitting=false;}}
  async function review(use:boolean){
    if(!run)return;
    const text=use?draft.trim():project.shot;
    try { await api.patch(project.id,{shot:text,action_review:run.id});project.shot=text;project.action_review=run.id;discarded=`${project.id}:${run.id}`;onchange(); }
    catch(e){onerror(String(e));}
  }
</script>
<div class="action-draft">
  {#if run?.status==='done'&&run.draft&&project.action_review!==run.id&&discarded!==`${project.id}:${run.id}`}
    <label>Suggested action · review before using<textarea bind:value={draft} rows="5"></textarea></label>
    <div class="row"><button disabled={!draft.trim()||locked} onclick={()=>review(true)}>Use this action</button><button class="ghost" disabled={locked} onclick={()=>review(false)}>Keep my description</button></div>
  {/if}
  <button class="small" disabled={analysing||submitting||locked} onclick={analyse}>{analysing||submitting?'Drafting action in background…':'Draft action from source clip'}</button>
  {#if analysing}<p class="small background" role="status"><span class="spinner" aria-hidden="true"></span>Continue setting up your shot. The action draft will appear here for review.</p>{/if}
  {#if run?.status==='error'}<p class="small err">Analysis failed: {run.error} You can enter the action below.</p>{/if}
</div>
<style>.action-draft{display:grid;gap:8px}textarea{min-height:100px}.row{flex-wrap:wrap}.background{display:flex;gap:8px;align-items:center;color:var(--muted);margin:0}.spinner{width:11px;height:11px;flex:none;border:2px solid var(--line);border-top-color:var(--accent);border-radius:50%;animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.spinner{animation:none}}</style>
