<script lang="ts">
  import {api,type Project} from './api';
  let {project,frame,onchange,compareMode='result',compareSplit=50,disabled=false}: {project:Project;frame:number;onchange:()=>void;compareMode?:'source'|'result'|'wipe';compareSplit?:number;disabled?:boolean}=$props();
  let person=$state(0), label=$state<0|1>(1),view=$state<'overlay'|'mask'|'cutout'>('overlay');
  const people=$derived([...new Set([0,person,...project.people.points.map(p=>p.person)])].sort((a,b)=>a-b));
  $effect(()=>{if(!project.people.points.length){person=0;label=1;}});
  const url=$derived(project.people_matte ? `/api/projects/${project.id}/people/frame/${frame}?view=${view}&v=${encodeURIComponent(JSON.stringify([project.people_matte.id,project.people.feather,project.people.trim]))}` : api.frameUrl(project.id,frame,'plate'));
  function click(e:MouseEvent){
    if(disabled)return;
    const r=e.currentTarget as HTMLElement,b=r.getBoundingClientRect();
    project.people.points.push({person,frame,label,x:e.detail===0 ? .5 : Math.min(1,Math.max(0,(e.clientX-b.left)/b.width)),y:e.detail===0 ? .5 : Math.min(1,Math.max(0,(e.clientY-b.top)/b.height))});
    onchange();
  }
</script>
<div class="people-preview">
  {#if project.people.selection==='auto'}
    <img class="auto-source" src={api.frameUrl(project.id,frame,'plate')} alt="Original plate; no selection needed"/>
    <p>The original plate is used directly. No matte or clicks are needed; choose Manual only to control which people are kept.</p>
  {:else}
  <div class="matte-tools"><label>Person <select bind:value={person}>{#each people as p}<option value={p}>{p+1}</option>{/each}</select></label>
    <button onclick={()=>{person=Math.min(7,Math.max(...people)+1);label=1;}}>Add person</button>
    <button class:active={label===1} aria-pressed={label===1} onclick={()=>label=1}>Keep +</button><button class:active={label===0} aria-pressed={label===0} onclick={()=>label=0}>Remove −</button>
    <span class="grow"></span><select aria-label="Matte preview" bind:value={view}><option value="overlay">Selection overlay</option><option value="mask">Black / white matte</option><option value="cutout">Cutout</option></select>
  </div>
  <button class="image" {disabled} onclick={click} aria-label="Add a matte point at the chosen image position">
    <img src={url} alt="Click a person to keep them; remove clicks exclude unwanted areas" draggable="false"/>
    {#if compareMode!=='result'}<img class="source-image" src={api.frameUrl(project.id,frame,'plate')} style:clip-path={compareMode==='wipe'?`inset(0 ${100-compareSplit}% 0 0)`:undefined} alt={`Original source plate at frame ${frame}`}/>{/if}
    {#if compareMode==='wipe'}<span class="wipe" style:left="{compareSplit}%"></span>{/if}
    {#each project.people.points.filter(p=>p.frame===frame) as p}<span class:remove={p.label===0} class:chosen={p.person===person} class="point" style:left="{p.x*100}%" style:top="{p.y*100}%">{p.label===1?'+':'−'}<small>{p.person+1}</small></span>{/each}
  </button>
  <p>Start each person with a Keep click on frame 0. Add Keep and Remove clicks on later frames to correct tracking, then rebuild the matte.</p>
  {/if}
</div>
<style>.auto-source{width:100%;display:block;aspect-ratio:16/9;object-fit:contain}.people-preview{background:#090b0c;border-radius:8px;overflow:hidden}.matte-tools{display:flex;gap:6px;align-items:center;padding:8px;flex-wrap:wrap}.matte-tools label{display:flex;gap:6px;align-items:center;font-size:12px}.matte-tools button,.matte-tools select{font-size:12px;padding:5px 7px}.active{border-color:var(--accent)}.image{position:relative;cursor:crosshair;aspect-ratio:16/9;display:block;width:100%;padding:0;border:0;border-radius:0;background:#090b0c}.image img{width:100%;height:100%;display:block;object-fit:contain}.source-image{position:absolute;inset:0;pointer-events:none}.wipe{position:absolute;top:0;bottom:0;width:2px;background:var(--accent);pointer-events:none}.point{position:absolute;transform:translate(-50%,-50%);border:2px solid white;border-radius:50%;background:#2b9f57;color:white;width:18px;height:18px;font-size:14px;line-height:13px;text-align:center;pointer-events:none}.point.remove{background:#d34b64}.point.chosen{box-shadow:0 0 0 2px #edff79}.point small{position:absolute;top:15px;font-size:9px;text-shadow:0 1px 3px black}p{font-size:12px;color:#c1c8cf;margin:0;padding:8px 12px}</style>
