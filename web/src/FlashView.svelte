<script lang="ts">
  // Muzzle placement within the shared live preview. Each final flash is still edited and spliced independently.
  import type { Flash, Preset, Project } from './api';
  import { api } from './api';
  import { frameSecondsLabel, safeAspect } from './nativeTiming';
  import { onDestroy } from 'svelte';
  import { renderLayers } from './mockup';
  import { renderFlashPreview } from './flashPreview';
  import { pruneSquib } from './squibfx';
  import { colliderKey, loadCollider, type ColliderSequence } from './collider';
  import { loadVolumeScene, isElementProject, sceneKey } from './volumeScene';
  import { setVolumeFXScene, renderVolumeElement, pruneVolumeFX, type VolumeFXScene } from './volumeFX';
  import { volumeKind, volumeOptions, elementTime, elementOnset } from './elementOptions';

  let { project, base, presets, paths, quality = 'draft', showHandles = true, frame = $bindable(0), selectedId = $bindable(null), fps = 24, timestamps, aspectRatio = 16 / 9, onchange, onerror }: {
    project: Project; base: string; presets: Record<string, Preset>; paths: Map<string, Float32Array>;
    quality?: 'draft' | 'high'; showHandles?: boolean; frame: number; selectedId: string | null; onchange: () => void; onerror?: (message: string) => void;
    fps?: number; timestamps?: number[]; aspectRatio?: number;
  } = $props();

  const W = 960, H = 540, WW = 1280, WH = 720;          // view canvas and muzzle_hub's working canvas
  const SIZES = { small: 32, medium: 96, large: 240 };
  let canvas: HTMLCanvasElement;
  let frames: (ImageBitmap | null)[] = [];
  const fxC = document.createElement('canvas'), glowC = document.createElement('canvas');
  fxC.width = glowC.width = W; fxC.height = glowC.height = H;
  let fxKey = '', fxPaths: Map<string, Float32Array> | null = null;
  let loadedFor = '';
  const cacheRadius = 24;
  let frameQueue: number[] = [], activeLoads = 0;
  const inFlight = new Set<string>();
  let sourceLoading = $state(false);
  let tick = $state(0);
  let collider = $state.raw<ColliderSequence | null>(null);
  let colliderStatus = $state('');
  let loadedColliderKey: string | null = null, colliderRequest = 0;
  $effect(() => {
    const key = isElementProject(project) ? '' : colliderKey(project);
    if (key === loadedColliderKey) return;
    loadedColliderKey = key;
    const request = ++colliderRequest;
    collider = null; colliderStatus = key ? 'Loading collider matte…' : '';
    if (key) loadCollider(project).then(value => {
      if (request === colliderRequest) { collider = value; colliderStatus = 'Collider active'; }
    }).catch(error => { if (request === colliderRequest) colliderStatus = String(error); });
  });
  const volumeOwner = 'flash-preview';
  let volumeScene = $state.raw<VolumeFXScene | null>(null);
  let volumeStatus = $state('');
  let loadedVolumeKey = '', volumeRequest = 0;
  $effect(() => {
    const key = isElementProject(project) ? sceneKey(project, W, H) : '';
    if (key === loadedVolumeKey) return;
    loadedVolumeKey = key;
    const request = ++volumeRequest;
    volumeScene = null;
    volumeStatus = key ? 'Loading 3D collision scene…' : '';
    pruneVolumeFX(new Set(), volumeOwner);
    if (key) loadVolumeScene(project, W, H).then(value => {
      if (request !== volumeRequest) return;
      volumeScene = value; setVolumeFXScene(value); volumeStatus = ''; tick++;
    }).catch(error => {
      if (request === volumeRequest) volumeStatus = String(error);
    });
  });
  let drag: { f: Flash; part: 'new' | 'origin' | 'tip' | 'move'; ox: number; oy: number; tx: number; ty: number; sx: number; sy: number } | null = null;
  let hover = $state<string | null>(null);
  let pending: { held: boolean; x: number; y: number } | null = null;

  const flashes = $derived(project.flashes ?? []);
  const here = $derived(flashes.filter((f) => f.frame === frame));
  onDestroy(() => { colliderRequest++; volumeRequest++; loadedFor = ''; frameQueue=[]; for (const f of frames) f?.close(); pruneSquib(new Set()); pruneVolumeFX(new Set(), volumeOwner); });

  function pumpFrames() {
    while (activeLoads < 4 && frameQueue.length && loadedFor) {
      const i = frameQueue.shift()!, mine = loadedFor, key = `${mine}|${i}`;
      if (frames[i] || inFlight.has(key)) continue;
      const pid = project.id, sourceBase = base;
      activeLoads++; inFlight.add(key);
      void (async () => {
        try {
          const response = await fetch(api.frameUrl(pid, i, sourceBase));
          if (!response.ok) throw new Error('Source frame unavailable.');
          const bitmap = await createImageBitmap(await response.blob());
          if (loadedFor === mine && Math.abs(i - frame) <= cacheRadius) {
            frames[i] = bitmap; tick++;
          } else bitmap.close();
        } catch { /* Retry when the playhead next visits this window. */ }
        finally { activeLoads--; inFlight.delete(key); pumpFrames(); }
      })();
    }
  }

  $effect(() => {
    const key = `${project.id}|${base}`;
    if (key !== loadedFor) {
      for (const f of frames) f?.close();
      loadedFor = key; frames = new Array(project.frames).fill(null);
    }
    // Native shots can contain thousands of frames. Never decode/cache an
    // entire shot merely to place a muzzle flash; keep a bounded scrub window.
    const focus = frame;
    for (let i=0;i<frames.length;i++) if (frames[i] && Math.abs(i-focus)>cacheRadius) {
      frames[i]!.close(); frames[i]=null;
    }
    const lo = Math.max(0,focus-cacheRadius), hi = Math.min(project.frames-1,focus+cacheRadius);
    frameQueue = Array.from({length:Math.max(0,hi-lo+1)},(_,j)=>lo+j)
      .filter(i=>!frames[i]&&!inFlight.has(`${key}|${i}`)).sort((a,b)=>Math.abs(a-focus)-Math.abs(b-focus));
    pumpFrames();
  });
  const px = (p: { x: number; y: number }) => [p.x * W, p.y * H] as const;

  function draw() {
    if (!canvas) return;
    const g = canvas.getContext('2d')!;
    const im = frames[frame] ?? frames.find((b) => b) ?? null;
    sourceLoading = !frames[frame];
    g.fillStyle = '#000'; g.fillRect(0, 0, W, H);
    if (im) g.drawImage(im, 0, 0, W, H);
    if (isElementProject(project)) {
      if (volumeScene) {
        try {
          setVolumeFXScene(volumeScene);
          pruneVolumeFX(new Set(project.effects.map(e => e.id)), volumeOwner);
          for (const e of project.effects) {
            const kind = volumeKind(e);
            if (!kind) continue;
            const onset = Math.max(0,elementTime(project, elementOnset(e))), now = elementTime(project, frame);
            if (now < onset || now >= onset + (e.element?.duration_s ?? 2)) continue;
            const options = volumeOptions(project, e, frame, paths, W, H);
            options.quality = quality;
            const result = renderVolumeElement(e.id, kind, options, frame, volumeOwner);
            if (result) {g.save();try{g.globalAlpha=e.element?.opacity??1;g.filter=`brightness(${e.element?.gain??1})`;g.drawImage(result.composite,0,0,W,H);}finally{g.restore();}}
          }
        } catch (error) {
          const message = String(error);
          if (volumeStatus !== message) { volumeStatus = message; onerror?.(message); }
        }
      }
    } else {
      const nextKey = JSON.stringify([project.id, frame, project.effects, quality, tick, collider?.key]);
      if (nextKey !== fxKey || paths !== fxPaths) {
        renderLayers(fxC, glowC, project.effects, presets, frame, project.frames, paths, () => tick++, quality, 'preview', collider);
        fxKey = nextKey; fxPaths = paths;
      }
      g.globalCompositeOperation = 'lighter'; g.drawImage(glowC, 0, 0);
      g.globalCompositeOperation = 'source-over'; g.drawImage(fxC, 0, 0);
    }
    renderFlashPreview(g, flashes, frame, () => tick++);
    if (!showHandles) return;
    for (const f of flashes) {
      const on = f.frame === frame, sel = f.id === selectedId;
      if (!on) continue;
      const [x, y] = px(f.origin), [tx, ty] = px(f.tip);
      const col = sel ? '#edff79' : f.result_current ? '#7fd1c0' : '#ffe27a';
      const line = (w: number, c: string) => { g.lineWidth = w; g.strokeStyle = c; g.beginPath(); g.moveTo(x, y); g.lineTo(tx, ty); g.stroke(); };
      g.save();
      line(4, 'rgba(0,0,0,.5)'); line(1.6, col);
      const a = Math.atan2(ty - y, tx - x);
      g.fillStyle = col; g.beginPath(); g.moveTo(tx, ty); g.lineTo(tx - 9 * Math.cos(a - 0.45), ty - 9 * Math.sin(a - 0.45)); g.lineTo(tx - 9 * Math.cos(a + 0.45), ty - 9 * Math.sin(a + 0.45)); g.fill();
      g.lineWidth = 2; g.strokeStyle = col; g.beginPath(); g.arc(x, y, hover === f.id ? 8 : 6, 0, Math.PI * 2); g.stroke();
      g.restore();
    }
  }
  $effect(() => { void tick; void frame; void selectedId; void hover; void base; void showHandles; void quality; void paths; JSON.stringify([flashes, project.effects]); draw(); });
  $effect(() => { (window as any).__keyfxCanvas = canvas; (window as any).__keyfxViewerProject = project; });

  function pt(ev: PointerEvent) { const r = canvas.getBoundingClientRect(); return [((ev.clientX - r.left) / r.width) * W, ((ev.clientY - r.top) / r.height) * H] as const; }
  function hit(x: number, y: number): { f: Flash; part: 'origin' | 'tip' | 'move' } | null {
    for (const f of [...here].reverse()) {
      const [ox, oy] = px(f.origin), [tx, ty] = px(f.tip);
      if (Math.hypot(tx - x, ty - y) < 11) return { f, part: 'tip' };
      if (Math.hypot(ox - x, oy - y) < 11) return { f, part: 'origin' };
      const L = Math.hypot(tx - ox, ty - oy) || 1, u = ((x - ox) * (tx - ox) + (y - oy) * (ty - oy)) / (L * L);
      if (u > 0 && u < 1 && Math.abs((x - ox) * (ty - oy) - (y - oy) * (tx - ox)) / L < 7) return { f, part: 'move' };
    }
    return null;
  }
  const clamp = (v: number) => Math.min(1, Math.max(0, v));

  async function down(ev: PointerEvent) {
    if (pending || !showHandles) return;
    const [x, y] = pt(ev);
    const h = hit(x, y);
    canvas.setPointerCapture(ev.pointerId);
    if (h) {
      selectedId = h.f.id;
      drag = { f: h.f, part: h.part, ox: h.f.origin.x, oy: h.f.origin.y, tx: h.f.tip.x, ty: h.f.tip.y, sx: x, sy: y };
      return;
    }
    // new flash: the press is the muzzle; dragging sets the flame tip (a plain click gives a medium flash pointing right)
    const o = { x: clamp(x / W), y: clamp(y / H) }, t = { x: clamp(o.x + SIZES.medium / WW), y: o.y };
    const press = {held: true, x, y}; pending = press;
    try {
      const p = await api.addFlash(project.id, frame, o, t);
      project.flashes = p.flashes; const f = project.flashes![project.flashes!.length - 1];
      selectedId = f.id;
      if (Math.hypot(press.x-x,press.y-y)>6) f.tip = {x:clamp(press.x/W),y:clamp(press.y/H)};
      if (press.held) drag = { f, part: 'new', ox: o.x, oy: o.y, tx: t.x, ty: t.y, sx: x, sy: y };
      else onchange();
    } catch(e) { onerror?.(String(e)); }
    finally { pending = null; }
  }
  function move(ev: PointerEvent) {
    const [x, y] = pt(ev);
    if (pending) {pending.x=x;pending.y=y;return;}
    if (!drag) { const h = hit(x, y); hover = h ? h.f.id : null; return; }
    const f = flashes.find(f => f.id === drag!.f.id);
    if (!f) { drag = null; return; }
    const dx = (x - drag.sx) / W, dy = (y - drag.sy) / H;
    if (drag.part === 'new' || drag.part === 'tip') {
      if (drag.part === 'new' && Math.hypot(x - drag.sx, y - drag.sy) < 6) return;
      f.tip = drag.part === 'new' ? { x: clamp(x / W), y: clamp(y / H) } : { x: clamp(drag.tx + dx), y: clamp(drag.ty + dy) };
    } else if (drag.part === 'origin') f.origin = { x: clamp(drag.ox + dx), y: clamp(drag.oy + dy) };
    else { f.origin = { x: clamp(drag.ox + dx), y: clamp(drag.oy + dy) }; f.tip = { x: clamp(drag.tx + dx), y: clamp(drag.ty + dy) }; }
    tick++;
  }
  function up() { if(pending) pending.held=false; if (drag) { onchange(); drag = null; } }

  const cursor = $derived(hover ? 'grab' : 'crosshair');
</script>

<div class="viewer" style:--aspect={safeAspect(aspectRatio)}>
  <canvas bind:this={canvas} width={W} height={H} style:cursor={showHandles ? cursor : 'default'} onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={up} onpointerleave={() => (hover = null)}></canvas>
  <div class="hint">{!showHandles ? 'Preview · explosions + muzzle flashes' : here.length ? 'Drag the ring to move the muzzle, the arrow tip to aim and size the flame' : 'Click the muzzle and drag to where the flame ends · one flash per frame'}</div>
  <div class="tc">{frame} · {frameSecondsLabel(frame, fps, timestamps)}</div>
  {#if colliderStatus}<div class="collider-status" role="status">{colliderStatus}</div>{/if}
  {#if volumeStatus}<div class="collider-status" role="status">{volumeStatus}</div>{/if}
  {#if sourceLoading}<div class="source-status" role="status">Loading source frame…</div>{/if}
</div>

<style>
  .viewer { position: relative; background: #000; border-radius: 8px; overflow: hidden; border: 1px solid var(--line); }
  canvas { display: block; width: 100%; height: auto; aspect-ratio: var(--aspect); touch-action: none; }
  .hint { position: absolute; left: 10px; bottom: 10px; background: rgba(10, 12, 14, .72); padding: 5px 10px; border-radius: 14px; font-size: 12px; color: #d9dde0; pointer-events: none; }
  .tc { position: absolute; right: 10px; top: 10px; background: rgba(10, 12, 14, .72); padding: 3px 9px; border-radius: 12px; font-size: 11px; font-variant-numeric: tabular-nums; }
  .collider-status { position: absolute; left: 10px; top: 10px; max-width: 75%; background: rgba(10,12,14,.8); padding: 4px 10px; border-radius: 12px; font-size: 11px; color: var(--accent); }
  .source-status { position:absolute;left:10px;top:40px;background:rgba(10,12,14,.8);padding:4px 10px;border-radius:12px;font-size:11px;color:var(--muted);pointer-events:none }
</style>
