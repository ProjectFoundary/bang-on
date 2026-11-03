<script lang="ts">
  import type {Effect} from './api';
  let {effect,disabled=false,onchange}:{effect:Effect;disabled?:boolean;onchange:()=>void}=$props();
  const defaults={matte_enabled:true,matte_multiply:16,matte_erode:12,matte_blur:3};
  const controls=[{key:'matte_multiply',label:'Matte multiply',max:64,step:1,min:1,unit:'×'},
    {key:'matte_erode',label:'Matte erode',max:64,step:1,min:0,unit:'px'},
    {key:'matte_blur',label:'Matte blur',max:24,step:.5,min:0,unit:'px'}] as const;
  const cfg=$derived({...defaults,...effect.element});
  function set(key:string,value:number|boolean){effect.element={...effect.element!,[key]:value};onchange();}
  function reset(){effect.element={...effect.element!,...defaults};onchange();}
  function numberInput(control:typeof controls[number],input:HTMLInputElement,commit=false){
    const v=input.valueAsNumber;
    if(Number.isFinite(v)&&v>=control.min&&v<=control.max&&(control.key!=='matte_erode'||Number.isInteger(v)))set(control.key,v);
    else if(commit)input.value=String(cfg[control.key]);
  }
</script>
<section class="matte-treatment" aria-label="Matte treatment">
  <h3>Matte treatment</h3>
  <label class="check"><input type="checkbox" checked={cfg.matte_enabled} {disabled} onchange={e=>set('matte_enabled',e.currentTarget.checked)}/> Strengthen core · retain soft fringe</label>
  <fieldset disabled={disabled||!cfg.matte_enabled}>
    {#each controls as control}
      <label>{control.label} · {cfg[control.key]}{control.unit}
        <span class="control"><input aria-label={control.label} type="range" min={control.min} max={control.max} step={control.step} value={cfg[control.key]} oninput={e=>set(control.key,+e.currentTarget.value)}/>
        <input aria-label={`${control.label} value`} type="number" min={control.min} max={control.max} step={control.step} value={cfg[control.key]} oninput={e=>numberInput(control,e.currentTarget)} onchange={e=>numberInput(control,e.currentTarget,true)}/></span>
      </label>
    {/each}
  </fieldset>
  <button class="small" {disabled} onclick={reset}>Reset matte to defaults</button>
  <p class="small muted">Erode and blur use matte pixels. Update the composite to see changes; no model rerun is needed.</p>
</section>
<style>
  .matte-treatment{display:grid;gap:12px;padding:12px;border:1px solid var(--line);border-radius:8px}h3,p{margin:0}h3{font-size:12px}fieldset{display:grid;gap:12px;padding:0;margin:0;border:0;min-width:0}.control{display:flex;align-items:center;gap:10px}.control input[type=range]{flex:1;min-width:0}.control input[type=number]{width:65px}.check{display:flex;align-items:center;gap:8px}.check input{margin:0}
</style>
