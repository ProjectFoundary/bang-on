<script lang="ts">
  import ComparePlate from './ComparePlate.svelte';
  import { api, type DressingDraft, type PlacementBox, type PropObject, type ProtectedArea } from './api';
  let { projectId, draft, selected = $bindable(null), tool = $bindable('object'), result = null,
    compareMode = 'result', compareSplit = 50, sourceFrame = 0, mix = $bindable(0), showBoxes = $bindable(true), onchange }: {
    compareMode?: 'result' | 'source' | 'wipe'; compareSplit?: number; sourceFrame?: number; projectId: string; draft: DressingDraft; selected: string | null; tool: 'object' | 'protect';
    result: string | null; mix: number; showBoxes: boolean; onchange: () => void;
  } = $props();
  let canvas: HTMLCanvasElement | undefined = $state();
  let height = $state(540), tick = $state(0), showWorking = $state(false);
  let editing = $state<PropObject | null>(null), input: HTMLInputElement | undefined = $state();
  let drag: { item: PropObject | ProtectedArea; mode: 'new' | 'move' | 'size'; x: number; y: number; box: PlacementBox } | null = null;
  const images = new Map<string, HTMLImageElement>();
  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  function image(file: string) {
    const url = `/api/projects/${projectId}/background?file=${encodeURIComponent(file)}`;
    let im = images.get(url);
    if (!im) { im = new Image(); im.onload = () => tick++; im.src = url; images.set(url, im); }
    return im.complete && im.naturalWidth ? im : null;
  }
  function draw() {
    if (!canvas || !draft.source) return;
    const im = image(draft.source);
    if (im) height = Math.round(960 * im.naturalHeight / im.naturalWidth);
    if (canvas.height !== height) canvas.height = height;
    const g = canvas.getContext('2d')!;
    g.fillStyle = '#0b0e11'; g.fillRect(0, 0, 960, height);
    if (im) g.drawImage(im, 0, 0, 960, height);
    if (result && mix > 0) { const edited = image(result); if (edited) { g.globalAlpha = mix; g.drawImage(edited, 0, 0, 960, height); g.globalAlpha = 1; } }
    if (showWorking) {
      g.fillStyle = 'rgba(98,207,128,.20)';
      const marginX = draft.margin * 960, marginY = marginX;
      for (const o of draft.objects) { const b = o.box; g.fillRect(b.x0 * 960 - marginX, b.y0 * height - marginY,
        (b.x1 - b.x0) * 960 + 2 * marginX, (b.y1 - b.y0) * height + 2 * marginY); }
    }
    if (!showBoxes && !showWorking) return;
    for (const item of [...draft.objects, ...draft.protected]) {
      const keep = !('text' in item), b = item.box, color = keep ? '#65d2ed' : '#edff79';
      const x = b.x0 * 960, y = b.y0 * height, w = (b.x1 - b.x0) * 960, h = (b.y1 - b.y0) * height;
      if (keep) { g.fillStyle = 'rgba(67,174,211,.20)'; g.fillRect(x,y,w,h); }
      g.strokeStyle = color; g.lineWidth = item.id === selected ? 3 : 2;
      g.setLineDash(item.id === selected ? [] : [6,4]); g.strokeRect(x,y,w,h); g.setLineDash([]);
      g.fillStyle = color; g.fillRect(x+w-5,y+h-5,10,10);
      const label = keep ? 'Protected — original pixels' : (item as PropObject).text || 'Describe this object';
      g.font = '500 12px sans-serif';
      const ly = Math.max(0,y-21), width = Math.min(g.measureText(label).width+12,960-x);
      g.fillStyle = 'rgba(10,13,16,.9)'; g.fillRect(x,ly,width,20);
      g.fillStyle = color; g.fillText(label.slice(0,90),x+5,ly+14);
    }
  }
  $effect(() => { void tick; void result; void mix; void showBoxes; void showWorking; void selected; JSON.stringify(draft); draw(); });
  function point(e: PointerEvent) { const r=canvas!.getBoundingClientRect(); return [clamp((e.clientX-r.left)/r.width),clamp((e.clientY-r.top)/r.height)]; }
  function down(e: PointerEvent) {
    if (!canvas || !draft.source || e.button !== 0) return;
    canvas.focus(); canvas.setPointerCapture(e.pointerId); showBoxes=true; mix=0;
    const [x,y]=point(e);
    const list=tool==='protect'?draft.protected:draft.objects;
    for (const item of [...list].reverse()) {
      const b=item.box;
      if (Math.abs(x-b.x1)<.015 && Math.abs(y-b.y1)<.025) { selected=item.id;drag={item,mode:'size',x,y,box:{...b}};return; }
      if(x>b.x0&&x<b.x1&&y>b.y0&&y<b.y1){selected=item.id;drag={item,mode:'move',x,y,box:{...b}};return;}
    }
    if (list.length >= (tool==='protect'?30:8)) return;
    const box={x0:x,y0:y,x1:x,y1:y},id=crypto.randomUUID().slice(0,8);
    if(tool==='object')draft.objects.push({id,text:'',action:'add',box});else draft.protected.push({id,box});
    const item=list[list.length-1];selected=id;drag={item,mode:'new',x,y,box:{...box}};
  }
  function move(e:PointerEvent){
    if(!drag)return;const [x,y]=point(e),b=drag.box,dx=x-drag.x,dy=y-drag.y;
    if(drag.mode==='new')drag.item.box={x0:Math.min(x,drag.x),y0:Math.min(y,drag.y),x1:Math.max(x,drag.x),y1:Math.max(y,drag.y)};
    else if(drag.mode==='size')drag.item.box={...b,x1:clamp(Math.max(b.x0+.01,b.x1+dx)),y1:clamp(Math.max(b.y0+.01,b.y1+dy))};
    else {const w=b.x1-b.x0,h=b.y1-b.y0,x0=Math.max(0,Math.min(1-w,b.x0+dx)),y0=Math.max(0,Math.min(1-h,b.y0+dy));drag.item.box={x0,y0,x1:x0+w,y1:y0+h};}
    tick++;
  }
  function up(){
    if(!drag)return;const item=drag.item,b=item.box;
    if(drag.mode==='new'){
      if(b.x1-b.x0<.01||b.y1-b.y0<.01){draft.objects=draft.objects.filter(o=>o.id!==item.id);draft.protected=draft.protected.filter(o=>o.id!==item.id);selected=null;}
      else if('text'in item){editing=item;setTimeout(()=>input?.focus(),0);}
    }
    drag=null;onchange();
  }
</script>

<div class="tools" aria-label="Object placement tools">
  <button class:primary={tool==='object'} onclick={()=>{tool='object';showBoxes=true;}}>Draw object</button>
  <button class:primary={tool==='protect'} onclick={()=>{tool='protect';showBoxes=true;}}>Protect area</button>
  <label><input type="checkbox" bind:checked={showBoxes}/> Show boxes</label>
  <label><input type="checkbox" bind:checked={showWorking}/> Show shadow room</label>
</div>
{#if draft.source}
  <ComparePlate {projectId} frame={sourceFrame} mode={compareMode} split={compareSplit}>
  <div class="viewer">
    <canvas bind:this={canvas} width="960" {height} tabindex="0" aria-label="Draw object and protected-area rectangles on the approved location"
      onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={up}></canvas>
    {#if editing}
      <input class="float" bind:this={input} bind:value={editing.text} aria-label="New object description" placeholder="e.g. a weathered wooden shipping crate"
        style:left="{Math.min(editing.box.x0*100,65)}%" style:top="{Math.max(0,editing.box.y0*100-8)}%"
        oninput={onchange} onblur={()=>editing=null} onkeydown={(e)=>{if(e.key==='Enter'||e.key==='Escape'){e.stopPropagation();editing=null;}}}/>
    {/if}
  </div>
  </ComparePlate>
  {#if result}
    <label class="compare">Approved source <input type="range" min="0" max="1" step=".01" bind:value={mix} aria-label="Compare approved still and edited result"/> Edited result</label>
  {:else}
    <p class="hint">{tool==='protect'?'Draw over people, weapons and details to keep exactly. Use several small areas for a closer outline.':'Draw the full object silhouette. Its lower edge sets floor contact for standing objects.'}</p>
  {/if}
{:else}
  <div class="empty">Build or upload a location, review it, then choose <b>Approve for object placement</b> beside its thumbnail.</div>
{/if}

<style>
  .tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}.tools label{display:flex;align-items:center;gap:5px;font-size:12px;color:var(--muted)}
  .viewer{position:relative;width:100%;min-height:min-content;justify-self:center;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#080a0c}canvas{display:block;width:100%;height:auto;touch-action:none;cursor:crosshair}
  .float{position:absolute;width:min(300px,90%);background:#10151a;border-color:var(--accent);font-size:13px;padding:8px}
  .hint{font-size:13px;color:var(--muted);margin:10px 0}.compare{display:flex;align-items:center;gap:12px;font-size:13px;margin:12px 0}.compare input{flex:1}
  .empty{border:1px dashed var(--line);padding:80px 30px;text-align:center;color:var(--muted);border-radius:8px}
</style>
