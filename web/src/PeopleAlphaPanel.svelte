<script lang="ts">
  import {api,type Project} from './api';
  import {connection} from './connection';
  import {elementRequest} from './elementFlow';
  import {peopleHoldoutDuration,peopleHoldoutRangeIssue,hasCustomAlphaModels,peopleAlphaReadiness,peopleAlphaStatus,canGeneratePeopleAlpha,canUsePeopleAlpha,type PeopleAlphaProject} from './peopleHoldoutUI';
  let {project,effectId,frame=0,disabled=false,onupdated,onattached,onconnect}:{project:Project;effectId?:string;frame?:number;disabled?:boolean;onupdated:(p:Project)=>void;onattached:(p:Project,effectId?:string)=>void;onconnect:()=>void}=$props();
  let open=$state(false),working=$state(''),error=$state(''),seed=$state(42),selectedTake=$state('');
  let targetId=$state(''),reviewed=$state<Record<string,boolean>>({}),reviewFrame=$state(0),view=$state<'overlay'|'checker'|'alpha'>('checker');
  let capabilities=$state<any>(null),capabilitiesKey=$state('');
  const rows=$derived((project as PeopleAlphaProject).people_alpha_takes??[]);
  const take=$derived(rows.find(row=>row.id===selectedTake)??rows[0]);
  const configured=$derived(!!$connection?.configured);
  const profileKey=$derived(JSON.stringify([project.id,project.settings.element_models??{},project.settings.element_model_revisions??{},$connection?.mode,$connection?.comfyui_url,$connection?.report?.checked_at]));
  const readiness=$derived(peopleAlphaReadiness({configured,customModels:hasCustomAlphaModels(project.settings.element_models),checkedProfile:capabilitiesKey===profileKey?capabilities:null,verifiedReport:$connection?.report}));
  const duration=$derived(peopleHoldoutDuration(project));
  const rangeIssue=$derived(peopleHoldoutRangeIssue(project));
  const targets=$derived((project.geometry?.mattes??[]).filter(matte=>matte.source_people_matte||matte.holdoutPost||matte.kind==='soft'));
  const target=$derived(targets.find(matte=>matte.id===targetId));
  const activeTargets=$derived((project.geometry?.mattes??[]).filter(matte=>matte.holdout?.take_id&&(!effectId||project.effects.find(effect=>effect.id===effectId)?.element?.colliders.includes(matte.id))));
  const previewFrame=$derived(Math.max(0,Math.min(project.frames-1,reviewFrame)));
  const prefix=$derived(`/api/projects/${project.id}/people-alpha`);
  const previewUrl=$derived(!take?'':view==='alpha'?`${prefix}/${take.id}/frames/${previewFrame}.png`:`${prefix}/${take.id}/review/${previewFrame}.png?view=${view}`);
  async function work(label:string,run:(pid:string)=>Promise<void>){const pid=project.id;working=label;error='';try{await run(pid);}catch(e){if(project.id===pid)error=e instanceof Error?e.message:String(e);}finally{if(project.id===pid)working='';}}
  async function prepare(newSeed=false){
    if(rangeIssue)return;if(newSeed)seed=(seed+1)%2147483647;
    await work('Preparing the full RGB shot locally…',async pid=>{const row=await elementRequest(`/api/projects/${pid}/people-alpha/prepare`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({seed})});const fresh=await api.project(pid);if(project.id!==pid)return;selectedTake=row.id;onupdated(fresh);});
  }
  async function check(){const key=profileKey;await work('Checking the AlphaGen model…',async pid=>{const result=await elementRequest(`/api/projects/${pid}/people-alpha/capabilities`);if(project.id===pid){capabilities=result;capabilitiesKey=key;}});}
  async function generate(){
    if(!canGeneratePeopleAlpha(take)||readiness.blocked||rangeIssue)return;
    const id=take!.id;await work('Submitting one AlphaGen holdout pass…',async pid=>{await elementRequest(`/api/projects/${pid}/people-alpha/${id}/generate`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const fresh=await api.project(pid);if(project.id===pid)onupdated(fresh);});
  }
  async function approve(){
    if(!canUsePeopleAlpha(take,!!reviewed[take?.id??'']))return;
    const id=take!.id,selectedEffect=effectId,matteId=target?.id;
    await work('Applying the reviewed optical holdout…',async pid=>{
      await elementRequest(`/api/projects/${pid}/people-alpha/${id}/approve`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reviewed:true,...(matteId?{matte_id:matteId}:{}),...(selectedEffect?{effect_id:selectedEffect}:{})})});
      const fresh=await api.project(pid);if(project.id!==pid)return;
      const assigned=fresh.effects.find(effect=>effect.id===selectedEffect)?.element?.colliders??[];
      const attached=fresh.geometry?.mattes.find(matte=>matte.holdout?.take_id===id&&(!selectedEffect||assigned.includes(matte.id)));
      targetId=matteId??attached?.id??'';onattached(fresh,selectedEffect);
    });
  }
  async function original(matteId:string){await work('Using this object’s original optical mask…',async pid=>{await elementRequest(`/api/projects/${pid}/geometry/mattes/${matteId}/holdout`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'original'})});const fresh=await api.project(pid);if(project.id===pid)onupdated(fresh);});}
</script>

<details class="people-alpha" bind:open>
  <summary>LTX AlphaGen people holdout <span>Optional · review required</span></summary>
  {#if open}<div class="body">
    <p>AlphaGen reads the original RGB clip and chooses the foreground automatically. Review the person and background before using its matte. The fast SAM2 mask remains available for collision; the source plate is unchanged.</p>
    <p class="range">Full shot · {project.frames} frames · {Number.isFinite(duration)?duration.toFixed(2):'—'}s · maximum 5s</p>
    <p class="small">The model runs at 24fps and its matte is mapped onto the source timeline. Check fast motion carefully on higher-frame-rate footage.</p>
    <p class="small">2D uses the matte directly. Depth 3D estimates foreground ordering from body and effect-source depth; check those overlaps in the composite.</p>
    {#if rangeIssue}<p class="notice" role="status">{rangeIssue}</p>{/if}
    {#if configured&&readiness.notice}<p class="notice" role="status">{readiness.notice}</p>{:else if !configured}<p class="notice">{readiness.notice}</p><button onclick={onconnect}>Connect Comfy</button>{/if}
    <button disabled={disabled||!!working||!configured} onclick={check}>Check AlphaGen model</button>
    <button disabled={disabled||!!working||!!rangeIssue} onclick={()=>prepare()}>Prepare AlphaGen input · local only</button>
    <details><summary>Seed / new pass</summary><fieldset disabled={disabled||!!working}>
      <label>AlphaGen seed<input type="number" min="0" max="2147483647" step="1" bind:value={seed}/></label><button disabled={!!rangeIssue} onclick={()=>prepare(true)}>Prepare a new seed · local only</button>
    </fieldset></details>
    {#if rows.length}
      <label>Holdout version<select aria-label="People AlphaGen holdout version" value={take?.id??''} onchange={event=>selectedTake=event.currentTarget.value}>{#each rows as row}<option value={row.id}>{row.created.slice(11,16)} · {peopleAlphaStatus(row.status)} · seed {row.seed}{row.current?'':' · earlier source'}</option>{/each}</select></label>
    {/if}
    {#if take}
      <div class="status" class:failed={['failed','uncertain'].includes(take.status)} aria-live="polite"><strong>{peopleAlphaStatus(take.status)}</strong>{#if ['checking','submitting','rendering'].includes(take.status)}<progress aria-label="People AlphaGen pass in progress"></progress>{/if}{#if take.error}<p role="alert">{take.error}</p>{/if}</div>
      <a class="small" href={`${prefix}/${take.id}/source.mp4`} target="_blank" rel="noreferrer">Review prepared RGB input</a>
      {#if !take.current}<p class="notice">The source or model profile changed. Prepare a current version before generating or applying a holdout.</p>{:else if take.status==='prepared'&&!take.backend_current}<p class="notice">The Comfy backend changed. Prepare the input again for this connection.</p>{/if}
      {#if take.status==='uncertain'}<p class="notice">Check the existing Comfy job before starting another pass. Its submission outcome is uncertain.</p>{:else if take.status==='failed'}<p class="notice">This version was not retried. Review the error, then prepare a new seed if you want another pass.</p>{/if}
      <button class="primary" disabled={disabled||!!working||!!rangeIssue||readiness.blocked||!canGeneratePeopleAlpha(take)} onclick={generate}>Generate foreground matte · AlphaGen</button>
      {#if take.status==='done'&&take.asset}
        <div class="review">
          <h4>Review before use</h4><p>Check fine edges, motion blur and unwanted background across the whole shot. This is an automatic foreground prediction, not a selected-person guarantee.</p>
          <label>Review view<select bind:value={view}><option value="checker">Foreground over checkerboard</option><option value="overlay">Matte over the original plate</option><option value="alpha">Alpha only</option></select></label>
          <img class="review-image" src={previewUrl} alt={view==='checker'?'AlphaGen foreground over checkerboard':view==='overlay'?'AlphaGen matte over original plate':'AlphaGen grayscale foreground alpha'}/>
          <label>Review frame · {previewFrame}<input aria-label="People holdout review frame" type="range" min="0" max={project.frames-1} step="1" bind:value={reviewFrame}/></label>
          <button class="small" onclick={()=>reviewFrame=Math.max(0,Math.min(project.frames-1,frame))}>Match timeline frame</button>
          <a href={`${prefix}/${take.id}/alpha.mp4`} target="_blank" rel="noreferrer">Open full alpha video</a>
          <label>Apply to<select aria-label="People holdout target" value={target?.id??''} onchange={event=>targetId=event.currentTarget.value}><option value="">New separate people holdout · keeps collider</option>{#each targets as matte}<option value={matte.id}>{matte.name??matte.id}{matte.holdoutPost?' · optical holdout':''}</option>{/each}</select></label>
          {#if target}<p>Updates the optical mask for every element using this named object. Collision continues to use its original mask.</p>{#if !target.holdoutPost}<p class="notice">Moving an existing combined matte to final compositing may require its FX guides to be generated again. A separate holdout object retains their cache.</p>{/if}{:else}<p>Creates a holdout-only object assigned to this element. Existing collision objects remain available.</p>{/if}
          <label class="check"><input aria-label="I reviewed this full-clip AlphaGen matte" type="checkbox" checked={!!reviewed[take.id]} onchange={event=>reviewed[take!.id]=event.currentTarget.checked}/>I reviewed this full-clip matte and its edges.</label>
          <button class="primary" disabled={disabled||!!working||!canUsePeopleAlpha(take,!!reviewed[take.id])} onclick={approve}>Use reviewed people holdout</button>
        </div>
      {/if}
    {/if}
    {#if activeTargets.length}<div class="active-holdouts"><h4>Active AlphaGen holdouts</h4>{#each activeTargets as matte}<div class="active-row"><span>{matte.name??matte.id}</span>{#if matte.holdoutBaseline!=='none'}<button class="small" disabled={disabled||!!working} onclick={()=>original(matte.id)}>Use original mask for this object</button>{:else}<p>Separate holdout with no original mask. Uncheck this object under “Objects used by this element” to disable it.</p>{/if}</div>{/each}</div>{/if}
    {#if working}<p role="status">{working}</p>{/if}{#if error}<p class="error" role="alert">{error}</p>{/if}
  </div>{/if}
</details>

<style>
  .people-alpha{padding:12px;border:1px solid var(--line);border-radius:9px;background:var(--panel2)}summary{display:list-item;list-style:disclosure-closed}details[open]>summary{list-style:disclosure-open}summary>span{display:block;margin:5px 0 0;font-size:10px;color:var(--muted)}.body,.review,.active-holdouts{display:grid;gap:12px}.body{padding-top:14px}p{margin:0;font-size:11px;line-height:1.6;color:var(--muted)}h4{margin:0;font-size:12px;font-weight:600}fieldset{border:0;padding:12px 0 0;margin:0;display:grid;gap:12px;min-width:0}.range{color:var(--text)}.notice{padding:9px 10px;border:1px solid var(--line);border-radius:6px;background:var(--panel3);color:var(--text)}.status{display:grid;gap:8px;padding:10px;border:1px solid var(--line);border-radius:6px;font-size:11px}.status.failed,.error{color:var(--danger)}progress{width:100%;accent-color:var(--accent)}a{font-size:11px}.review{padding-top:14px;border-top:1px solid var(--line)}.review-image{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:6px}.check{display:flex;align-items:flex-start;gap:8px;line-height:1.5}.check input{flex:none;margin:2px 0 0}.active-holdouts{padding-top:12px;border-top:1px solid var(--line)}.active-row{display:grid;gap:8px;font-size:11px}.error{overflow-wrap:anywhere}
</style>
