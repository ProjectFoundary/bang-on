<script lang="ts">
  import {onDestroy} from 'svelte';
  import type { Effect, Flash, Preset } from './api';
  import { COLORS, PREROLL_MAX, phaseOf } from './effects';
  import { frameSecondsLabel, frameTime, frameAtTime } from './nativeTiming';
  import {elementOnset} from './elementOptions';
  import {elementRange,durationThroughFrame,setElementDuration,MAX_ELEMENT_SECONDS} from './elementTiming';
  import { endOf, onsetOf } from './mockup';

  let { frames, frame, effects, presets, selectedId, playing, keyFrames, flashes = [], selectedFlashId = null, onselectflash, guideMode = 'sparse', fps = 24, timestamps, clipDuration, elementMode=false, disabled=false, onseek, onselect, onchange, onplay }: {
    frames: number; frame: number; effects: Effect[]; presets: Record<string, Preset>; selectedId: string | null; playing: boolean;
    keyFrames: { frame: number; hard: boolean }[];
    flashes?: Flash[]; selectedFlashId?: string | null; onselectflash?: (id: string) => void;
    guideMode?: 'sparse' | 'full';
    fps?: number; timestamps?: number[];clipDuration?:number;elementMode?:boolean;disabled?:boolean;
    onseek: (f: number) => void; onselect: (id: string) => void; onchange: (e: Effect) => void; onplay: () => void;
  } = $props();

  let tracks: HTMLDivElement | undefined = $state();
  let endDraft = $state<{id:string;duration:number}|null>(null);
  let endCleanup:(()=>void)|undefined;
  const timing=$derived({frames,fps,timestamps,clipDuration});
  const selected=$derived(effects.find(e=>e.id===selectedId));
  const flashKeys=$derived.by(()=>{
    const rows=new Map<number,number>();
    return [...flashes].sort((a,b)=>a.frame-b.frame).map(flash=>{
      const row=rows.get(flash.frame)??0;rows.set(flash.frame,row+1);
      return {flash,row};
    });
  });
  const flashRows=$derived(Math.max(1,...flashKeys.map(k=>k.row+1)));
  const flashStatus=(f:Flash)=>f.result_current?'ready':f.status==='error'?'failed':['queued','generating'].includes(f.status)?'generating':'pending';
  function selectFlash(f:Flash){onseek(f.frame);onselectflash?.(f.id);}
  const range=(e:Effect)=>elementRange(e,timing,endDraft?.id===e.id?endDraft.duration:e.element?.duration_s??2);
  const pct = (f: number) => (frames > 1 ? (f / (frames - 1)) * 100 : 0);
  const clampF = (f: number) => Math.min(frames - 1, Math.max(0, f));
  const pctC = (f: number) => Math.min(100, Math.max(0, pct(f)));
  const persists = (e: Effect) => !!presets[e.preset]?.phases.at(-1)?.persist;
  const isGuide = (e: Effect, phase: string) => guideMode === 'full' || !presets[e.preset]?.guide_phases || presets[e.preset].guide_phases!.includes(phase);
  const elementStart=(e:Effect)=>range(e).start;
  const elementEnd=(e:Effect)=>range(e).endFrame;
  const guides=$derived.by(()=>elementMode?effects.flatMap(e=>{
    const start=elementStart(e),duration=Math.min(range(e).duration,Math.max(1/24,(clipDuration??frameTime(frames,fps,timestamps))-start));
    const n=Math.min(121,Math.max(2,Math.ceil(duration*24))),cadence=e.element?.cadence??1;
    return Array.from({length:n},(_,i)=>i).filter(i=>i%cadence===0||i===n-1).map(i=>({frame:frameAtTime(start+i/24,frames,fps,timestamps),hard:true}));
  }):keyFrames);

  function frameAt(x: number, free = false) {
    const r = tracks!.getBoundingClientRect(), u = (x - r.left) / r.width;
    return Math.round((free ? u : Math.min(1, Math.max(0, u))) * (frames - 1));
  }
  function dragWith(ev: PointerEvent, fn: (f: number) => void, done?: () => void, free = false) {
    ev.stopPropagation(); ev.preventDefault();
    const mv = (e2: PointerEvent) => fn(frameAt(e2.clientX, free));
    const upf = () => { window.removeEventListener('pointermove', mv); window.removeEventListener('pointerup', upf); done?.(); };
    window.addEventListener('pointermove', mv); window.addEventListener('pointerup', upf);
  }
  function scrub(ev: PointerEvent) { onseek(frameAt(ev.clientX)); dragWith(ev, onseek); }

  function commitEnd(e:Effect,duration:number){
    if(disabled)return;
    onselect(e.id);
    if(Math.abs(duration-(e.element?.duration_s??2))>1e-9){setElementDuration(e,duration);onchange(e);}
    onseek(elementRange(e,timing).endFrame);
  }
  /** Only redraw the range while dragging; replay/save the simulation once on release. */
  function endDown(ev:PointerEvent,e:Effect){
    if(disabled||ev.button!==0)return;
    ev.stopPropagation();ev.preventDefault();endCleanup?.();onselect(e.id);onseek(clampF(frame));
    endDraft={id:e.id,duration:e.element?.duration_s??2};
    const move=(event:PointerEvent)=>{if(event.pointerId===ev.pointerId)endDraft={id:e.id,duration:durationThroughFrame(e,frameAt(event.clientX),timing)};};
    const cleanup=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',cancel);window.removeEventListener('blur',cancel);window.removeEventListener('keydown',key,true);endDraft=null;endCleanup=undefined;};
    const up=(event:PointerEvent)=>{if(event.pointerId!==ev.pointerId)return;const duration=endDraft!.duration;cleanup();commitEnd(e,duration);};
    const cancel=()=>cleanup();
    const key=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();cleanup();}};
    endCleanup=cleanup;window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);window.addEventListener('pointercancel',cancel);window.addEventListener('blur',cancel);window.addEventListener('keydown',key,true);
  }
  onDestroy(()=>endCleanup?.());
  function endKey(event:KeyboardEvent,e:Effect){
    const r=range(e),step=event.shiftKey?10:1;
    const target=event.key==='Home'?r.minEndFrame:event.key==='End'?r.maxEndFrame:['ArrowRight','ArrowUp'].includes(event.key)?r.endFrame+step:['ArrowLeft','ArrowDown'].includes(event.key)?r.endFrame-step:null;
    if(target===null)return;event.preventDefault();event.stopPropagation();commitEnd(e,durationThroughFrame(e,target,timing));
  }

  /** Drag a phase diamond: that key (and repeats of the same phase after it keep their spacing). */
  function keyDown(ev: PointerEvent, e: Effect, phase: string) {
    if(disabled)return;
    onselect(e.id);
    const ks = e.keys.filter((k) => k.phase === phase), base = ks.map((k) => k.frame), f0 = base[0];
    onseek(f0);
    dragWith(ev, (f) => { const d = f - f0; ks.forEach((k, i) => (k.frame = clampF(base[i] + d))); onseek(clampF(f0 + d)); }, () => onchange(e));
  }
  /** Drag the bar: retime the whole effect (keys and track anchors together). */
  function barDown(ev: PointerEvent, e: Effect) {
    if(disabled)return;
    onselect(e.id);
    // a lasting effect can be dragged off the left edge: it is then already going when the shot starts
    const last = presets[e.preset].phases.at(-1)!.phase, core = e.keys.filter((k) => k.phase !== last || k === e.keys.find((q) => q.phase === last));
    const kb = e.keys.map((k) => k.frame), ab = (e.anchors ?? []).map((a) => a.frame), f0 = frameAt(ev.clientX);
    const start=elementOnset(e);
    const lo = (persists(e) ? -PREROLL_MAX : 0) - (elementMode?start:Math.min(...core.map((k) => k.frame))), hi = frames - 1 - (elementMode?start:Math.max(...core.map((k) => k.frame)));
    dragWith(ev, (f) => {
      const d = Math.min(hi, Math.max(lo, f - f0));
      e.keys.forEach((k, i) => (k.frame = kb[i] + d)); (e.anchors ?? []).forEach((a, i) => (a.frame = clampF(ab[i] + d)));
      onseek(clampF(onsetOf(e, presets[e.preset])));
    }, () => onchange(e), true);
  }
  const phasesOf = (e: Effect) => {
    const seen = new Map<string, number>();
    for (const k of [...e.keys].sort((a, b) => a.frame - b.frame)) if (!seen.has(k.phase)) seen.set(k.phase, k.frame);
    return [...seen.entries()];
  };
</script>

<div class="timeline">
  <div class="transport row">
    <button class="ghost play" onclick={onplay} title="Play / pause (space)">{playing ? '❚❚' : '▶'}</button>
    <span class="tc">{frame} <span class="muted">/ {frames - 1}</span> · {frameSecondsLabel(frame, fps, timestamps)}</span>
    <span class="grow"></span>
    <span class="muted small" title={elementMode?'Isolated24fps model guide samples; muzzle frames generate last':'Explosion image guides; muzzle frames generate after the video'}><span class="kdot"></span>{guides.length} guide{guides.length === 1 ? '' : 's'}{#if flashes.length} · <span class="flash-legend">{flashes.length} muzzle frame{flashes.length === 1 ? '' : 's'}</span>{/if}</span>
  </div>
  <div class="tracks" bind:this={tracks}>
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="ruler" onpointerdown={scrub}>
      {#each Array(Math.ceil(frames / 12)) as _, i}<span class="tick" style:left="{pct(i * 12)}%">{i * 12}</span>{/each}
      {#each guides as k}<span class="kf" class:hard={k.hard} style:left="{pct(k.frame)}%" title="Image guide · frame {k.frame}"></span>{/each}
    </div>
    {#if flashKeys.length}
      <div class="lane flash-lane" style:height="{flashRows*40+2}px" class:sel={!!selectedFlashId} role="group" aria-label="Muzzle flash keyframes">
        <span class="name flash-name">Muzzle flash</span>
        {#each flashKeys as {flash:f,row} (f.id)}
          <button type="button" class="flash-key" class:selected={f.id===selectedFlashId} class:at-playhead={f.frame===frame} class:ready={!!f.result_current} class:failed={f.status==='error'&&!f.result_current}
            style:left="{pctC(f.frame)}%" style:top="{1+row*40}px" data-flash-id={f.id} aria-label={`Select muzzle flash at frame ${f.frame}`} aria-pressed={f.id===selectedFlashId}
            title={`Muzzle flash · frame ${f.frame} · ${flashStatus(f)} · click to edit`} onclick={()=>selectFlash(f)}>
            <span class="flash-diamond" aria-hidden="true"></span><span class="flash-frame">{f.frame}</span>
          </button>
        {/each}
      </div>
    {/if}
    {#each effects as e (e.id)}
      {@const p = presets[e.preset]}
      {@const on = elementMode?elementOnset(e):p ? onsetOf(e, p) : 0}
      {@const end = elementMode?elementEnd(e):p ? endOf(e, p, frames) : 0}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <div class="lane" class:sel={e.id === selectedId} data-effect-id={e.id} onclick={() => onselect(e.id)}>
        <span class="name" style:color={COLORS[e.preset] ?? '#ddd'}>{p?.name ?? e.preset}</span>
        <span class="bar" class:pre={on < 0} style:left="{pctC(on)}%" style:width="{Math.max(0.8, pctC(end) - pctC(on))}%" style:--c={COLORS[e.preset] ?? '#888'}
              title={on < 0 ? `Started ${-on} frames before the shot — drag to retime` : 'Drag to move effect'} onpointerdown={(ev) => barDown(ev, e)}></span>
        {#if elementMode}
          {@const r=range(e)}
          <button type="button" class="end-handle" class:dragging={endDraft?.id===e.id} style:left="{pctC(end)}%" style:--c={COLORS[e.preset]??'#ddd'}
            role="slider" aria-label={`Adjust ${p?.name??e.preset} end`} aria-valuemin={r.minEndFrame} aria-valuemax={r.maxEndFrame} aria-valuenow={r.endFrame}
            aria-valuetext={`End frame ${r.endFrame}, ${(r.end-r.start).toFixed(2)} seconds`} {disabled}
            title={`Drag to set end · frame ${end} · ${(r.end-r.start).toFixed(2)}s. Arrow keys: 1 frame; Shift: 10. Maximum ${MAX_ELEMENT_SECONDS}s.`}
            onpointerdown={ev=>endDown(ev,e)} onkeydown={ev=>endKey(ev,e)} onclick={ev=>ev.stopPropagation()}><span></span></button>
          {#if e.id===selectedId}<span class="range-label">{Math.max(0,on)}–{end} · {(r.end-r.start).toFixed(2)}s{endDraft?.id===e.id&&r.duration>=MAX_ELEMENT_SECONDS?' · 5s limit':''}</span>{/if}
        {/if}
        {#if on < 0}<span class="preTag" title="Started {-on} frames before the shot: already going at frame 0">◂ already on</span>{/if}
        {#if !elementMode}{#each e.keys.filter((k) => k.phase !== 'before' && k.frame >= 0) as k}
          <span class="rep" style:left="{pct(k.frame)}%"></span>
        {/each}
        {#each phasesOf(e) as [phase, f]}
          {@const ph = p ? phaseOf(p, phase) : null}
          {#if ph && phase !== 'before' && f >= 0}
            <span class="key" class:hard={ph.hard} class:empty={!ph.stamp} class:on={f === frame} class:preview-only={!isGuide(e,phase)} style:left="{pct(f)}%"
                  style:--c={COLORS[e.preset] ?? '#ddd'} title="{ph.label} · frame {f}{isGuide(e,phase) ? ' · AI guide' : ' · preview timing only'} — drag to retime" onpointerdown={(ev) => keyDown(ev, e, phase)}></span>
          {/if}
        {/each}{/if}
      </div>
    {/each}
    <div class="playhead" style:left="{pct(frame)}%"></div>
  </div>
  {#if elementMode&&effects.length}
    <div class="range-tools">
      <span>Drag bar to move · right edge to set end</span>
      {#if selected}
        <button class="ghost" {disabled} onclick={()=>commitEnd(selected,durationThroughFrame(selected,frame,timing))}>End at playhead</button>
        <button class="ghost" {disabled} onclick={()=>commitEnd(selected,durationThroughFrame(selected,frames-1,timing))}>{(clipDuration??frameTime(frames,fps,timestamps))-range(selected).start>MAX_ELEMENT_SECONDS?'Extend to 5s':'To shot end'}</button>
      {/if}
    </div>
    {#if selected}<p class="range-note">Up to 5 seconds per LTX element. A changed range updates Preview; an existing generated version needs a new render.</p>{/if}
  {/if}
</div>

<style>
  .timeline { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px 10px; user-select: none; }
  .transport { margin-bottom: 6px; }
  .play { width: 34px; }
  .tc { font-variant-numeric: tabular-nums; }
  .kdot { display: inline-block; width: 6px; height: 10px; background: var(--accent2); border-radius: 2px; vertical-align: -1px; margin-right: 4px; }
  .tracks { position: relative; margin-left: 110px; }
  .ruler { position: relative; height: 22px; cursor: ew-resize; border-bottom: 1px solid var(--line); }
  .tick { position: absolute; top: 1px; font-size: 10px; color: var(--muted); transform: translateX(-50%); pointer-events: none; }
  .kf { position: absolute; bottom: 0; width: 4px; height: 8px; margin-left: -2px; background: var(--accent2); border-radius: 1px; pointer-events: none; }
  .kf.hard { background: #fff; }
  .lane { position: relative; height: 32px; border-bottom: 1px solid #202428; cursor: pointer; }
  .lane.sel { background: rgba(255, 176, 74, .06); }
  .flash-lane {height:42px;cursor:default;}
  .flash-name,.flash-legend {color:#ffe27a;}
  .flash-key {position:absolute;top:1px;width:24px;height:39px;transform:translateX(-50%);padding:0;border:0;border-radius:4px;background:transparent;color:#ffe27a;display:flex;flex-direction:column;align-items:center;gap:4px;justify-content:center;cursor:pointer;z-index:1;}
  .flash-diamond {width:11px;height:11px;flex:none;transform:rotate(45deg);border:1px solid currentColor;background:currentColor;box-shadow:0 0 0 1px #000a;}
  .flash-frame {font-size:10px;line-height:12px;font-variant-numeric:tabular-nums;}
  .flash-key.ready {color:#7fd1c0;}
  .flash-key.failed {color:#ff8585;}
  .flash-key.at-playhead .flash-diamond {outline:1px solid #fff;outline-offset:2px;}
  .flash-key:hover,.flash-key.selected,.flash-key:focus-visible {background:#ffe27a24;color:#fff;z-index:2;}
  .flash-key.selected .flash-diamond {outline:2px solid #fff;outline-offset:2px;}
  .flash-key:focus-visible {outline:2px solid var(--accent2);outline-offset:2px;}
  .name { position: absolute; right: calc(100% + 8px); top: 6px; width: 102px; text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 12px; }
  .bar { position: absolute; top: 9px; height: 10px; border-radius: 5px; background: color-mix(in srgb, var(--c) 35%, transparent); border: 1px solid color-mix(in srgb, var(--c) 60%, transparent); cursor: grab; }
  .bar.pre { border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: 0; }
  .preTag { position: absolute; left: 4px; top: 7px; font-size: 10px; color: #e6e8ea; pointer-events: none; text-shadow: 0 0 3px #000, 0 0 2px #000; }
  .bar:hover { background: color-mix(in srgb, var(--c) 50%, transparent); }
  .end-handle { position:absolute;top:3px;width:16px;height:23px;transform:translateX(-50%);padding:0;border:1px solid var(--c);border-radius:4px;background:var(--panel);cursor:ew-resize;touch-action:none;z-index:3;display:grid;place-items:center; }
  .end-handle span {height:12px;border-left:2px solid var(--c);border-right:2px solid var(--c);width:5px;pointer-events:none;}
  .end-handle:hover,.end-handle.dragging,.end-handle:focus-visible {background:color-mix(in srgb,var(--c) 30%,var(--panel));outline:2px solid var(--accent2);outline-offset:2px;}
  .end-handle:disabled {cursor:default;opacity:.4;}
  .range-label {position:absolute;right:20px;top:4px;font-size:11px;line-height:20px;color:#eee;pointer-events:none;text-shadow:0 1px 3px #000,0 0 3px #000;}
  .range-tools {display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:7px;color:var(--muted);font-size:11px;}
  .range-tools button {padding:4px 7px;font-size:11px;}
  .range-note {font-size:11px;color:var(--muted);margin:5px 0 0;}
  .rep { position: absolute; top: 11px; width: 2px; height: 6px; margin-left: -1px; background: #0009; pointer-events: none; }
  .key { position: absolute; top: 7px; width: 13px; height: 13px; margin-left: -6.5px; background: var(--c); transform: rotate(45deg); border-radius: 2px; cursor: ew-resize; box-shadow: 0 0 0 1px #000a; }
  .key.empty { background: #0f1113; border: 2px solid var(--c); }
  .key.hard { outline: 2px solid #fff; outline-offset: 1px; }
  .key.on { box-shadow: 0 0 0 2px #fff; }
  .key.preview-only { opacity: .45; background: transparent; border: 1px dashed var(--c); }
  .playhead { position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; background: #ff5a5a; pointer-events: none; }
</style>
