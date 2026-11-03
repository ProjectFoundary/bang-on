<script lang="ts">
  import {api,type Project,type RoomReference} from './api';
  let {project,disabled=false,onchange,onbusy,onerror}: {
    project:Project;disabled?:boolean;onchange:(id:string,refs:RoomReference[])=>void;onbusy:(id:string,value:boolean)=>void;onerror:(id:string,text:string)=>void;
  }=$props();
  let uploading=$state(false);
  const refs=$derived(project.room_references??[]);
  const max=$derived(project.room_reference_max??6);
  const limit=$derived(project.room_reference_limits?.[project.image_models.location]??(project.image_models.location==='qwen'?2:6));
  async function add(event:Event){
    const input=event.currentTarget as HTMLInputElement,files=Array.from(input.files??[]),id=project.id;
    if(!files.length)return;
    if(refs.length+files.length>max){onerror(id,`Keep up to ${max} room photos. Remove some before adding more.`);input.value='';return;}
    uploading=true;onbusy(id,true);
    try{const p=await api.roomReferences(id,files);if(project.id===id)onchange(id,p.room_references??[]);}
    catch(e){if(project.id===id)onerror(id,String(e));}
    finally{uploading=false;input.value='';onbusy(id,false);}
  }
  async function remove(ref:string){
    const id=project.id;uploading=true;onbusy(id,true);
    try{const p=await api.removeRoomReference(id,ref);if(project.id===id)onchange(id,p.room_references??[]);}
    catch(e){if(project.id===id)onerror(id,String(e));}
    finally{uploading=false;onbusy(id,false);}
  }
</script>
<section class="room-references" aria-label="Optional room reference photos">
  <div class="heading"><strong>Room photos <span>optional</span></strong><span>{refs.length} / {max}</span></div>
  <p>The plate guides camera and perspective. These photos guide the room’s layout, furniture and materials when you generate a new location still.</p>
  {#if refs.length}<div class="photos">{#each refs as ref,i (ref.id)}
    <div class="photo"><img src={api.roomReferenceUrl(project.id,ref.id)} alt={`Room reference ${i+1}: ${ref.name}`}/><div><span title={ref.name}>{ref.name}</span><button type="button" class="ghost" aria-label={`Remove room reference ${i+1}`} disabled={disabled||uploading} onclick={()=>remove(ref.id)}>Remove</button></div></div>
  {/each}</div>{/if}
  <label class="upload">{uploading?'Saving photos…':'Add room photos'}<input type="file" accept="image/jpeg,image/png,image/webp" multiple disabled={disabled||uploading||refs.length>=max} onchange={add}/></label>
  {#if refs.length>limit}<p class="notice">Qwen can use two room photos alongside the plate. Choose Nano Banana or FLUX 3 to use all {refs.length}, or remove some photos.</p>
  {:else if project.image_models.location==='qwen'}<p class="hint">Qwen: up to 2 photos. Nano Banana and FLUX 3: up to {max}.</p>{/if}
</section>
<style>
.room-references{margin:10px 0 16px;padding:12px;background:var(--bg);border:1px solid var(--line);border-radius:8px}.heading{display:flex;justify-content:space-between;gap:8px;font-size:12px}.heading span{color:var(--muted);font-weight:400}.heading strong span{margin-left:4px}p{font-size:11px;line-height:1.45;color:var(--muted);margin:8px 0}.photos{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:10px 0}.photo{min-width:0;border:1px solid var(--line);border-radius:6px;overflow:hidden}.photo img{width:100%;height:82px;object-fit:contain;display:block;background:#090b0c}.photo>div{padding:5px}.photo span{display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:10px}.photo button{font-size:10px;padding:3px 0}.upload{font-size:12px}.upload input{display:block;margin-top:6px;width:100%;font-size:11px}.hint{margin-bottom:0}.notice{margin:10px 0 0}
</style>
