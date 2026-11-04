<script lang="ts">
  // Live blocking view: the plate plays with every effect mocked up on it, tracked into the scene.
  // Ring = move (re-anchors at this frame; the auto-track carries it through the shot). Dot = aim (direction and strength).
  import type { Effect, Preset, Project } from './api';
  import { api } from './api';
  import { COLORS, image } from './effects';
  import { H, W, dirVec, endOf, onsetOf, renderLayers } from './mockup';
  import type { Tracker } from './track';
  import { onDestroy } from 'svelte';
  import { pruneSquib } from './squibfx';
  import { renderFlashPreview } from './flashPreview';
  import { colliderKey, loadCollider, type ColliderSequence } from './collider';
  import {isElementProject,loadVolumeScene,sceneKey,nativeAspect} from './volumeScene';
  import {setVolumeFXScene,renderVolumeElement,pruneVolumeFX} from './volumeFX';
  import type {VolumeFXScene} from './volumeGeometry';
  import {volumeKind,volumeOptions,elementOnset,elementTime} from './elementOptions';
  import {ViewerFrameCache,viewerPlateKey,viewerVisualKey,type ViewerCacheStatus} from './viewerCache';

  let { project, presets, frame, selectedId, soloEffectId = null, armed, tracker, paths, showHandles = true, caption = null, quality = 'draft', onplace, onchange, onselect, onframes, oncache }: {
    project: Project; presets: Record<string, Preset>; frame: number; selectedId: string | null; armed: string | null;
    tracker: Tracker | null; paths: Map<string, Float32Array>; showHandles?: boolean; caption?: string | null;
    onplace: (x: number, y: number) => void; onchange: (e: Effect) => void; onselect: (id: string | null) => void;
    onframes?: (loaded: number, total: number) => void;
    oncache?: (status:ViewerCacheStatus)=>void;
    quality?: 'draft' | 'high';
    soloEffectId?: string | null;
  } = $props();
  // Visibility is temporary UI state; keep the full project for physics/cache
  // identity, tracking, guides and downloaded/exported all-FX results.
  const previewEffects = $derived(soloEffectId && project.effects.some(e=>e.id===soloEffectId)
    ? project.effects.filter(e=>e.id===soloEffectId) : project.effects);
  const soloEffect = $derived(soloEffectId && previewEffects.length===1 ? previewEffects[0] : null);

  let canvas: HTMLCanvasElement;
  const frameCache=new ViewerFrameCache<HTMLCanvasElement>(undefined,value=>{value.width=0;value.height=0;});
  let cacheFrames=$state(0),cacheHit=$state(false),assetRevision=$state(0);
  let cacheRevision=0,expectedFrames:number[]=[],publishedCacheState='';
  // Decode/model-stamp readiness changes imagery; plate arrivals are scoped to
  // their own frame and must not throw away other already-correct snapshots.
  function assetsChanged(){frameCache.clear();assetRevision++;tick++;}
  const fxC = document.createElement('canvas'), glowC = document.createElement('canvas');
  fxC.width = glowC.width = W; fxC.height = glowC.height = H;
  let plates: (ImageBitmap | null)[] = [];
  let plateFailures=new Set<number>();
  let loadedFor = '';
  let tick = $state(0);
  let collider = $state.raw<ColliderSequence | null>(null);
  let colliderStatus = $state('');
  let loadedColliderKey: string | null = null, colliderRequest = 0;
  let volumeData=$state.raw<VolumeFXScene|null>(null),volumeStatus=$state(''),volumeError=$state('');
  let loadedVolumeKey='',volumeRequest=0;
  $effect(()=>{
    const active=isElementProject(project)&&project.effects.length>0,key=active?sceneKey(project,W,H):'';
    if(key===loadedVolumeKey)return;
    loadedVolumeKey=key;const request=++volumeRequest;volumeData=null;volumeError='';volumeStatus=active?'Loading simulation…':'';
    if(active)loadVolumeScene(project,W,H).then(value=>{
      if(request!==volumeRequest)return;volumeData=value;volumeStatus='Simulation preview';tick++;
    }).catch(error=>{if(request===volumeRequest){volumeError=String(error);volumeStatus='';}});
  });
  $effect(() => {
    if(isElementProject(project)){colliderRequest++;collider=null;colliderStatus='';loadedColliderKey=null;return;}
    const key = colliderKey(project);
    if (key === loadedColliderKey) return;
    loadedColliderKey = key;
    const request = ++colliderRequest;
    collider = null; colliderStatus = key ? 'Loading collider matte…' : '';
    if (key) loadCollider(project).then(value => {
      if (request === colliderRequest) { collider = value; colliderStatus = 'Collider active'; }
    }).catch(error => { if (request === colliderRequest) colliderStatus = String(error); });
  });
  let drag: { e: Effect; mode: 'move' | 'aim'; ox: number; oy: number } | null = null;
  let hover = $state<{ id: string; part: 'ring' | 'aim' } | null>(null);
  const AIM = 70; // px of aim handle at full strength
  let fxKey = '', fxPaths: Map<string, Float32Array> | null = null;
  onDestroy(() => { colliderRequest++;volumeRequest++; loadedFor = '';frameCache.clear(); for (const p of plates) p?.close(); pruneSquib(new Set());pruneVolumeFX(new Set(),'preview'); });

  // ---- plate frames: fetch once as bitmaps so playback is smooth
  $effect(() => {
    const key = viewerPlateKey(project);
    if (key === loadedFor) return;
    for (const p of plates) p?.close();frameCache.clear();
    loadedFor = key; plates = new Array(project.frames).fill(null);plateFailures=new Set();
    let done = 0; const total = project.frames, mine = key;
    const order = [...Array(total).keys()].sort((a, b) => Math.abs(a - frame) - Math.abs(b - frame));
    (async () => {
      const queue = [...order];
      const worker = async () => {
        while (queue.length && loadedFor === mine) {
          const i = queue.shift()!;
          const base = !isElementProject(project)&&project.mode === 'greenscreen' && project.environment ? `environment:${project.environment}` : 'plate';
          try {const b=await(await fetch(api.frameUrl(project.id,i,base))).blob(),bitmap=await createImageBitmap(b);if(loadedFor===mine)plates[i]=bitmap;else bitmap.close();} catch {if(loadedFor===mine)plateFailures.add(i); /* retry on next open */}
          if(loadedFor!==mine)return;
          done++; onframes?.(done, total); if (Math.abs(i - frame) < 2) tick++;
        }
      };
      await Promise.all([worker(), worker(), worker(), worker(), worker(), worker()]);
      if(loadedFor===mine)tick++;
    })();
  });

  const posAt = (e: Effect, f: number): [number, number] => {
    const pth = paths.get(e.id); if (!pth) return [0, 0];
    const i = Math.min(project.frames - 1, Math.max(0, f)); return [pth[i * 2], pth[i * 2 + 1]];
  };
  const live = (e: Effect, f: number) => {
    if(isElementProject(project)){const t=elementTime(project,f),start=Math.max(0,elementTime(project,elementOnset(e)));return !!volumeKind(e)&&t>=start&&t<start+(e.element?.duration_s??2)-1e-9;}
    const p = presets[e.preset]; return p && f >= onsetOf(e, p) - 3 && f <= endOf(e, p, project.frames);
  };
  const aimPos = (e: Effect, f: number): [number, number] | null => {
    const d = presets[e.preset]?.direction; if (!d || d.kind === 'none') return null;
    const [x, y] = posAt(e, f), v = dirVec(e.angle ?? d.angle), L = d.kind === 'barrel' ? AIM * 0.8 : 18 + AIM * (e.strength ?? d.strength);
    return [x + v[0] * L, y + v[1] * L];
  };

  function draw() {
    if (!canvas) return;
    const g = canvas.getContext('2d')!;
    let cacheChanged=frameCache.configure(viewerVisualKey(project,presets,paths,quality,soloEffectId,volumeData?.key??null,collider?.key??null,assetRevision));
    if(cacheChanged){cacheRevision++;expectedFrames=Array.from({length:project.frames},(_,f)=>f).filter(f=>previewEffects.some(e=>live(e,f))||!soloEffect&&project.flashes?.some(item=>item.frame===f));}
    const saved=frameCache.get(frame);cacheHit=!!saved;
    let renderedReady=!!saved;
    const pl = plates[frame] ?? plates.find((b) => b) ?? null;
    g.globalCompositeOperation = 'source-over';
    g.fillStyle = '#000'; g.fillRect(0, 0, W, H);
    if(saved)g.drawImage(saved,0,0,W,H);
    else{
    if (pl) g.drawImage(pl, 0, 0, W, H);
    const exactPlate=!!plates[frame],activeFX=previewEffects.some(e=>live(e,frame));
    let complete=exactPlate;
    if(isElementProject(project)&&exactPlate){
      if(volumeData){
        setVolumeFXScene(volumeData);pruneVolumeFX(new Set(project.effects.map(e=>e.id)),'preview');
        const time=elementTime(project,frame);
        try{
          for(const e of previewEffects){
            const kind=volumeKind(e),start=Math.max(0,elementTime(project,elementOnset(e)));
            if(!kind||time<start||time>=start+(e.element?.duration_s??2)-1e-9)continue;
            const options=volumeOptions(project,e,frame,paths,W,H);options.quality=quality;options.compositeOnly=true;options.interactive=true;
            const result=renderVolumeElement(e.id,kind,options,frame,'preview');
            if(result){g.save();try{g.globalAlpha=e.element?.opacity??1;g.filter=`brightness(${e.element?.gain??1})`;g.drawImage(result.composite,0,0,W,H);}finally{g.restore();}}
          }
          if(volumeError)volumeError='';
        }catch(error){complete=false;const text=String(error);if(volumeError!==text)volumeError=text;}
      }else if(activeFX)complete=false;
    }else if(!isElementProject(project)&&exactPlate){
      if(loadedColliderKey&&!collider)complete=false;
      const nextKey = JSON.stringify([project.id, frame, previewEffects, quality, tick, collider?.key]);
      if (nextKey !== fxKey || paths !== fxPaths) {
        renderLayers(fxC, glowC, previewEffects, presets, frame, project.frames, paths, assetsChanged, quality, 'preview', collider);
        fxKey = nextKey; fxPaths = paths;
      }
      g.globalCompositeOperation = 'lighter'; g.drawImage(glowC, 0, 0);
      g.globalCompositeOperation = 'source-over'; g.drawImage(fxC, 0, 0);
    }
    if (!soloEffect&&exactPlate&&project.flashes?.length){
      const stamp=image(api.stampUrl('muzzle_side','flash.png'),assetsChanged);
      complete=complete&&stamp.complete;
      if(!stamp.complete)stamp.addEventListener('error',assetsChanged,{once:true});
      renderFlashPreview(g,project.flashes,frame,assetsChanged);
    }
    // Empty plate-only frames are already cheap to draw and must not evict a
    // short expensive effect range in a longer shot. Store before live handles.
    if(complete&&(activeFX||!soloEffect&&project.flashes?.some(f=>f.frame===frame))){
      const snapshot=document.createElement('canvas');snapshot.width=W;snapshot.height=H;
      snapshot.getContext('2d')!.drawImage(canvas,0,0);
      cacheChanged=frameCache.put(frame,snapshot,W*H*4)||cacheChanged;
    }
    renderedReady=complete;
    }
    cacheFrames=frameCache.stats().frames;
    const cacheError=!!volumeError||plateFailures.has(frame),state=`${cacheRevision}:${frame}:${renderedReady}:${cacheError}`;
    if(cacheChanged||state!==publishedCacheState){publishedCacheState=state;const frames=frameCache.frames(),available=new Set(frames),capacity=Math.floor(frameCache.budget/(W*H*4));oncache?.({revision:cacheRevision,frame,ready:renderedReady,error:cacheError,frames,capacity,total:expectedFrames.length,warm:expectedFrames.every(f=>available.has(f)),fits:expectedFrames.length<=capacity});}
    if (!showHandles) return;
    for (const e of previewEffects) {
      const sel = e.id === selectedId; if (!sel && !live(e, frame)) continue;
      const [x, y] = posAt(e, frame), col = sel ? '#edff79' : 'rgba(236,239,241,.85)', a = aimPos(e, frame);
      g.save();
      if (!live(e, frame)) g.globalAlpha = 0.45;
      const strokeBoth = (fn: () => void, w: number, c: string) => { g.lineWidth = w + 2.5; g.strokeStyle = 'rgba(0,0,0,.5)'; fn(); g.stroke(); g.lineWidth = w; g.strokeStyle = c; fn(); g.stroke(); };
      if (a) strokeBoth(() => { g.beginPath(); const v = [a[0] - x, a[1] - y], L = Math.hypot(v[0], v[1]) || 1; g.moveTo(x + v[0] / L * 9, y + v[1] / L * 9); g.lineTo(a[0], a[1]); }, 1.4, col);
      const hr = hover?.id === e.id && hover.part === 'ring';
      strokeBoth(() => { g.beginPath(); g.arc(x, y, hr ? 10 : 8, 0, Math.PI * 2); }, hr ? 2 : 1.4, col);
      strokeBoth(() => { g.beginPath(); g.moveTo(x - 3, y); g.lineTo(x + 3, y); g.moveTo(x, y - 3); g.lineTo(x, y + 3); }, 1.2, col);
      if (a) { g.fillStyle = col; g.beginPath(); g.arc(a[0], a[1], hover?.id === e.id && hover.part === 'aim' ? 6 : 4.5, 0, Math.PI * 2); g.fill(); }
      if (!sel && hover?.id !== e.id) { g.restore(); continue; }
      const label = `${presets[e.preset]?.name ?? e.preset}`;
      g.font = '500 11px Inter, sans-serif';
      const tw = g.measureText(label).width, away = a ? [x - a[0], y - a[1]] : [0, -1], al = Math.hypot(away[0], away[1]) || 1;
      const ux = away[0] / al, uy = away[1] / al;
      const lx = Math.min(W - tw - 10, Math.max(6, x + ux * (16 + tw / 2) - tw / 2)), ly = Math.min(H - 8, Math.max(16, y + uy * 24 + 4));
      g.fillStyle = 'rgba(14,16,18,.78)'; g.fillRect(lx - 5, ly - 12, tw + 10, 17);
      g.fillStyle = sel ? '#edff79' : (COLORS[e.preset] ?? '#dde0e2'); g.fillText(label, lx, ly);
      g.restore();
    }
  }

  $effect(() => { void tick; void frame; void selectedId; void soloEffectId; void hover; void paths; void quality; void showHandles;void volumeData;void volumeError; JSON.stringify([project.effects, project.flashes]); draw(); });
  $effect(() => { (window as any).__keyfxCanvas = canvas; (window as any).__keyfxViewerProject = project; });

  // ---- interaction
  function pt(ev: PointerEvent | WheelEvent) { const r = canvas.getBoundingClientRect(); return [((ev.clientX - r.left) / r.width) * W, ((ev.clientY - r.top) / r.height) * H]; }
  function hit(x: number, y: number): { e: Effect; part: 'ring' | 'aim' } | null {
    const list = [...previewEffects].reverse();
    const sel = list.find((e) => e.id === selectedId);
    if (sel) { const a = aimPos(sel, frame); if (a && Math.hypot(a[0] - x, a[1] - y) < 11) return { e: sel, part: 'aim' }; }
    for (const e of [...(sel ? [sel] : []), ...list]) {
      if (e.id !== selectedId && !live(e, frame)) continue;
      const [ex, ey] = posAt(e, frame);
      if (Math.hypot(ex - x, ey - y) < 16) return { e, part: 'ring' };
    }
    return null;
  }

  function moveAnchor(e: Effect, x: number, y: number, pin: boolean) {
    const nx = Math.min(1, Math.max(0, x / W)), ny = Math.min(1, Math.max(0, y / H));
    const as = e.anchors ?? [];
    const near = as.find((a) => Math.abs(a.frame - frame) <= 2);
    if (near) { near.x = nx; near.y = ny; near.frame = frame; }
    else if (as.length <= 1 && !pin) e.anchors = [{ frame, x: nx, y: ny }];
    else e.anchors = [...as, { frame, x: nx, y: ny }];
  }

  function down(ev: PointerEvent) {
    const [x, y] = pt(ev);
    if (armed) { onplace(x / W, y / H); return; }
    const h = hit(x, y);
    if (!h) { onselect(null); return; }
    onselect(h.e.id);
    const [ex, ey] = posAt(h.e, frame);
    drag = { e: h.e, mode: h.part === 'aim' ? 'aim' : 'move', ox: ex - x, oy: ey - y };
    canvas.setPointerCapture(ev.pointerId);
  }
  function move(ev: PointerEvent) {
    const [x, y] = pt(ev);
    if (!drag) { const h = hit(x, y); const nh = h ? { id: h.e.id, part: h.part } : null; if (JSON.stringify(nh) !== JSON.stringify(hover)) hover = nh; return; }
    const e = drag.e;
    if (drag.mode === 'move') moveAnchor(e, x + drag.ox, y + drag.oy, ev.altKey);
    else {
      const [ex, ey] = posAt(e, frame), d = presets[e.preset].direction!;
      e.angle = Math.round(((Math.atan2(x - ex, -(y - ey)) * 180) / Math.PI + 360) % 360);
      if (d.kind !== 'barrel') e.strength = Math.round(Math.min(1, Math.max(0, (Math.hypot(x - ex, y - ey) - 18) / AIM)) * 100) / 100;
    }
    onchange(e);
  }
  function up() { if (drag) { onchange(drag.e); drag = null; } }
  function wheel(ev: WheelEvent) {
    const e = project.effects.find((q) => q.id === selectedId); if (!e) return;
    ev.preventDefault();
    e.size = Math.min(3, Math.max(0.2, (e.size ?? 1) * (ev.deltaY < 0 ? 1.05 : 1 / 1.05)));
    onchange(e);
  }

  const cursor = $derived(armed ? 'crosshair' : hover ? (hover.part === 'aim' ? 'grab' : 'move') : 'default');
  const hint = $derived.by(() => {
    if (armed) {
      const preset=presets[armed];
      const timing=(preset?.default_start_frame??0)<0?'already running at frame 0':'starts on this frame';
      return `Click to place ${preset?.name??'the effect'} · ${timing}`;
    }
    const e = project.effects.find((q) => q.id === selectedId);
    if (!e) return project.effects.length ? 'Click a ring to select · space plays' : 'Pick an effect, then click on the shot';
    const d = presets[e.preset]?.direction;
    const aim = d && d.kind !== 'none' ? ` · Dot: ${d.kind === 'barrel' ? 'aim the barrel' : d.label.toLowerCase()}` : '';
    return `Ring: move${aim} · Scroll: size · ⌥-drag: pin a fix on this frame`;
  });
</script>

<div class="viewer" class:armed>
  <canvas bind:this={canvas} width={W} height={H} style:cursor={cursor} style:aspect-ratio={isElementProject(project)?nativeAspect(project):undefined}
          onpointerdown={down} onpointermove={move} onpointerup={up} onpointerleave={() => (hover = null)} onwheel={wheel}></canvas>
  <div class="hint">{caption ?? hint}</div>
  {#if colliderStatus}<div class="collider-status" role="status">{colliderStatus}</div>{/if}
  {#if volumeStatus||volumeError}<div class="collider-status" class:error={!!volumeError} role="status">{volumeError||volumeStatus}{#if !volumeError&&soloEffect} · Solo: {presets[soloEffect.preset]?.name??soloEffect.preset}{/if}</div>{/if}
  {#if project.effects.length&&cacheFrames}<div class="cache-status" title="Rendered preview frames are reused on replay. Changes automatically clear the cache; memory is bounded.">{cacheHit?'Cached playback':'Caching preview'} · {cacheFrames} frames</div>{/if}
  {#if !tracker}<div class="tracking">Tracking the shot…</div>{/if}
</div>

<style>
  .viewer { position: relative; background: #000; border-radius: 8px; overflow: hidden; border: 1px solid var(--line); }
  canvas { display: block; width: 100%; height: auto; touch-action: none; }
  .hint { position: absolute; left: 10px; bottom: 10px; background: rgba(10, 12, 14, .72); padding: 5px 10px; border-radius: 14px; font-size: 12px; color: #d9dde0; pointer-events: none; }
  .tracking { position: absolute; right: 10px; top: 10px; background: rgba(10, 12, 14, .72); padding: 4px 10px; border-radius: 12px; font-size: 11px; color: var(--accent); }
  .collider-status { position: absolute; left: 10px; top: 10px; max-width: 75%; background: rgba(10,12,14,.8); padding: 4px 10px; border-radius: 12px; font-size: 11px; color: var(--accent); }
  .collider-status.error { color: #ff9a91; }
  .cache-status{position:absolute;right:10px;bottom:10px;background:rgba(10,12,14,.72);padding:5px 10px;border-radius:14px;font-size:11px;color:#d9dde0;pointer-events:none}
</style>
