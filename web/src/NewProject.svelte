<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { api, type Project } from './api';
  import ClipTrim from './ClipTrim.svelte';
  import { get } from 'svelte/store';
  import { connection } from './connection';

  let { oncreated, oncancel }: { oncreated: (p: Project) => void; oncancel: () => void } = $props();
  let file: File | null = $state(null);
  let name = $state('');
  let mode = $state<'plate' | 'greenscreen'>('plate');
  let colourProfile = $state('auto');
  let rgbRange = $state<'preserve'|'legalize'>('legalize');
  let start = $state(0);
  let end = $state(0);
  let sourceUrl = $state('');
  let duration = $state(0);
  let analyse = $state(true);
  let busy = $state(false);
  let error = $state('');
  let dialog: HTMLDialogElement;
  onMount(() => { analyse=!!get(connection)?.configured; dialog.showModal(); });
  onDestroy(() => { if (sourceUrl) URL.revokeObjectURL(sourceUrl); });

  async function create() {
    if (!file || !duration || end <= start) return;
    busy = true; error = '';
    try { oncreated(await api.create(file, name, mode, start, end - start, mode==='greenscreen'&&analyse, colourProfile, rgbRange)); }
    catch (e) { error = e instanceof Error?e.message:String(e); }
    finally { busy = false; }
  }
  function chooseFile(f: File | undefined) {
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    file=f??null;duration=0;start=0;end=0;sourceUrl='';error='';rgbRange='legalize';
    if(!file)return;
    if(!name)name=file.name.replace(/\.[^.]+$/,'');
    sourceUrl=URL.createObjectURL(file);
  }
</script>

<dialog bind:this={dialog} aria-label="New shot" oncancel={oncancel}>
  <div class="sheet">
    <h3>New shot</h3>
    <label>Choose your footage
      <input type="file" accept="video/*" disabled={busy} onchange={(e) => chooseFile(e.currentTarget.files?.[0])} />
    </label>
    {#if sourceUrl}{#key sourceUrl}<ClipTrim src={sourceUrl} bind:start bind:end bind:duration maxDuration={mode==='plate'?60:15.08} onerror={(text) => {error=text;duration=0;}}/>{/key}{/if}
    <label>Name <input bind:value={name} placeholder="Shot name" /></label>
    <div class="modes">
      <button class:sel={mode === 'plate'} aria-pressed={mode === 'plate'} onclick={() => (mode = 'plate')}><strong>Real footage</strong><span>Add effects to a shot as it is.</span></button>
      <button class:sel={mode === 'greenscreen'} aria-pressed={mode === 'greenscreen'} onclick={() => (mode = 'greenscreen')}><strong>New environment</strong><span>Green screen or ordinary footage. Keep the people and replace the setting.</span></button>
    </div>
    {#if mode==='plate'}
      <label>Source colour interpretation<select bind:value={colourProfile}><option value="auto">Use source colour tags</option><option value="rec709">Assign Rec.709 SDR</option><option value="srgb">Assign sRGB</option><option value="pq2020">Assign Rec.2020 PQ / HDR10</option><option value="hlg2020">Assign Rec.2020 HLG</option></select></label>
      <label>RGB working range<select bind:value={rgbRange} disabled={busy} onchange={()=>error=''}><option value="preserve">Preserve source values · reject clipping</option><option value="legalize">Legal-range RGB working copy · clip outliers</option></select></label>
      {#if rgbRange==='legalize'}<p class="range-warning" role="status">This clips decoded RGB below 0 or above 1 in the working master. Extreme colours or highlights may change. Resolution, timing and colour interpretation stay the same; your original file is kept unchanged.</p>{:else}<p class="muted small">Strict preservation stops if decoded RGB exceeds the integer master’s range. For that footage you can explicitly choose a legal-range working copy, or use a float/EXR compositor to retain all values.</p>{/if}
      <p class="muted small">Display/model previews are separate. Untagged footage needs an explicit colour assignment. Each generated FX range can be up to five seconds.</p>
    {:else}
      <label class="row"><input type="checkbox" bind:checked={analyse} disabled={!$connection?.configured}/> Draft action in the background</label>
      <p class="muted small">Scene generation uses the existing 1080p / 24fps working profile.</p>
    {/if}
    {#if error}<p class="err">{error}</p>{/if}
    <div class="row"><span class="grow"></span><button onclick={oncancel}>Cancel</button>
      <button class="primary" disabled={!file || busy || !duration || end <= start} onclick={create}>{busy ? 'Importing…' : mode==='plate'&&rgbRange==='legalize'?'Create legal-range working copy':'Create'}</button></div>
  </div>
</dialog>

<style>
  dialog { padding:0; border:0; background:transparent; color:var(--text); max-width:96vw; max-height:94vh; }
  dialog::backdrop { background:rgba(14,10,20,.8); }
  .sheet { width: min(640px, 92vw); max-height:94vh; box-sizing:border-box; overflow-y:auto; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 20px; display: grid; gap: 14px; }
  h3 { margin: 0; font-weight: 600; }
  .modes { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .modes button { display: grid; gap: 4px; text-align: left; padding: 12px; }
  .modes button span { color: var(--muted); font-size: 12px; }
  .modes button.sel { border-color: var(--accent); background: var(--panel3); }
  .err { color: var(--danger); margin: 0; }
  .range-warning {padding:10px;border:1px solid var(--accent);border-radius:7px;font-size:12px;line-height:1.5;margin:0}
</style>
