<script lang="ts">
  import { onDestroy } from 'svelte';

  let { src, start = $bindable(0), end = $bindable(0), duration = $bindable(0), maxDuration=15.08, onerror }: {
    src: string; start?: number; end?: number; duration?: number; maxDuration?:number; onerror: (message: string) => void;
  } = $props();
  const MAX = $derived(maxDuration), STEP = 1/24;
  let video: HTMLVideoElement;
  let strip = $state<HTMLDivElement>();
  let thumbnails = $state<string[]>([]);
  let current = $state(0);
  let playing = $state(false);
  let drag: 'start' | 'end' | null = null;
  let thumbnailJob: AbortController | null = null;
  let playbackFrame = 0;
  const selected = $derived(end - start);
  const minimum = $derived(Math.min(STEP, duration));
  const pct = (time: number) => duration ? time / duration * 100 : 0;
  const stamp = (time: number) => `${Math.floor(time / 60)}:${(time % 60).toFixed(2).padStart(5, '0')}`;

  onDestroy(() => { thumbnailJob?.abort(); cancelAnimationFrame(playbackFrame); });

  function ready() {
    if (!Number.isFinite(video.duration) || video.duration <= 0) {
      onerror('Could not read the clip duration. Choose another video.'); return;
    }
    duration = video.duration; start = 0; end = Math.min(duration, MAX); current = 0;
    thumbnails = []; playing = false;
    makeThumbnails(src, duration);
  }

  async function makeThumbnails(url: string, seconds: number) {
    thumbnailJob?.abort();
    const job = new AbortController(); thumbnailJob = job;
    const thumb = document.createElement('video');
    thumb.muted = true; thumb.preload = 'auto';
    const wait = (event: string) => new Promise<void>((resolve, reject) => {
      const cleanup = () => { thumb.removeEventListener(event, done); thumb.removeEventListener('error', fail); job.signal.removeEventListener('abort', fail); };
      const done = () => { cleanup(); resolve(); };
      const fail = () => { cleanup(); reject(new Error('Thumbnail read stopped')); };
      thumb.addEventListener(event, done, { once: true }); thumb.addEventListener('error', fail, { once: true });
      job.signal.addEventListener('abort', fail, { once: true });
      if (job.signal.aborted) fail();
    });
    try {
      const loaded = wait('loadeddata'); thumb.src = url; await loaded;
      const canvas = document.createElement('canvas'); canvas.width = 160; canvas.height = 90;
      const g = canvas.getContext('2d')!;
      for (let i = 0; i < 10 && !job.signal.aborted; i++) {
        const seeked = wait('seeked'); thumb.currentTime = seconds * (i + 0.5) / 10; await seeked;
        if (job.signal.aborted) break;
        g.fillStyle = '#000'; g.fillRect(0, 0, 160, 90);
        const scale = Math.min(160 / thumb.videoWidth, 90 / thumb.videoHeight);
        const w = thumb.videoWidth * scale, h = thumb.videoHeight * scale;
        g.drawImage(thumb, (160 - w) / 2, (90 - h) / 2, w, h);
        thumbnails = [...thumbnails, canvas.toDataURL('image/jpeg', 0.65)];
      }
    } catch { /* Playback and trim remain available if thumbnails cannot be decoded. */ }
    finally { thumb.removeAttribute('src'); thumb.load(); }
  }

  function pause() { video?.pause(); playing = false; cancelAnimationFrame(playbackFrame); }
  function started() {
    playing = true; cancelAnimationFrame(playbackFrame);
    const advance = () => { update(); if (!video.paused) playbackFrame = requestAnimationFrame(advance); };
    playbackFrame = requestAnimationFrame(advance);
  }
  function seek(time: number) {
    pause(); current = Math.min(duration, Math.max(0, time)); video.currentTime = current;
  }
  function setEdge(edge: 'start' | 'end', value: number) {
    if (!Number.isFinite(value) || !duration) return;
    pause();
    if (edge === 'start') {
      start = Math.min(end - minimum, Math.max(0, value));
      if (end - start > MAX) end = start + MAX;
      video.currentTime = start; current = start;
    } else {
      end = Math.min(duration, Math.max(start + minimum, value));
      if (end - start > MAX) start = end - MAX;
      // Out is exclusive; show the final included frame.
      current = Math.max(start, end - STEP); video.currentTime = current;
    }
  }
  const timeAt = (ev: PointerEvent) => {
    const r = strip!.getBoundingClientRect(); return Math.min(duration, Math.max(0, (ev.clientX - r.left) / r.width * duration));
  };
  function down(ev: PointerEvent, edge: 'start' | 'end') {
    if (ev.button !== 0) return;
    drag = edge; (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
    setEdge(edge, timeAt(ev));
  }
  function key(ev: KeyboardEvent, edge: 'start' | 'end') {
    const value = edge === 'start' ? start : end, step = ev.shiftKey ? 1 : STEP;
    let next: number;
    if (ev.key === 'ArrowLeft' || ev.key === 'ArrowDown') next = value - step;
    else if (ev.key === 'ArrowRight' || ev.key === 'ArrowUp') next = value + step;
    else if (ev.key === 'Home') next = edge === 'start' ? 0 : start + minimum;
    else if (ev.key === 'End') next = edge === 'start' ? end - minimum : duration;
    else return;
    ev.preventDefault(); setEdge(edge, next);
  }
  async function play() {
    if (playing) { pause(); return; }
    if (video.currentTime < start || video.currentTime >= end - STEP / 2) video.currentTime = start;
    try { await video.play(); } catch { playing = false; }
  }
  function update() {
    current = video.currentTime;
    if (playing && current >= end) { pause(); video.currentTime = start; current = start; }
  }
  function reset() { start = 0; end = Math.min(duration, MAX); seek(0); }
</script>

<div class="clip-trim">
  <div class="picture">
    <video bind:this={video} {src} preload="auto" muted playsinline onloadedmetadata={ready}
      ontimeupdate={update} onplay={started} onpause={() => { playing = false; cancelAnimationFrame(playbackFrame); }} onended={() => { pause(); seek(start); }}
      onerror={() => onerror('This video could not be previewed. Choose a video your browser can play.')} aria-label="Clip trim preview"></video>
    {#if duration}<span class="timecode">{stamp(current)}</span>{/if}
  </div>
  {#if duration}
    <div class="transport">
      <button type="button" onclick={play} aria-label={playing ? 'Pause clip preview' : 'Play selected range'}>{playing ? 'Ⅱ Pause' : '▶ Play selection'}</button>
      <span>{selected.toFixed(2)}s selected <span class="muted">/ {duration.toFixed(2)}s source</span></span>
    </div>
    <div class="trim-track" bind:this={strip}>
      <div class="filmstrip" aria-hidden="true">{#each thumbnails as thumbnail}<img src={thumbnail} alt="" />{/each}</div>
      <button type="button" class="scrub" aria-label="Seek clip preview" onpointerdown={(ev) => seek(timeAt(ev))}
        onkeydown={(ev) => { if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') { ev.preventDefault(); seek(current + (ev.key === 'ArrowRight' ? STEP : -STEP)); } }}></button>
      <div class="shade left" style:width="{pct(start)}%"></div>
      <div class="shade right" style:left="{pct(end)}%"></div>
      <div class="selection" style:left="{pct(start)}%" style:width="{pct(selected)}%"></div>
      <div class="playhead" style:left="{pct(current)}%"></div>
      {#each ['start', 'end'] as edge}
        {@const isStart = edge === 'start'}
        <div role="slider" tabindex="0" class="handle" class:out={!isStart} style:left="{pct(isStart ? start : end)}%"
          aria-label={isStart ? 'Trim start' : 'Trim end'} aria-valuemin={isStart ? 0 : start + minimum}
          aria-valuemax={isStart ? end - minimum : duration} aria-valuenow={isStart ? start : end} aria-valuetext={stamp(isStart ? start : end)}
          onpointerdown={(ev) => down(ev, isStart ? 'start' : 'end')} onpointermove={(ev) => { if (drag) setEdge(drag, timeAt(ev)); }}
          onpointerup={() => drag = null} onpointercancel={() => drag = null} onlostpointercapture={() => drag = null}
          onkeydown={(ev) => key(ev, isStart ? 'start' : 'end')}><span></span><span></span></div>
      {/each}
    </div>
    <div class="range-labels"><span>In <strong>{stamp(start)}</strong></span><span>Out <strong>{stamp(end)}</strong></span></div>
    <div class="trim-help"><span>Drag the In / Out handles · up to {MAX}s</span><button type="button" class="ghost small" onclick={reset}>{duration <= MAX ? 'Use full clip' : 'Reset range'}</button></div>
  {:else}<p class="small muted">Reading clip…</p>{/if}
</div>

<style>
  .clip-trim { display:grid; gap:10px; min-width:0; }
  .picture { position:relative; background:#000; border-radius:7px; overflow:hidden; }
  video { display:block; width:100%; height:clamp(140px,28vh,260px); object-fit:contain; }
  .timecode { position:absolute; right:8px; bottom:8px; padding:3px 7px; background:#100e16c9; border-radius:4px; font:11px monospace; }
  .transport,.range-labels,.trim-help { display:flex; align-items:center; justify-content:space-between; gap:8px; font-size:11px; }
  .transport button { font-size:11px; padding:6px 9px; }
  .trim-track { position:relative; height:52px; margin:0 10px; background:var(--panel3); touch-action:none; }
  .filmstrip { display:flex; position:absolute; inset:0; overflow:hidden; border-radius:4px; }
  .filmstrip img { width:10%; height:100%; object-fit:cover; }
  .scrub { position:absolute; inset:0; width:100%; border:0; padding:0; border-radius:0; background:transparent; }
  .shade,.selection,.playhead { position:absolute; pointer-events:none; top:0; bottom:0; }
  .shade { background:rgba(12,8,20,.72); }.left { left:0; }.right { right:0; }
  .selection { border-top:2px solid var(--accent); border-bottom:2px solid var(--accent); }
  .playhead { width:1px; background:#fff; box-shadow:0 0 2px #000; }
  .handle { position:absolute; top:-2px; bottom:-2px; width:20px; transform:translateX(-50%); border-radius:4px; background:var(--accent); color:#17151b; cursor:ew-resize; display:flex; align-items:center; justify-content:center; gap:3px; touch-action:none; }
  .handle span { width:2px; height:17px; background:currentColor; opacity:.7; border-radius:1px; }
  .handle:focus-visible { outline:2px solid white; outline-offset:3px; }
  .range-labels { font-variant-numeric:tabular-nums; color:var(--muted); }.range-labels strong { color:var(--text); margin-left:5px; font-weight:500; }
  .trim-help { color:var(--muted); }.trim-help button { padding:3px 6px; white-space:nowrap; }
</style>
