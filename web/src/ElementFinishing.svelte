<script lang="ts">
  import type {Effect} from './api';
  let {effect,disabled=false,onchange}:{effect:Effect;disabled?:boolean;onchange:()=>void}=$props();
  const defaults={body_matte:false,edge_blend:1,holdout_blur:0,ltx_glow:0};
  const cfg=$derived({...defaults,...effect.element});
  function set(key:string,value:number|boolean){effect.element={...effect.element!,[key]:value};onchange();}
</script>
<section class="element-finishing" aria-label="Element edge finishing">
  <h3>Body &amp; edge detail</h3>
    <label class="check"><input aria-label="Keep original body" type="checkbox" checked={cfg.body_matte} {disabled} onchange={e=>set('body_matte',e.currentTarget.checked)}/> Keep original body</label>
    <p class="small muted">Retain the simulated body and building interaction. Blend in the LTX wisps around its outer edge.</p>
    <label>Edge blend · {Math.round(cfg.edge_blend*100)}%
      <input aria-label="Edge blend" type="range" min="0" max="1" step=".01" value={cfg.edge_blend} disabled={disabled||!cfg.body_matte} oninput={e=>set('edge_blend',+e.currentTarget.value)}/>
    </label>
    <p class="small muted">0% keeps the original matte. 100% uses the generated edge detail. Older versions need a captured body-alpha guide.</p>
  <label>Holdout blur · {cfg.holdout_blur}px
    <input aria-label="Holdout blur" type="range" min="0" max="12" step=".5" value={cfg.holdout_blur} {disabled} oninput={e=>set('holdout_blur',+e.currentTarget.value)}/>
  </label>
  <label>LTX glow · {Math.round(cfg.ltx_glow*100)}%
    <input aria-label="LTX glow" type="range" min="0" max=".5" step=".01" value={cfg.ltx_glow} {disabled} oninput={e=>set('ltx_glow',+e.currentTarget.value)}/>
  </label>
  <p class="small muted">Soften the building holdout and add subtle glow around bright FX. These cached adjustments need no model rerun; update the review or composite to see them.</p>
</section>
<style>
  .element-finishing{display:grid;gap:12px;padding:12px;border:1px solid var(--line);border-radius:8px}h3,p{margin:0}h3{font-size:12px}.check{display:flex;align-items:center;gap:8px}.check input{margin:0}
</style>
