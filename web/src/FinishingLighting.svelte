<script lang="ts">
  import type {Project} from './api';
  let {project,disabled=false,configured=false,onrun,onsetting}:{project:Project;disabled?:boolean;configured?:boolean;onrun:(newVersion?:boolean)=>void;onsetting:(body:Record<string,unknown>)=>void}=$props();
  const active=$derived(project.lighting_takes?.find(t=>t.id===project.light_integration?.take_id));
  const approved=$derived(project.effects.every(e=>project.element_takes?.some(t=>t.effect_id===e.id&&t.current&&t.approved)));
  const running=$derived(project.lighting_takes?.find(t=>!['done','error'].includes(t.status)));
  const recent=$derived(project.lighting_takes?.[0]);
</script>
<section class="block" aria-label="MiniMax light integration">
  <h2>Interactive lighting · MiniMax</h2>
  <p class="small muted">Uses the clean plate and your LTX composite to generate room and actor lighting. A signed light/shadow difference is added to the original plate, then the fixed LTX elements are placed back over it.</p>
  <button disabled={disabled||!configured||!approved||!project.effects.length} onclick={()=>onrun(false)}>Run MiniMax lighting pass · uses credits</button>
  {#if !approved}<p class="small muted">Approve the effects first so the lighting follows the versions you keep.</p>{/if}
  {#if running}<p class="small" role="status">Lighting · {running.status}</p>{/if}
  {#if recent?.status==='error'}<p class="err small" role="alert">{recent.error}</p><button disabled={disabled||!configured||!approved} onclick={()=>onrun(true)}>Run a new lighting version · uses credits</button>{/if}
  {#if active?.status==='done'}
    {#if !active.current}<p class="small notice">This pass uses earlier effects. Regenerate it or disable it before building the composite.</p>{/if}
    <label class="check"><input type="checkbox" disabled={disabled||!active.current&&!(project.light_integration?.enabled)} checked={project.light_integration?.enabled??false} onchange={e=>onsetting({enabled:e.currentTarget.checked})}/> Use generated light/shadow layer</label>
    <label>Lighting strength · {(project.light_integration?.gain??1).toFixed(2)}×<input aria-label="MiniMax lighting strength" disabled={disabled||!active.current} type="range" min="0" max="3" step=".05" value={project.light_integration?.gain??1} onchange={e=>onsetting({gain:+e.currentTarget.value})}/></label>
    <button disabled={disabled||!configured||!approved} onclick={()=>onrun(true)}>New lighting variation · uses credits</button>
    <p class="small muted">The difference can include model texture or shape changes. Compare the rebuilt result before using it. Your original footage and LTX assets are retained.</p>
  {/if}
</section>
<style>.block{display:grid;gap:13px;padding:18px;border-bottom:1px solid var(--line)}h2,p{margin:0}.check{display:flex;gap:8px;align-items:center}.err{color:var(--danger)}</style>
