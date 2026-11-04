<script lang="ts">
  import {api,type Project,type Effect} from './api';
  import {elementRequest} from './elementFlow';
  import {isSmokePreset,isGrenadeSmokePreset,elementDefaults,elementSettings} from './elementOptions';
  import {connection} from './connection';
  import {collisionMethod,currentDepth,geometryJob,geometryJobStatus,interactionIssue,matteTrackingBody,elementTakeStatus} from './elementWorkflow';
  import DepthSelection from './DepthSelection.svelte';
  import PeopleAlphaPanel from './PeopleAlphaPanel.svelte';
  import MatteTreatment from './MatteTreatment.svelte';
  import ElementFinishing from './ElementFinishing.svelte';
  import ColliderMaskPreview from './ColliderMaskPreview.svelte';
  let {project,selected,frame=0,disabled=false,onchange,onupdated,onprepare,onrender,onrerun,onmatte,onpreview,onconnect}:{project:Project;selected:Effect|null;frame?:number;disabled?:boolean;onchange:()=>void;onupdated:(p:Project)=>void;onprepare:(id:string)=>void;onrender:(id:string)=>void;onrerun:(id:string)=>void;onmatte?:(effectId:string,takeId:string)=>void;onpreview:(id:string)=>void;onconnect:()=>void}=$props();
  type Scope='depth'|'matte'|'geometry'|'model'|'approval'|'reuse'|'review'|'recovery'|'alpha';
  type DepthKind = 'inverse' | 'distance';
  let errors=$state<Record<Scope,string>>({depth:'',matte:'',geometry:'',model:'',approval:'',reuse:'',review:'',recovery:'',alpha:''});
  let working=$state(''),workingScope=$state<Scope|null>(null),capabilities=$state<any>(null);
  let capabilitiesKey=$state('');
  let matteName=$state('Object'),matteKind=$state('hard'),pointX=$state(.5),pointY=$state(.5),referenceFrame=$state(0);
  let depthKind=$state<DepthKind>('inverse');
  let pendingMatte=$state<{pid:string;effectId:string;jobId:string}|null>(null);
  let geometryTimer:ReturnType<typeof setTimeout>;
  let reviewMovies=$state<Record<string,{url:string;inputKey:string;frames:number;fps:number}>>({});
  let alphaMovies=$state<Record<string,{url:string;frames:number;fps:number;inputKey:string}>>({});
  const defaults=elementDefaults;
  const configured=$derived(!!$connection?.configured);
  const modelProfileKey=$derived(JSON.stringify([project.id,project.settings.element_models??{},project.settings.element_model_revisions??{},$connection?.mode,$connection?.comfyui_url,$connection?.report?.checked_at]));
  const checkedProfile=$derived(capabilitiesKey===modelProfileKey?capabilities:null);
  const customModels=$derived(Object.values(project.settings.element_models??{}).some(name=>name.trim().length>0));
  const modelChecks=$derived((checkedProfile?.checks??(!customModels&&$connection?.report?.connected?$connection.report.checks:[])??[]).filter((check:any)=>['rgb','alpha'].includes(check.id)&&check.required!==false));
  const missingModels=$derived(modelChecks.filter((check:any)=>check.status==='missing'));
  const exactModelsReady=$derived(!!checkedProfile&&modelChecks.length===2&&modelChecks.every((check:any)=>check.status==='ready'));
  const modelsBlocked=$derived(missingModels.length>0||customModels&&!exactModelsReady||!!checkedProfile&&!exactModelsReady);
  const modelNotice=$derived(missingModels.some((check:any)=>check.id==='alpha')?'AlphaGen model is not installed on this Comfy backend. Add it in model setup before rendering.':missingModels.some((check:any)=>check.id==='rgb')?'The required LTX Fluid model is not installed on this Comfy backend. Add it in model setup before rendering.':customModels&&!checkedProfile?'Custom model names are selected. Check these models before rendering.':checkedProfile&&!exactModelsReady?checkedProfile.error??'The selected model profile is not verified ready. Check its model setup before rendering.':'');
  const mode=$derived(collisionMethod(project.geometry));
  const depthReady=$derived(currentDepth(project.geometry));
  const issue=$derived(interactionIssue(project.geometry));
  const depthJob=$derived(geometryJob(project.renders,'depth'));
  const matteJob=$derived(geometryJob(project.renders,'matte'));
  const rows=$derived(project.element_takes?.filter(t=>t.effect_id===selected?.id)??[]);
  const currentGuide=$derived(rows.find(t=>t.current&&t.backend_current!==false));
  const assigned=$derived(selected?.element?.colliders??[]);
  $effect(()=>{referenceFrame=project.geometry?.reference_frame??0;});
  $effect(()=>{depthKind=project.geometry?.depth?.kind??'inverse';});
  $effect(()=>{
    const pending=pendingMatte;
    if(!pending||pending.pid!==project.id)return;
    const job=project.renders.find(r=>r.id===pending.jobId);
    if(job?.status==='error'){pendingMatte=null;return;}
    if(job?.status==='done'&&project.geometry?.mattes.some(m=>m.id===pending.jobId)){
      assignToEffect(pending.effectId,pending.jobId);pendingMatte=null;
    }
  });
  async function work(scope:Scope,label:string,run:()=>Promise<unknown>,pid=project.id,mergeEffectId?:string){
    if(project.id===pid){working=label;workingScope=scope;errors[scope]='';}
    try{await run();const fresh=await api.project(pid);if(project.id===pid){if(mergeEffectId)objectAdded(fresh,mergeEffectId);else onupdated(fresh);}}
    catch(e){if(project.id===pid)errors[scope]=e instanceof Error?e.message:String(e);}
    finally{if(project.id===pid){working='';workingScope=null;}}
  }
  function init(){if(selected)selected.element=elementSettings(selected);}
  function guideNumber(key:'cadence'|'frame_blend',input:HTMLInputElement){
    if(!input.checkValidity()||!Number.isInteger(input.valueAsNumber))return;
    init();selected!.element![key]=input.valueAsNumber;onchange();
  }
  function assignToEffect(effectId:string,id:string){const effect=project.effects.find(e=>e.id===effectId);if(!effect)return;effect.element=elementSettings(effect);effect.element.colliders=[...new Set([...effect.element.colliders,id])];onchange();}
  function assign(id:string,yes:boolean){init();selected!.element!.colliders=yes?[...new Set([...selected!.element!.colliders,id])]:selected!.element!.colliders.filter(x=>x!==id);onchange();}
  function geometryPayload(){const g=project.geometry??{reference_frame:0,focal_pixels:900,depth_scale:4,depth_bias:.05,depth:null,mattes:[]};return {reference_frame:g.reference_frame,focal_pixels:g.focal_pixels,depth_scale:g.depth_scale,depth_bias:g.depth_bias,collision_mode:collisionMethod(g),mattes:g.mattes.map(({id,role,kind,thickness,softRadius,colliderBlur,colliderDisplacement,drag,depthOffset})=>({id,role,kind,thickness,softRadius,colliderBlur,colliderDisplacement,drag,depthOffset}))};}
  async function writeGeometry(pid:string,payload:ReturnType<typeof geometryPayload>){await work('geometry','Saving interaction…',()=>elementRequest(`/api/projects/${pid}/geometry`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),pid);}
  async function geometrySave(){clearTimeout(geometryTimer);await writeGeometry(project.id,geometryPayload());}
  async function changeMethod(value:'matte2d'|'depth3d'){
    project.geometry??={reference_frame:0,focal_pixels:900,depth_scale:4,depth_bias:.05,depth:null,mattes:[]};
    project.geometry.collision_mode=value;await geometrySave();
  }
  async function changeDepthKind(kind:DepthKind){
    const depth=project.geometry?.depth;
    if(!depth){depthKind=kind;return;}
    const pid=project.id;
    await work('depth','Updating depth interpretation…',()=>elementRequest(`/api/projects/${pid}/geometry`,{
      method:'PATCH',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({depth_interpretation:{id:depth.id,kind}}),
    }),pid);
    if(project.id===pid)depthKind=project.geometry?.depth?.kind??'inverse';
  }
  function colliderChanged(matte:any,key:'colliderBlur'|'colliderDisplacement',value:number){matte[key]=value;clearTimeout(geometryTimer);const pid=project.id,payload=geometryPayload();geometryTimer=setTimeout(()=>writeGeometry(pid,payload),300);}
  async function upload(event:Event,kind:'depth'|'mattes'){
    const file=(event.currentTarget as HTMLInputElement).files?.[0];if(!file)return;
    const pid=project.id,effectId=selected?.id;
    await work(kind==='depth'?'depth':'matte',kind==='depth'?'Importing depth…':'Importing matte…',async()=>{
      const fd=new FormData();fd.append('file',file);fd.append('name',matteName);fd.append('kind',kind==='depth'?depthKind:matteKind);fd.append('reference_frame',String(referenceFrame));
      const result=await elementRequest(`/api/projects/${pid}/geometry/${kind}`,{method:'POST',body:fd});
      if(kind==='mattes'&&effectId&&project.id===pid&&result.asset?.id)assignToEffect(effectId,result.asset.id);
    },pid);
  }
  async function generate(kind:'depth'|'matte'){
    if(!configured){errors[kind]='Connect Comfy before generating. You can import a local file instead.';return;}
    const pid=project.id,effectId=selected?.id;
    await work(kind,kind==='depth'?'Starting depth generation…':'Starting matte tracking…',async()=>{
      const body=kind==='depth'?{kind,reference_frame:referenceFrame}:matteTrackingBody(matteName,matteKind==='soft',pointX,pointY);
      const job=await elementRequest(`/api/projects/${pid}/geometry/generate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      if(kind==='matte'&&effectId&&project.id===pid)pendingMatte={pid,effectId,jobId:job.id};
    },pid);
  }
  async function usePeopleMatte(){const pid=project.id,effectId=selected?.id;await work('reuse','Adding the existing people matte…',()=>elementRequest(`/api/projects/${pid}/geometry/use-people-matte`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(effectId?{effect_id:effectId}:{})}),pid,effectId);}
  function pickPoint(event:MouseEvent){const target=event.currentTarget as HTMLElement,r=target.getBoundingClientRect();pointX=event.detail===0 ? .5 : Math.max(0,Math.min(1,(event.clientX-r.left)/r.width));pointY=event.detail===0 ? .5 : Math.max(0,Math.min(1,(event.clientY-r.top)/r.height));}
  async function approve(takeId:string){await work('approval','Approving this element…',()=>elementRequest(`/api/projects/${project.id}/element-takes/${takeId}/approve`,{method:'POST'}));}
  async function reviewCoreAlpha(takeId:string){const pid=project.id,inputKey=reviewInputKey();await work('alpha','Preparing treated matte · local/free…',async()=>{await api.patch(pid,{effects:project.effects});const result=await elementRequest(`/api/projects/${pid}/element-takes/${takeId}/effective-alpha-review`,{method:'POST'});if(project.id===pid&&reviewInputKey()===inputKey)alphaMovies[takeId]={...result,inputKey};},pid);}
  function reviewInputKey(){return JSON.stringify([project.id,selected?.id,selected?.element,project.geometry,project.native_profile]);}
  async function reviewOverPlate(takeId:string){
    const pid=project.id,inputKey=reviewInputKey();
    await work('review','Building local SDR review over the original plate…',async()=>{
      await api.patch(pid,{effects:project.effects});
      const result=await elementRequest(`/api/projects/${pid}/element-takes/${takeId}/review`,{method:'POST'});
      if(project.id===pid&&reviewInputKey()===inputKey)reviewMovies[takeId]={...result,inputKey};
    },pid);
  }
  async function recoverDownloaded(takeId:string,stage:string,sha:string){
    const pid=project.id;
    await work('recovery',`Revalidating downloaded ${stage} locally…`,()=>elementRequest(`/api/projects/${pid}/element-takes/${takeId}/recover-downloaded`,{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stage,expected_raw_sha256:sha})
    }),pid);
  }
  function objectAdded(fresh:Project,effectId=selected?.id){if(fresh.id!==project.id)return;const live=project.effects.find(e=>e.id===effectId),saved=fresh.effects.find(e=>e.id===effectId);if(live&&saved?.element){live.element={...defaults,...live.element};live.element.colliders=[...saved.element.colliders];}onupdated(fresh);}
  async function check(){const key=modelProfileKey;await work('model','Checking models…',async()=>{capabilities=await elementRequest(`/api/projects/${project.id}/elements/capabilities`);capabilitiesKey=key;});}
  function modelSetting(role:string,value:string,revision=false){const key=revision?'element_model_revisions':'element_models',map={...project.settings[key]};if(value.trim())map[role]=value.trim();else delete map[role];project.settings[key]=map;capabilities=null;onchange();}
  function selectAlphaBf16(){
    const profile=project.element_alpha_bf16_models;if(!profile)return;
    const revisions={...project.settings.element_model_revisions};
    for(const role of Object.keys(profile))delete revisions[role];
    project.settings.element_model_revisions=revisions;
    project.settings.element_models={...project.settings.element_models,...profile};
    capabilities=null;onchange();
  }
</script>

{#if selected}
  {@const cfg=elementSettings(selected)}
  <details class="block interaction-step" id="element-interaction">
    <summary><strong>2. Interaction <span>Optional</span></strong><span>{assigned.length?`${assigned.length} assigned`:'Free flow'}</span></summary>
    <div class="step-body">
      <p class="small muted">Skip this step for free-flowing FX. Choose the objects this element should collide with or pass behind.</p>
      <fieldset disabled={disabled||!!working}>
        <label>Collision method · shared by this shot<select aria-label="Collision method" value={mode} onchange={e=>changeMethod(e.currentTarget.value as 'matte2d'|'depth3d')}><option value="matte2d">2D matte · no depth needed</option><option value="depth3d">Depth 3D · surfaces in the room</option></select></label>
        <p class="small muted">{mode==='matte2d'?'2D mattes block and drag the fluid in image space. Import or track a matte below; no depth pass is needed.':'Depth places the assigned mattes on finite surfaces so fluid can wrap around them. This reconstruction is approximate.'}</p>
      </fieldset>
      {#if mode==='matte2d'&&project.people_matte?.current}<div class="setup-card"><h3>Fast collision matte · SAM2</h3><p class="small muted">This shot already has a tracked people matte. Reuse it locally for fluid interaction.</p><button disabled={disabled||!!working} onclick={usePeopleMatte}>Use existing people matte</button>{#if workingScope==='reuse'}<p class="small" role="status">{working}</p>{/if}{#if errors.reuse}<p class="inline-error" role="alert">{errors.reuse}</p>{/if}</div>{/if}
      <PeopleAlphaPanel {project} effectId={selected.id} {frame} disabled={disabled||!!working} {onupdated} onattached={objectAdded} {onconnect}/>

      {#if mode==='depth3d'}
        <section class="setup-card" aria-label="Depth setup">
          <h3>Depth pass</h3>
          <fieldset disabled={disabled||!!working}>
            <label>Depth reference frame<input type="number" min="0" max={project.frames-1} bind:value={referenceFrame}/></label>
            <button disabled={!configured} onclick={()=>generate('depth')}>Generate depth · Comfy</button>
            <label>Depth convention<select aria-label="Depth convention" bind:value={depthKind} onchange={e=>changeDepthKind(e.currentTarget.value as DepthKind)}><option value="inverse">Bright is near · inverse depth</option><option value="distance">Bright is far · distance</option></select></label>
            <label class="small">Or import depth PNG<input type="file" accept="image/png" onchange={e=>upload(e,'depth')}/></label>
            <p class="small muted">Match the PNG’s convention. You can correct an imported pass here without uploading it again.</p>
          </fieldset>
          {#if !configured}<p class="small setup-notice">Comfy is not connected. Connect it to generate depth, or import a depth PNG. 2D matte collision works without depth.</p><button class="small" onclick={onconnect}>Connect Comfy</button>{:else}<p class="small muted">Uses the selected Comfy connection. Required depth models are checked when you submit; a connected account does not guarantee model availability.</p>{/if}
          {#if workingScope==='depth'}<p class="small" role="status">{working}</p>{/if}
          {#if errors.depth}<p class="inline-error" role="alert">{errors.depth}</p>{/if}
          {#if depthJob}<div class="job-status" class:failed={depthJob.status==='error'} aria-live="polite"><strong>{['done','error'].includes(depthJob.status)?'Last depth job':'Depth'} · {geometryJobStatus(depthJob)}</strong>{#if !['done','error'].includes(depthJob.status)}<progress aria-label="Depth generation in progress"></progress>{/if}{#if depthJob.error}<p role="alert">{depthJob.error}</p>{/if}</div>{/if}
          {#if depthReady}<p class="small ready">Depth connected · frame {project.geometry?.reference_frame}.</p>{:else}<p class="small setup-notice">{issue}</p>{/if}
          <DepthSelection {project} effectId={selected.id} disabled={disabled||!!working||!depthReady} onupdated={objectAdded}/>
          {#if project.geometry}<details><summary>Depth calibration</summary><fieldset class="adv" disabled={disabled||!!working}><label>Focal length (logical pixels)<input type="number" min="100" max="10000" bind:value={project.geometry.focal_pixels} onchange={geometrySave}/></label><label>Depth scale<input type="number" min=".01" max="1000" step=".1" bind:value={project.geometry.depth_scale} onchange={geometrySave}/></label><label>Depth bias<input type="number" min="0" max="10" step=".05" bind:value={project.geometry.depth_bias} onchange={geometrySave}/></label></fieldset></details>{/if}
        </section>
      {/if}

      <div class="assigned-objects">
        <h3>Objects used by this element</h3>
        {#each project.geometry?.mattes??[] as matte}
          <label class="check"><input aria-label={`Use ${matte.name??matte.id} for this element`} type="checkbox" checked={cfg.colliders.includes(matte.id)} disabled={disabled||!!working} onchange={e=>assign(matte.id,e.currentTarget.checked)}/>{matte.name??matte.id}<span class="chip">{matte.holdoutPost&&matte.role==='holdout'?'Optical holdout':matte.static?'Static':'Tracked'}</span></label>
          <details class="matte-settings"><summary>{matte.name??matte.id} · controls and preview</summary><fieldset class="adv" disabled={disabled||!!working}>
            {#if matte.holdoutBaseline==='none'}<p class="small muted">Optical holdout only. Use a separate tracked matte for fluid collision.</p>{:else}<label>Role<select aria-label={`Role for ${matte.name??matte.id}`} bind:value={matte.role} onchange={geometrySave}><option value="both">Collider + holdout</option><option value="collide">Collider only</option><option value="holdout">Holdout only</option></select></label>{/if}
            {#if matte.role!=='holdout'}
            <label>Surface<select bind:value={matte.kind} onchange={geometrySave}><option value="hard">Hard · shelf / wall</option><option value="soft">Soft · moving person</option></select></label>
            <label>Collision boundary · {matte.colliderDisplacement??0}px<input aria-label={`Collider displacement for ${matte.name??matte.id}`} type="range" min="-128" max="128" step="1" value={matte.colliderDisplacement??0} oninput={e=>colliderChanged(matte,'colliderDisplacement',+e.currentTarget.value)}/></label>
            <p class="small muted">Positive expands the collision mask; negative shrinks it before blur.</p>
            <label>Collider blur · {matte.colliderBlur??0}px<input aria-label={`Collider blur for ${matte.name??matte.id}`} type="range" min="0" max="128" step="1" value={matte.colliderBlur??0} oninput={e=>colliderChanged(matte,'colliderBlur',+e.currentTarget.value)}/></label>
            {/if}
            {#if matte.holdout?.take_id}<div class="matte-preview" class:single={matte.role==='holdout'}>{#if matte.role!=='holdout'}<figure><ColliderMaskPreview src={`/api/projects/${project.id}/geometry/mattes/${matte.id}/frames/${matte.static?0:frame}.png?max_edge=256`} displacement={matte.colliderDisplacement??0} blur={matte.colliderBlur??0} name={matte.name??matte.id}/><figcaption>Collision · mask adjustments</figcaption></figure>{/if}<figure><img src={`/api/projects/${project.id}/people-alpha/${matte.holdout.take_id}/frames/${frame}.png`} alt="Active AlphaGen optical holdout"/><figcaption>Optical holdout · reviewed AlphaGen</figcaption></figure></div><p class="small muted">{matte.role==='holdout'?'This object supplies the optical holdout at final compositing. Use separate tracked objects for collision.':'The AlphaGen mask is applied at final compositing. Collision retains its independent original mask.'}</p>{:else}
            <div class="matte-preview"><figure><img src={`/api/projects/${project.id}/geometry/mattes/${matte.id}/frames/${matte.static?0:frame}.png?max_edge=256`} alt="Original holdout matte"/><figcaption>Holdout · original edge</figcaption></figure><figure><ColliderMaskPreview src={`/api/projects/${project.id}/geometry/mattes/${matte.id}/frames/${matte.static?0:frame}.png?max_edge=256`} displacement={matte.colliderDisplacement??0} blur={matte.colliderBlur??0} name={matte.name??matte.id}/><figcaption>Collider · adjusted mask</figcaption></figure></div>
            <p class="small muted">Boundary displacement and blur adjust the physical interaction. The holdout keeps the original edge. Only checked objects affect this element.</p>
            {/if}
            {#if mode==='depth3d'&&matte.role!=='holdout'}<label>Thickness<input type="number" min=".002" max="2" step=".02" bind:value={matte.thickness} onchange={geometrySave}/></label><label>Surface depth offset<input type="number" min="-5" max="5" step=".05" bind:value={matte.depthOffset} onchange={geometrySave}/></label>{/if}
            {#if matte.kind==='soft'&&matte.role!=='holdout'}<label>Body drag radius<input type="range" min="0" max="64" step="1" bind:value={matte.softRadius} onchange={geometrySave}/></label><label>Drag<input type="range" min="0" max="80" bind:value={matte.drag} onchange={geometrySave}/></label>{/if}
          </fieldset></details>
        {:else}<p class="small muted">No mattes yet. Add one below, or leave this step empty.</p>{/each}
        {#if assigned.length}<button class="ghost small" disabled={disabled||!!working} onclick={()=>{init();selected!.element!.colliders=[];onchange();}}>Clear this element’s interaction</button>{/if}
      </div>

      <details class="setup-card"><summary>Add an object or person matte</summary><div class="adv">
        <fieldset disabled={disabled||!!working}>
          <label>Object name<input bind:value={matteName} maxlength="80"/></label><label>Surface<select bind:value={matteKind}><option value="hard">Hard · shelf / wall</option><option value="soft">Soft · moving person</option></select></label>
          <label>Import matte · static PNG or tracked PNG ZIP<input type="file" accept="image/png,.zip" onchange={e=>upload(e,'mattes')}/></label>
          <details><summary>Or track an object with Comfy</summary><div class="adv"><p class="small muted">Click the object in frame 0, then start tracking. The result is assigned to this element.</p>
            <button class="point-picker" aria-label="Choose an object tracking point on frame 0" onclick={pickPoint}><img src={api.frameUrl(project.id,0,'plate')} alt="First frame for matte tracking"/><span class="point" style:left={`${pointX*100}%`} style:top={`${pointY*100}%`}></span></button>
            <div class="row"><label>X<input type="number" min="0" max="1" step=".01" bind:value={pointX}/></label><label>Y<input type="number" min="0" max="1" step=".01" bind:value={pointY}/></label></div>
            <button disabled={!configured} onclick={()=>generate('matte')}>Track matte · Comfy</button>
          </div></details>
        </fieldset>
        {#if !configured}<p class="small setup-notice">Importing a matte is local. Tracking a new one requires Comfy.</p><button class="small" onclick={onconnect}>Connect Comfy</button>{/if}
        {#if workingScope==='matte'}<p class="small" role="status">{working}</p>{/if}{#if errors.matte}<p class="inline-error" role="alert">{errors.matte}</p>{/if}
      </div></details>
      {#if matteJob}<div class="job-status" class:failed={matteJob.status==='error'} aria-live="polite"><strong>{['done','error'].includes(matteJob.status)?'Last matte tracking job':'Matte tracking'} · {geometryJobStatus(matteJob)}</strong>{#if !['done','error'].includes(matteJob.status)}<progress aria-label="Matte tracking in progress"></progress>{/if}{#if matteJob.error}<p role="alert">{matteJob.error}</p>{/if}</div>{/if}
      {#if workingScope==='geometry'}<p class="small" role="status">{working}</p>{/if}{#if errors.geometry}<p class="inline-error" role="alert">{errors.geometry}</p>{/if}
    </div>
  </details>

  <section class="block" id="element-simulation" aria-label="Review the simulation">
    <h2>3. Review simulation</h2>
    <fieldset disabled={disabled||!!working}>
      {#if isSmokePreset(selected.preset)}
        <label>Density · {(cfg.density??1).toFixed(2)}×<input aria-label="Smoke density" type="range" min="0" max="4" step=".05" value={cfg.density??1} oninput={e=>{init();selected!.element!.density=+e.currentTarget.value;onchange();}}/></label>
        <label>Brightness · {(cfg.brightness??1).toFixed(2)}×<input aria-label="Smoke brightness" type="range" min="0" max="4" step=".05" value={cfg.brightness??1} oninput={e=>{init();selected!.element!.brightness=+e.currentTarget.value;onchange();}}/></label>
        <p class="small muted">Adjusts smoke thickness and overall brightness in the preview and the next LTX guide.</p>
      {/if}
      <label>Light intensity · {cfg.light_intensity.toFixed(2)}×<input aria-label="Light intensity" type="range" min="0" max="4" step=".05" value={cfg.light_intensity} oninput={e=>{init();selected!.element!.light_intensity=+e.currentTarget.value;onchange();}}/></label>
      <label>Fill intensity · {cfg.fill_intensity.toFixed(2)}×<input aria-label="Fill intensity" type="range" min="0" max="2" step=".01" value={cfg.fill_intensity} oninput={e=>{init();selected!.element!.fill_intensity=+e.currentTarget.value;onchange();}}/></label>
      <label>Light rotation · {cfg.light_rotation}°<input aria-label="Light rotation" type="range" min="-180" max="180" step="1" value={cfg.light_rotation} oninput={e=>{init();selected!.element!.light_rotation=+e.currentTarget.value;onchange();}}/></label>
      <label>Light elevation · {cfg.light_elevation}°<input aria-label="Light elevation" type="range" min="-90" max="90" step="1" value={cfg.light_elevation} oninput={e=>{init();selected!.element!.light_elevation=+e.currentTarget.value;onchange();}}/></label>
      <p class="small muted">Lower fill keeps the shadow side dark. Negative elevation lights from below. Lighting affects the live volume and captured guide.</p>
      {#if isSmokePreset(selected.preset)}
        <label>Painted rim strength · {Math.round(cfg.rim_strength*100)}%<input aria-label="Painted rim strength" type="range" min="0" max="1" step=".05" value={cfg.rim_strength} oninput={e=>{init();selected!.element!.rim_strength=+e.currentTarget.value;onchange();}}/></label>
        <label>Painted rim width · {cfg.rim_width}px<input aria-label="Painted rim width" type="range" min="1" max="128" step="1" value={cfg.rim_width} oninput={e=>{init();selected!.element!.rim_width=+e.currentTarget.value;onchange();}}/></label>
        <p class="small muted">Painted-guide controls: concentrate highlights along contours facing the light. 0% keeps the normal painting. Width is scaled from a 1024px guide.</p>
        {#if cfg.guide_style!=='painted'&&cfg.rim_strength>0}<p class="small setup-notice">Select Painted guide style to use the rim treatment.</p>{/if}
        {#if selected.preset==='smoke_plume_hq'}<button class="ghost small" onclick={()=>{init();Object.assign(selected!.element!,{fill_intensity:.03,light_intensity:1,light_rotation:105,light_elevation:-10,rim_width:16,rim_strength:1,guide_style:'painted',frame_blend:20,cadence:10});onchange();}}>Match left-rim example</button>{/if}
      {/if}
      <label>Duration (seconds)<input type="number" min=".1" max="5" step="any" value={cfg.duration_s} onchange={e=>{init();selected!.element!.duration_s=+e.currentTarget.value;onchange();}}/></label>
      <p class="small muted">You can also drag the effect’s right edge on the timeline to set its end.</p>
      {#if selected.preset==='explosion'||isGrenadeSmokePreset(selected.preset)}
        {@const dispersionKey=selected.preset==='explosion'?'wind_dispersion':'smoke_dispersion'}
        <label>Wind dispersion · {cfg[dispersionKey].toFixed(2)}<input aria-label="Wind dispersion" type="range" min="0" max="2" step=".05" value={cfg[dispersionKey]} oninput={e=>{init();selected!.element![dispersionKey]=+e.currentTarget.value;onchange();}}/></label>
        <p class="small muted">Spread and thin smoke across the airflow. 0 keeps directional wind without added dispersion.{(selected.strength??0)===0?' Raise Wind above 0 to enable added dispersion.':''}</p>
      {/if}
      <button disabled={!!issue} onclick={()=>onpreview(selected!.id)}>Play simulation · local preview</button>
      <button disabled={!!issue} onclick={()=>onprepare(selected!.id)}>Prepare guide video · no model spend</button>
      {#if currentGuide}<a class="small" href={`/api/projects/${project.id}/element-takes/${currentGuide.id}/guide.mp4`} target="_blank" rel="noreferrer">Review prepared guide video ↗</a>
        {#if cfg.cadence>1}<p class="small muted">The guide shows only sampled frames. Use Play simulation to check the motion between them.</p>{/if}
      {/if}
      {#if issue}<p class="small setup-notice">{issue}</p>{/if}
      <div class="adv">
        <label>Painted frame submission · every Nth frame<input aria-label="Guide frame cadence" type="number" min="1" max="120" step="1" value={cfg.cadence} oninput={e=>guideNumber('cadence',e.currentTarget)}/></label>
        <label>Frame blend · {cfg.frame_blend} frames<input aria-label="Frame blend" type="number" min="1" max="48" step="1" value={cfg.frame_blend} oninput={e=>guideNumber('frame_blend',e.currentTarget)}/></label>
        <p class="small muted">Defaults: every 10th frame, 10-frame blend. Blend the complete painted sequence before selecting frames. 1 disables blending.</p>
        <label>Painted-guide turbulence · {cfg.guide_displacement}px<input aria-label="Painted guide displacement" type="range" min="0" max="100" step="1" value={cfg.guide_displacement} oninput={e=>{init();selected!.element!.guide_displacement=+e.currentTarget.value;onchange();}}/></label>
        <label>Displacement falloff · {cfg.guide_displacement_blur}px<input aria-label="Painted displacement falloff" type="range" min="1" max="128" step="1" value={cfg.guide_displacement_blur} oninput={e=>{init();selected!.element!.guide_displacement_blur=+e.currentTarget.value;onchange();}}/></label>
        <p class="small muted">Adds evolving edge turbulence around checked colliders after blending. 0 keeps the simulated shape. Guide-only; inspect the prepared video.</p>
        <button class="ghost small" onclick={()=>{init();selected!.element!.cadence=10;selected!.element!.frame_blend=10;selected!.element!.guide_style='painted';onchange();}}>Use painted defaults · 10 / 10</button>
        <label>Guide style<select value={cfg.guide_style} onchange={e=>{init();selected!.element!.guide_style=e.currentTarget.value as 'painted'|'rendered';onchange();}}><option value="painted">Painted · default</option><option value="rendered">Rendered volume</option></select></label>
      </div>
      <details><summary>Advanced guide settings</summary><div class="adv">
        {#if mode==='depth3d'}<label>Origin depth offset · positive toward camera<input type="number" min="-5" max="5" step=".05" value={cfg.depth_offset} onchange={e=>{init();selected!.element!.depth_offset=+e.currentTarget.value;onchange();}}/></label>{/if}
        {#each [['blur','Guide blur',0,24,.5],['shading','Guide shading contrast',.1,4,.1]] as [key,label,min,max,step]}<label>{label}<input type="range" {min} {max} {step} value={cfg[key as keyof typeof cfg] as number} oninput={e=>{init();(selected!.element as any)[key]=+e.currentTarget.value;onchange();}}/></label>{/each}
        <label>Seed<input type="number" min="0" value={cfg.seed} onchange={e=>{init();selected!.element!.seed=+e.currentTarget.value;onchange();}}/></label>
        <label>AlphaGen seed<input type="number" min="0" max="9007199254740991" value={cfg.alpha_seed} onchange={e=>{init();selected!.element!.alpha_seed=+e.currentTarget.value;onchange();}}/></label>
        <p class="small muted">The matte has its own seed. Changing only this seed keeps the RGB reusable when the captured guide is unchanged. Review the new matte before approving.</p>
        <button class="ghost small" onclick={()=>{init();selected!.element!.seed=(selected!.element!.seed+1)%2147483647;onchange();}}>New seed variation</button>
      </div></details>
    </fieldset>
    <p class="small muted">Check placement, scale, wind and interaction here before spending on a model render.</p>
  </section>

  <section class="block" id="element-generation" aria-label="Generate the selected element">
    <h2>4. Generate selected element</h2>
    <ElementFinishing effect={selected} disabled={disabled||!!working} {onchange}/>
    {#if !selected.element?.body_matte}<MatteTreatment effect={selected} disabled={disabled||!!working} {onchange}/>{/if}
    <p class="small muted">LTX finishes this effect, then AlphaGen extracts its alpha. The original plate is retained.</p>
    {#if !configured}<p class="small setup-notice">Connect Comfy to generate LTX + alpha. Simulation and imported-matte setup remain available.</p><button onclick={onconnect}>Connect Comfy</button>{/if}
    {#if configured&&modelNotice}<p class="small setup-notice" role="status">{modelNotice}</p><button disabled={disabled||!!working} onclick={check}>Check selected models</button>{/if}
    {#if workingScope==='model'}<p class="small" role="status">{working}</p>{/if}{#if errors.model}<p class="inline-error" role="alert">{errors.model}</p>{/if}
    <button class="primary" disabled={disabled||!!working||!configured||!!issue||modelsBlocked} onclick={()=>onrender(selected!.id)}>Generate selected · LTX + alpha</button>
    {#if rows.length}<details><summary>New variation</summary><div class="adv"><p class="small muted">Rerun this element with a new seed. Other approved elements are reused. This spends model credits.</p><button disabled={disabled||!!working||!configured||!!issue||modelsBlocked} onclick={()=>onrerun(selected!.id)}>Rerun selected element · new variation</button></div></details>{/if}
    {#if workingScope==='approval'}<p class="small" role="status">{working}</p>{/if}{#if errors.approval}<p class="inline-error" role="alert">{errors.approval}</p>{/if}
    {#if workingScope==='review'}<p class="small" role="status">{working}</p>{/if}{#if errors.review}<p class="inline-error" role="alert">{errors.review}</p>{/if}
    {#if workingScope==='recovery'}<p class="small" role="status">{working}</p>{/if}{#if errors.recovery}<p class="inline-error" role="alert">{errors.recovery}</p>{/if}
    {#if workingScope==='alpha'}<p class="small" role="status">{working}</p>{/if}{#if errors.alpha}<p class="inline-error" role="alert">{errors.alpha}</p>{/if}
    {#each rows as take}<div class="take-row"><div class="row"><strong class="small">{take.approved?'Approved version':take.current?'Current version':'Earlier version'}</strong><span class="chip">{elementTakeStatus(take.status)}</span><span class="small muted">{take.created.slice(11,16)}</span></div>
      <div class="take-links"><a href={`/api/projects/${project.id}/element-takes/${take.id}/guide.mp4`} target="_blank" rel="noreferrer">Guide</a>{#if take.assets?.rgb}<a href={`/api/projects/${project.id}/element-takes/${take.id}/rgb.mp4`} target="_blank" rel="noreferrer">RGB</a>{/if}{#if take.assets?.alpha}<a href={`/api/projects/${project.id}/element-takes/${take.id}/alpha.mp4`} target="_blank" rel="noreferrer">Alpha</a>{/if}</div>
      {#if take.status==='error'&&take.assets?.rgb&&!take.assets?.alpha}<p class="small setup-notice">RGB completed and is retained. Alpha is incomplete; this version cannot be approved or composited.</p>{/if}
      {#each take.recoverable_stages??[] as candidate}<div class="setup-card"><p class="small muted">A completed {candidate.stage} download is retained. Revalidation uses the saved file; no upload or model submission. Review and approval are still required.</p><button class="small" disabled={disabled||!!working} onclick={()=>recoverDownloaded(take.id,candidate.stage,candidate.raw_sha256)}>Revalidate downloaded {candidate.stage} · local/free</button></div>{/each}
      {#if take.local_recovery}<p class="small muted">Downloaded {take.local_recovery.stage} recovered locally · no new model job.</p>{/if}
      {#if take.status==='done'}
        {#if take.current}
          <button class="small" disabled={disabled||!!working} onclick={()=>reviewCoreAlpha(take.id)}>Review treated matte · local/free</button>
          {#if alphaMovies[take.id]?.inputKey===reviewInputKey()}
            <div class="element-review"><p class="small muted">Alpha used for compositing, before holdouts and opacity. Raw AlphaGen is retained in the Alpha link above.</p>
              <!-- svelte-ignore a11y_media_has_caption (data-alpha review has no speech) -->
              <video controls preload="metadata" src={alphaMovies[take.id].url} aria-label="Treated element matte review"></video>
            </div>
          {/if}
        {/if}
        <button class="small" disabled={disabled||!!working||!take.current} onclick={()=>reviewOverPlate(take.id)}>Review over plate · local/free</button>
        {@const review=reviewMovies[take.id]}
        {#if take.current&&review?.inputKey===reviewInputKey()}
          <div class="element-review"><p class="small muted">SDR review · original plate + this element only · {review.frames} frames at {review.fps}fps. Approval and final take are unchanged.</p>
            <!-- svelte-ignore a11y_media_has_caption (visual FX review of the user's source clip) -->
            <video controls preload="metadata" src={review.url} aria-label="Selected element SDR review over the original plate"></video>
            <a class="small" href={review.url} target="_blank" rel="noreferrer">Open full-size SDR review</a>
          </div>
        {/if}
        <button class="small" disabled={disabled||!!working||!take.current||take.approved} onclick={()=>approve(take.id)}>{take.approved?'Approved · ready for Finish':take.current?'Approve this version':'Guide changed'}</button>
      {/if}
      {#if take.approval_outdated}<p class="small setup-notice">The guide changed. Generate and approve a current version before finishing.</p>{/if}{#if take.error}<p class="inline-error" role="alert">{take.error}</p>{/if}
    </div>{/each}
    <p class="small muted">Review the RGB and alpha, approve the version you want, then continue to Finish.</p>
  </section>
{:else}
  <div class="block"><p class="small muted">Choose an effect and click the plate to place it. Its interaction, simulation and generation steps will appear here.</p></div>
{/if}

<div class="block"><details><summary>Advanced · Comfy models</summary><div class="adv">
  <button disabled={disabled||!!working||!configured} onclick={check}>Check Fluid + AlphaGen models</button>
  {#if capabilities}<pre class="small">{JSON.stringify(capabilities,null,2)}</pre>{/if}{#if errors.model}<p class="inline-error" role="alert">{errors.model}</p>{/if}
  <p class="small muted">Connection and model readiness are separate. Both required model stages must be available before a beauty render can start.</p>
  {#if project.element_alpha_bf16_models}
    <button disabled={disabled||!!working} aria-pressed={Object.entries(project.element_alpha_bf16_models).every(([role,name])=>project.settings.element_models?.[role]===name)} onclick={selectAlphaBf16}>Use full BF16 for AlphaGen</button>
    <p class="small muted">Try the full-precision models if AlphaGen returns colour. Check model availability and GPU memory first. This leaves the RGB model settings unchanged.</p>
  {/if}
  <details><summary>Installed model names / revisions</summary><fieldset class="adv" disabled={disabled||!!working}>{#each Object.entries(project.element_model_defaults??{}) as [role,name]}<label>{role}<input placeholder={name} value={project.settings.element_models?.[role]??''} onchange={e=>modelSetting(role,e.currentTarget.value)}/></label><label class="small">Declared revision / hash<input value={project.settings.element_model_revisions?.[role]??''} onchange={e=>modelSetting(role,e.currentTarget.value,true)}/></label>{/each}</fieldset></details>
</div></details></div>

<style>
  .block{display:grid;gap:14px;padding:18px;border-bottom:1px solid var(--line)}h2,h3,p{margin:0}h3{font-size:12px;font-weight:600}.adv,.step-body{display:grid;gap:12px;padding-top:14px}fieldset{border:0;padding:0;margin:0;min-width:0;display:grid;gap:12px}input[type=file]{max-width:100%;font-size:11px}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:260px;overflow:auto}.row>label{min-width:0}summary{display:list-item;list-style:disclosure-closed;padding-left:2px}details[open]>summary{list-style:disclosure-open}.interaction-step>summary strong{font-size:12px;font-weight:600}.interaction-step>summary strong>span{font-weight:400;margin-left:6px}.interaction-step>summary>span{float:right;white-space:nowrap}.setup-card{display:grid;gap:12px;padding:12px;background:var(--panel2);border:1px solid var(--line);border-radius:9px}.setup-card>.adv{padding-top:0}.setup-notice{padding:9px 10px;background:var(--panel3);border-radius:6px}.inline-error{font-size:12px;color:var(--danger);line-height:1.5;overflow-wrap:anywhere}.job-status{display:grid;gap:8px;font-size:11px;padding:10px;border:1px solid var(--line);border-radius:6px}.job-status.failed{border-color:var(--danger);color:var(--danger)}.job-status progress{width:100%;accent-color:var(--accent)}.ready{color:var(--accent2)}.assigned-objects{display:grid;gap:12px}.check{display:flex;align-items:center;gap:8px;line-height:1.5}.check input{margin:0;flex:none}.check .chip{margin-left:auto}.matte-settings{padding-left:8px}.matte-preview{display:grid;grid-template-columns:1fr 1fr;gap:8px}.matte-preview figure{margin:0;min-width:0;overflow:hidden;background:#111;border-radius:5px}.matte-preview img{display:block;width:100%;height:auto}.matte-preview figcaption{padding:5px;font-size:10px;color:var(--muted)}.point-picker{position:relative;display:block;padding:0;width:100%;overflow:hidden;cursor:crosshair}.point-picker img{display:block;width:100%;height:auto}.point{position:absolute;width:12px;height:12px;transform:translate(-50%,-50%);background:var(--accent);border:2px solid var(--bg);border-radius:50%;pointer-events:none}.take-row{display:grid;gap:9px;padding:12px;border:1px solid var(--line);border-radius:8px}.take-links{display:flex;gap:14px;font-size:11px}.take-row .row{flex-wrap:wrap}
  .matte-preview.single{grid-template-columns:1fr}
  .element-review{display:grid;gap:9px}.element-review video{display:block;width:100%;border-radius:6px;background:#000}
</style>
