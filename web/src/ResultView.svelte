<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import type { Project, Render } from './api';
  import { api } from './api';
  import { frameAtTime, frameTime, safeFps, safeAspect, previewCrop } from './nativeTiming';

  let { project, render, fps = 24, timestamps, aspectRatio = 16 / 9, onclose, onapprove }: {
    project: Project; render: Render; fps?: number; timestamps?: number[]; aspectRatio?: number;
    onclose: () => void; onapprove: (r: Render) => void;
  } = $props();
  let split = $state(50);
  let a: HTMLVideoElement | undefined = $state(), b: HTMLVideoElement | undefined = $state();
  let paused = $state(true);
  let currentFrame = $state(0);
  let checked = $state(false);
  let dialog: HTMLDialogElement;
  onMount(() => dialog.showModal());
  let showOriginalKeys = $state(false);
  let animation = 0;
  let encodedWidth = $state(0), encodedHeight = $state(0);
  const crop = $derived(previewCrop(encodedWidth, encodedHeight, project.native_profile?.width, project.native_profile?.height));
  const masterFile = $derived((render.files as {master?: string}).master);
  const plateFile = $derived((render.files as {plate?: string}).plate);

  const url = (f: string) => api.renderUrl(project.id, render.id, f);
  function sync() {
    if (a) currentFrame = frameAtTime(a.currentTime, project.frames, fps, timestamps);
    if (a && b && (frameAtTime(b.currentTime, project.frames, fps, timestamps) !== currentFrame || Math.abs(a.currentTime - b.currentTime) > 1 / (2 * safeFps(fps))))
      b.currentTime = a.currentTime;
  }
  function playback() {
    sync();
    if (a && !a.paused) animation = requestAnimationFrame(playback);
  }
  function toggle() {
    if (!a) return;
    if (a.paused) {
      void Promise.all([a.play(), ...(b ? [b.play()] : [])]).then(() => {
        paused = false; cancelAnimationFrame(animation); playback();
      }).catch(() => { a?.pause(); b?.pause(); paused = true; });
    } else { a.pause(); b?.pause(); paused = true; cancelAnimationFrame(animation); }
  }
  function seek(f: number) {
    if (!a) return;
    a.pause(); b?.pause(); paused = true; cancelAnimationFrame(animation);
    currentFrame = Math.max(0, Math.min(project.frames - 1, f));
    a.currentTime = frameTime(currentFrame, fps, timestamps);
    if (b) b.currentTime = a.currentTime;
  }
  onDestroy(() => { cancelAnimationFrame(animation); a?.pause(); b?.pause(); });
</script>

<dialog bind:this={dialog} aria-label="Compare take with source plate" oncancel={onclose}>
  <div class="sheet" class:review={render.kind !== 'keys'}>
    {#if render.kind === 'keys'}
    <div class="row head">
      <strong>Keys · {render.id}</strong>
      <span class="muted small">{render.polish ? 'AI-polished' : 'as placed'} · these frames steer the render</span>
      <span class="grow"></span>
      <button aria-pressed={showOriginalKeys} onclick={() => showOriginalKeys=!showOriginalKeys}>{showOriginalKeys ? "Show effect keys" : "Compare source plate"}</button>
      <button class="ghost" aria-label="Close comparison" onclick={onclose}>Close</button>
    </div>
    <div class="keygrid">
      {#each render.files.keys ?? [] as k}
        <figure><img src={showOriginalKeys ? api.frameUrl(project.id,Number(k.match(/(\d+)\.png$/)?.[1] ?? 0),"plate") : url(k)} alt={showOriginalKeys ? "Original source plate" : "Effect key"} /><figcaption>frame {Number(k.match(/(\d+)\.png$/)?.[1] ?? 0)}</figcaption></figure>
      {/each}
    </div>
    {:else}
    <div class="row head">
      <strong>{render.kind === 'environment' ? 'Environment pass' : 'Render'} · {render.id}</strong>
      <span class="muted small">seed {render.seed}{render.kind === 'environment' ? render.lms_strength!=null ? ` · HD finish · LMS ${render.lms_strength}` : '' : ` · keys ${render.strength}`}</span>
      <span class="grow"></span>
      <a href={url('result.mp4')} download><button>{masterFile ? 'Download preview' : 'Download result'}</button></a>
      {#if masterFile}<a href={url(masterFile)} download><button>{project.native_profile?.rgb_range_policy==='legalize'?'Download working-copy master':'Download native master'}</button></a>{/if}
      {#if render.files.compare}<a href={url(render.files.compare)} download><button>Side by side</button></a>{/if}
      <button class="ghost" aria-label="Close comparison" onclick={onclose}>Close</button>
    </div>
    {#if render.quality_warnings?.length}<div class="notice" role="note"><strong>Review issues</strong>{#each render.quality_warnings as warning}<p class="small">{warning}</p>{/each}</div>{/if}
    <div class="stage" style:--split="{split}%" style:--aspect={safeAspect(aspectRatio)}>
      <video bind:this={a} src={url('result.mp4')} style:width="{crop[0] * 100}%" style:height="{crop[1] * 100}%" style:object-fit={crop[0] === 1 && crop[1] === 1 ? 'contain' : 'fill'} loop muted playsinline onloadedmetadata={() => {if(a){encodedWidth=a.videoWidth;encodedHeight=a.videoHeight;}}} ontimeupdate={sync}></video>
      {#if plateFile}<video bind:this={b} class="top" src={url(plateFile)} loop muted playsinline></video>
      {:else}<img class="top" src={api.frameUrl(project.id, currentFrame, 'plate')} alt={`Original source frame ${currentFrame}`} />{/if}
      <div class="line"></div>
      <span class="lab l">Original</span><span class="lab r">Bang On</span>
    </div>
    <div class="row">
      <button class="ghost" aria-label="Previous review frame" onclick={() => seek(currentFrame-1)}>◀</button>
      <button class="ghost" aria-label="Play or pause comparison" onclick={toggle}>{paused ? '▶' : '❚❚'}</button>
      <button class="ghost" aria-label="Next review frame" onclick={() => seek(currentFrame+1)}>▶|</button>
      <span class="small">Frame {currentFrame} / {project.frames-1}</span>
      <input class="grow" type="range" min="0" max={project.frames-1} step="1" value={currentFrame} oninput={(e) => seek(+(e.currentTarget as HTMLInputElement).value)} aria-label="Review frame" />
    </div>
    <div class="row">
      <button class="small" onclick={() => seek(0)}>Opening</button>
      <button class="small" onclick={() => seek(Math.floor((project.frames-1)/2))}>Middle</button>
      <button class="small" onclick={() => seek(project.frames-1)}>Ending</button>
      <input class="grow" type="range" min="0" max="100" bind:value={split} aria-label="Wipe" />
      <span class="muted small">Original ↔ result</span>
    </div>
    {#if render.kind === 'environment'}
      <p class="muted small">{render.opening_reference ? (render.reference_mode === 'scene_and_opening' ? 'Opening guide + separate scene reference' : 'One reference for the opening and room') : 'Earlier render without an explicit opening guide'}</p>
      {#if project.approved_environment === render.id}<p class="small">This is the approved environment.</p>
      {:else}
        <label class="row small"><input type="checkbox" bind:checked={checked} /> I checked the people, movement, full background and ending, including unwanted effects.</label>
        <button class="primary" disabled={!checked} onclick={() => onapprove(render)}>Approve and use this environment</button>
        <p class="muted small">Your current environment stays selected until approval. Results made on another version will be marked outdated.</p>
      {/if}
    {/if}
    {/if}
  </div>
</dialog>

<style>
  dialog { border:0;padding:0;background:transparent;color:var(--text);max-width:96vw;max-height:96vh; }
  dialog::backdrop { background:rgba(14,10,20,.8); }
  .head { flex-wrap:wrap; }
  .sheet { width: min(1200px, 94vw); max-height: 94vh; overflow: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px; display: grid; gap: 10px; }
  .sheet.review { width: min(1200px, 94vw, calc(96vh + 28px)); display: flex; flex-direction: column; }
  .sheet.review > * { flex-shrink: 0; }
  .head a { text-decoration: none; }
  .stage { position: relative; width:100%;max-width:min(100%,calc(68vh * var(--aspect)));align-self:center;aspect-ratio:var(--aspect);background:#000;border-radius:6px;overflow:hidden; }
  video, .stage img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
  .stage img { object-fit:fill }
  .top { clip-path: inset(0 calc(100% - var(--split)) 0 0); }
  .line { position: absolute; top: 0; bottom: 0; left: var(--split); width: 2px; background: #fff; opacity: .8; pointer-events: none; }
  .lab { position: absolute; top: 10px; font-size: 11px; background: rgba(0, 0, 0, .55); padding: 3px 8px; border-radius: 10px; }
  .lab.l { left: 10px; } .lab.r { right: 10px; }
  .keygrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 10px; max-height: 78vh; overflow: auto; }
  figure { margin: 0; display: grid; gap: 4px; }
  figure img { width: 100%; border-radius: 6px; display: block; }
  figcaption { font-size: 11px; color: var(--muted); }
</style>
