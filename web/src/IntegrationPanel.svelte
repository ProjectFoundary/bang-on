<script lang="ts">
  import type {Project,ElementSettings} from './api';
  import MatteTreatment from './MatteTreatment.svelte';
  import ElementFinishing from './ElementFinishing.svelte';
  let {project,disabled=false,onchange,onrebuild}:{project:Project;disabled?:boolean;onchange:()=>void;onrebuild:()=>void}=$props();
  const defaults:ElementSettings={duration_s:2,cadence:1,blur:2,shading:1,seed:42,opacity:1,gain:1,light:0,shadow:0,depth_offset:0,colliders:[]};
  function value(effect:Project['effects'][number],key:string){return (effect.element as any)?.[key]??(key==='gain'||key==='opacity'?1:0);}
  function update(effect:Project['effects'][number],key:string,v:number){effect.element={...defaults,...effect.element,[key]:v};onchange();}
</script>
<div class="block">
  <h2>Element adjustments</h2>
  <p class="small muted">Adjust each cached element over the original photography. Optional glow and shadow sliders add simple approximations alongside the generated lighting layer.</p>
  <fieldset {disabled}>
    {#each project.effects as effect}
      <details open={project.effects.length===1}><summary>{effect.where||effect.preset.replaceAll('_',' ')} <span>{project.element_takes?.some(t=>t.effect_id===effect.id&&t.approved&&t.current)?'approved':'element'}</span></summary><div class="adv">
      {#each [['opacity','Element opacity',0,1,.05],['gain','Element brightness',0,4,.1],['light','Approximate glow',0,2,.05],['shadow','Approximate shadow',0,1,.05]] as [key,label,min,max,step]}
        <label>{label} · {value(effect,String(key)).toFixed(2)}{key==='gain'?'×':''}<input type="range" {min} {max} {step} value={value(effect,String(key))} oninput={e=>update(effect,String(key),+e.currentTarget.value)}/></label>
      {/each}
      <ElementFinishing {effect} {disabled} {onchange}/>
      {#if !effect.element?.body_matte}<MatteTreatment {effect} {disabled} {onchange}/>{/if}
      </div></details>
    {/each}
    <button disabled={!project.effects.length} onclick={onrebuild}>Update composite from cached elements</button>
  </fieldset>
  <p class="small muted">No LTX rerun is needed for these controls. Rebuild the composite, then export the updated finish.</p>
</div>
<style>.block{display:grid;gap:14px;padding:18px;border-bottom:1px solid var(--line)}h2,p{margin:0}.adv{display:grid;gap:12px;padding-top:14px}fieldset{border:0;padding:0;margin:0;display:grid;gap:12px;min-width:0}</style>
