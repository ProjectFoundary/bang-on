<script lang="ts">
  import type {Project, Render} from './api';
  let {project,busy='',localProgress=null,onresult}: {project:Project;busy?:string;localProgress?:{done:number;total:number}|null;onresult:(r:Render)=>void}=$props();
  const background=$derived(project.renders.find(r=>r.kind==='analysis'&&!['done','error'].includes(r.status)));
  const foreground=$derived(project.renders.find(r=>r.kind!=='analysis'&&!['done','error'].includes(r.status)));
  const active=$derived(foreground??background);
  const latest=$derived([...project.renders].sort((a,b)=>stamp(b)-stamp(a))[0]);
  const r=$derived(active ?? latest);
  function stamp(r:Render){return r.finished_at??(Date.parse((r.finished??r.created).replace(' ','T'))/1000||0);}
  const title=$derived.by(()=>{
    if(busy)return busy;
    if(!r)return 'Ready to work on your shot';
    const kind={analysis:'Action analysis',matte:'People matte',people_alpha:'AlphaGen people holdout',element:'LTX element + alpha',lighting:'MiniMax lighting',geometry:'Depth / collider matte',effect:'Effects video',keys:'Effect images',pipeline:'Complete take',environment:'Scene video',location:'Location still',dressing:'Object edit',final:'Final take'}[r.kind];
    if(r.status==='done')return r.source_current===false?`${kind} · earlier settings`:r.quality_warnings?.length?`${kind} ready · review issues`:`${kind} ready`;
    if(r.status==='error')return `${kind} failed`;
    if(r.kind==='analysis')return r.progress?.cloud_status?.startsWith('queued')?'Action draft · waiting in Comfy in the background':'Drafting action in the background';
    if(r.kind==='environment'&&r.scene_phase){
      const phase=r.scene_phase==='draft'?'1 of 2 · Building scene':'2 of 2 · Finishing HD';
      if(r.progress?.cloud_status?.startsWith('queued'))return `${phase} · waiting in Comfy`;
      const state=({queued:'queued',preparing:'preparing source',uploading:'uploading',downloading:'downloading',finishing:'preparing result'}[r.status]);
      return state?`${phase} · ${state}`:phase;
    }
    if(r.progress?.cloud_status?.startsWith('queued'))return `Waiting in Comfy · ${kind.toLowerCase()}`;
    return ({queued:'Queued',rgb:'Rendering Fluid RGB',alpha:'Extracting AlphaGen matte',validating:'Checking generated assets',preparing:'Preparing source',building_location:'1 of 2 · Building the empty location',integrating_people:'2 of 2 · Integrating selected people',polishing:'Generating effect key images',uploading:'Uploading to Comfy',rendering:'Processing in Comfy',downloading:'Downloading result',finishing:'Preparing the result',effects:'Generating effects video',flashes:'Generating muzzle frames',splicing:'Splicing the final take'}[r.status]??'Processing')+` · ${kind.toLowerCase()}`;
  });
  const p=$derived(busy?undefined:active?.progress);
  const realFraction=$derived(localProgress?.total ? localProgress.done/localProgress.total : p?.steps && p.step!=null ? p.step/p.steps : p?.nodes_total && p.nodes_done!=null ? p.nodes_done/p.nodes_total : null);
  const inProgress=$derived(!!busy||!!active);
  const complete=$derived(!inProgress&&r?.status==='done');
  const failed=$derived(!inProgress&&r?.status==='error');
</script>
<section class="processing" class:failed aria-label="Shot processing" aria-live="polite">
  <div class="top"><strong>{title}</strong>
    {#if inProgress}<span class="state active">In progress</span>{:else if complete}<span class="state complete">Complete</span>{:else if failed}<span class="state failed-state">Failed</span>{/if}
    <span class="grow"></span>
    {#if complete&&['effect','environment','final','keys','pipeline'].includes(r.kind)}<button class="small" onclick={()=>onresult(r)}>View result</button>{/if}
    <span class="timing">{(project.native_profile?.duration??project.frames/24).toFixed(2)}s · {project.frames} frames</span>
  </div>
  {#if failed}<p class="error">{r?.error??'The job did not finish. Your source and placements are saved.'}</p>
  {:else if inProgress}
    {#if realFraction==null}
      <div class="progress-track" role="progressbar" aria-label={title} aria-valuetext="In progress"><span class="progress-loop"></span></div>
    {:else}
      <progress max="1" value={Math.min(1,Math.max(0,realFraction))} aria-label={title}></progress>
    {/if}
    <div class="detail">{#if !busy&&active?.kind==='analysis'}You can continue setting up your shot. Review the draft when it is ready.{:else if localProgress}{localProgress.done} / {localProgress.total} {busy.startsWith('Capturing simulation preview')?'preview frames captured':'keyframes prepared'}{:else if p?.steps&&p.step!=null}Step {p.step} / {p.steps} in this pass{:else if p?.queue_position!=null}Queue position {p.queue_position}{:else}Waiting for the result.{/if}</div>
  {:else if complete}
    <progress max="1" value="1" aria-label="Complete"></progress>
  {/if}
  {#if background&&(foreground||busy)}<div class="detail">Action drafting continues in the background.</div>{/if}
  {#if complete&&r.quality_warnings?.length}<p class="notice small">{r.quality_warnings[0]}</p>{/if}
  {#if project.render_timing && active && ['effect','environment','pipeline'].includes(active.kind)}<div class="detail">Output matches your {project.frames}-frame clip. {project.render_timing.padding_frames ? `Comfy needs ${project.render_timing.padding_frames} padding frames; these are removed.` : 'No extra frames requested.'}</div>{/if}
</section>
<style>
  .processing{margin:14px 24px 0;padding:12px 16px;background:var(--panel);border:1px solid var(--line);border-radius:8px;flex:none}.top{display:flex;gap:12px;align-items:center}.top strong{font-size:13px}.timing,.detail{font-size:11px;color:var(--muted)}.detail{margin-top:5px}.failed{border-color:var(--danger)}.error{font-size:13px;color:var(--danger);margin:6px 0 0}progress{display:block;width:100%;height:5px;margin-top:10px;accent-color:var(--accent)}@media(max-width:650px){.processing{margin:10px}.top{flex-wrap:wrap}.timing{white-space:nowrap}}
  .state{font-size:11px;white-space:nowrap;border:1px solid var(--line);padding:3px 8px;border-radius:20px}.active,.complete{color:var(--accent)}.failed-state{color:var(--danger)}.progress-track{height:5px;margin-top:10px;border-radius:4px;overflow:hidden;background:var(--panel3);position:relative}.progress-loop{position:absolute;inset:0 auto 0 0;width:32%;border-radius:4px;background:var(--accent);animation:progress-loop 1.5s ease-in-out infinite}@keyframes progress-loop{from{transform:translateX(-110%)}to{transform:translateX(320%)}}@media(prefers-reduced-motion:reduce){.progress-loop{animation:none;left:34%}}
</style>
