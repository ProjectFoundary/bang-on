<script lang="ts">
  import type {Project,Render} from './api';
  import {elementRequest} from './elementFlow';
  let {project,render,disabled=false}:{project:Project;render?:Render;disabled?:boolean}=$props();
  let combined=$state(true),lighting=$state(true),elements=$state(true),working=$state(false),error=$state(''),output=$state<{url:string;id:string}|null>(null),progress=$state('');
  async function build(){
    const pid=project.id;working=true;error='';output=null;
    try{
      const include=[...(combined?['combined']:[]),...(lighting?['lighting']:[]),...(elements?['elements']:[])];
      let job=await elementRequest(`/api/projects/${pid}/layer-exports`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({include,...(render?.source_current!==false&&render?.id?{render_id:render.id}:{})})});
      while(!['done','error'].includes(job.status)){
        progress=`Preparing layers${job.progress?` · ${typeof job.progress==='number'?Math.round(job.progress*100)+'%':JSON.stringify(job.progress)}`:''}`;
        await new Promise(resolve=>setTimeout(resolve,600));job=await elementRequest(`/api/projects/${pid}/layer-exports/${job.id}`);
      }
      if(job.status==='error')throw new Error(job.error??'Layer export failed.');
      if(project.id===pid)output=job;
    }catch(e){error=e instanceof Error?e.message:String(e);}finally{working=false;progress='';}
  }
</script>
<section class="block" aria-label="Export layers">
  <h2>Export layers</h2>
  <label class="check"><input type="checkbox" bind:checked={combined} disabled={disabled||working}/> Combined composite</label>
  <label class="check"><input type="checkbox" bind:checked={lighting} disabled={disabled||working}/> Lighting pass · signed EXR</label>
  <label class="check"><input type="checkbox" bind:checked={elements} disabled={disabled||working}/> Each LTX element with alpha · RGBA EXR</label>
  <p class="small muted">Native resolution and frame timing, with a colour/alpha manifest. Element EXRs are premultiplied linear RGB. Muzzle edits remain in the combined final. An ungenerated lighting pass is labelled absent.</p>
  {#if render?.review_only}<p class="small muted">Exports this review selection; approval stays manual.</p>{/if}
  <button disabled={disabled||working||!combined&&!lighting&&!elements} onclick={build}>Prepare selected exports · local/free</button>
  {#if working}<p class="small" role="status">{progress||'Preparing export…'}</p>{/if}
  {#if error}<p class="small err" role="alert">{error}</p>{/if}
  {#if output}<a class="download primary" href={output.url} download>Download layer package · ZIP</a>{/if}
</section>
<style>.block{display:grid;gap:13px;padding:18px;border-bottom:1px solid var(--line)}h2,p{margin:0}.check{display:flex;gap:8px;align-items:center}.err{color:var(--danger)}</style>
