<script lang="ts">
  import {api,type Project} from './api';
  import {elementRequest} from './elementFlow';
  let {project,effectId,disabled=false,onupdated}:{project:Project;effectId?:string;disabled?:boolean;onupdated:(p:Project)=>void}=$props();
  let dialog:HTMLDialogElement;
  let points=$state<{x:number;y:number}[]>([]),tolerance=$state(.04),name=$state('Shelf'),role=$state('both');
  let preview=$state<any>(null),error=$state(''),working=$state(false),showDepth=$state(.35);
  let limit=$state(false),bounds=$state([0,0,1,1]);
  let revision=0,timer:ReturnType<typeof setTimeout>;
  const body=()=>({points,tolerance,...(limit?{bounds}: {})});
  function schedule(){preview=null;clearTimeout(timer);const version=++revision;if(!points.length)return;timer=setTimeout(()=>update(version),180);}
  async function update(version:number){
    error='';working=true;
    try{const result=await elementRequest(`/api/projects/${project.id}/geometry/depth-selection/preview`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body())});if(version===revision)preview=result;}
    catch(e){if(version===revision)error=String(e);}finally{if(version===revision)working=false;}
  }
  function pick(event:MouseEvent){const box=event.currentTarget as HTMLElement,r=box.getBoundingClientRect();const point=event.detail===0?{x:.5,y:.5}:{x:Math.max(0,Math.min(1,(event.clientX-r.left)/r.width)),y:Math.max(0,Math.min(1,(event.clientY-r.top)/r.height))};points=event.shiftKey?[...points,point].slice(-32):[point];schedule();}
  async function add(){if(!preview||working)return;working=true;error='';try{await elementRequest(`/api/projects/${project.id}/geometry/depth-selection`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body(),selection_key:preview.selection_key,name,role,kind:'hard',...(effectId?{effect_id:effectId}:{})})});onupdated(await api.project(project.id));dialog.close();points=[];preview=null;}catch(e){error=String(e);}finally{working=false;}}
  function open(){points=[];preview=null;error='';working=false;revision++;dialog.showModal();}
</script>

<button disabled={disabled||!project.geometry?.depth} onclick={open}>Select object from depth…</button>
<dialog bind:this={dialog} aria-label="Select a depth object">
  <div class="depth-dialog">
    <div class="row"><h3>Select an object from depth</h3><span class="grow"></span><button onclick={()=>dialog.close()} aria-label="Close depth selection">Close</button></div>
    <p class="small muted">Click the shelf or surface. Shift-click adds disconnected pieces. Adjust tolerance to keep the openings clear. Depth regions are approximate; they do not identify objects by name.</p>
    <button class="depth-picker" aria-label="Pick a surface on the plate" onclick={pick}>
      <img class="plate" src={api.frameUrl(project.id,project.geometry?.reference_frame??0,'plate')} alt="Depth reference plate"/>
      {#if preview}<img class="layer" src={preview.depth_url} style:opacity={showDepth} alt=""/><img class="layer" src={preview.overlay_url} alt="Selected depth region"/>{/if}
      {#each points as point}<span class="point" style:left={`${point.x*100}%`} style:top={`${point.y*100}%`}></span>{/each}
    </button>
    <div class="row"><label class="grow">Depth tolerance · {tolerance.toFixed(3)}<input aria-label="Depth tolerance" type="range" min="0" max=".3" step=".002" bind:value={tolerance} oninput={schedule}/></label><label class="grow">Depth overlay<input type="range" min="0" max="1" step=".05" bind:value={showDepth}/></label></div>
    <details><summary>Limit selection to a region</summary><div class="adv"><label class="check"><input type="checkbox" bind:checked={limit} onchange={schedule}/>Restrict depth selection</label>{#if limit}<div class="bounds">{#each ['Left','Top','Right','Bottom'] as label,i}<label>{label}<input type="number" min="0" max="1" step=".01" bind:value={bounds[i]} oninput={schedule}/></label>{/each}</div>{/if}</div></details>
    <div class="row"><label class="grow">Object name<input bind:value={name} maxlength="80"/></label><label class="grow">Interaction<select bind:value={role}><option value="both">Collider + holdout · wraps around</option><option value="holdout">Holdout only · fluid passes through</option><option value="collide">Collider only · no visible cutout</option></select></label></div>
    {#if preview}<p class="small muted">{(preview.coverage*100).toFixed(1)}% of the depth image selected. {effectId?'This object will be assigned only to the selected element.':'Assign the new object to the elements that should use it.'}</p>{/if}
    {#if working}<p class="small" role="status">Updating depth selection…</p>{/if}{#if error}<p class="err" role="alert">{error}</p>{/if}
    <div class="row"><span class="grow"></span><button disabled={working||!preview||!name.trim()} class="primary" onclick={add}>Add selected object</button></div>
  </div>
</dialog>
<style>
dialog{padding:0;border:1px solid var(--line);border-radius:12px;background:var(--panel);color:var(--text);width:min(920px,94vw);max-height:94vh}dialog::backdrop{background:rgb(0 0 0 / .75)}.depth-dialog{display:grid;gap:12px;padding:20px;overflow:auto;max-height:90vh;box-sizing:border-box}h3,p{margin:0}.depth-picker{position:relative;display:block;width:100%;padding:0;overflow:hidden;border:1px solid var(--line);border-radius:7px;cursor:crosshair}.plate{display:block;width:100%;height:auto}.layer{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.point{position:absolute;transform:translate(-50%,-50%);width:10px;height:10px;border:2px solid white;background:#4aca70;border-radius:50%;pointer-events:none}.bounds{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.grow{min-width:0}
</style>
