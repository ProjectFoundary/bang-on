<script lang="ts">
  import { onMount } from 'svelte';
  import { api, type ConnectionDraft, type ConnectionReport, type ConnectionSummary } from './api';

  let { initial, onclose, onsaved }: {
    initial: ConnectionSummary | null; onclose: () => void; onsaved: (value: ConnectionSummary) => void;
  } = $props();
  let mode = $state<'cloud' | 'local'>('cloud');
  let cloudKey = $state(''), localToken = $state('');
  let localUrl = $state('http://127.0.0.1:8189'), comfyuiUrl = $state('http://127.0.0.1:8188');
  let allowPartner = $state(false);
  let clearCloud = $state(false), clearToken = $state(false);
  let report = $state<ConnectionReport | null>(null);
  let busy = $state(''), error = $state('');
  let dialog: HTMLDialogElement;
  onMount(() => {
    if (initial) {
      mode = initial.mode; localUrl = initial.local_url; comfyuiUrl = initial.comfyui_url;
      allowPartner = initial.allow_cloud_partner;
    }
    dialog.showModal();
  });
  const hasCloud = $derived(!!cloudKey.trim() || !!initial?.has_cloud_key && !clearCloud);
  const keepToken = $derived(!!initial?.has_local_token && !clearToken && localUrl.trim().replace(/\/+$/, '') === initial.local_url);
  const canTest = $derived(mode === 'cloud' ? hasCloud : !!localUrl && !!comfyuiUrl && (!allowPartner || hasCloud));
  const readyCount = $derived(report?.checks.filter(check => check.status === 'ready').length ?? 0);
  const body = (): ConnectionDraft => ({mode, local_url:localUrl, comfyui_url:comfyuiUrl, cloud_api_key:cloudKey,
    local_token:localToken, allow_cloud_partner:allowPartner, clear_cloud_key:clearCloud, clear_local_token:clearToken});
  function changed() { report = null; error = ''; }
  async function test() {
    busy = 'Testing…'; error = ''; report = null;
    try { report = await api.testConnection(body()); }
    catch (e) { error = String(e); }
    finally { busy = ''; }
  }
  async function save() {
    if (!report?.connected || !report.test_id) return;
    busy = 'Saving…'; error = '';
    try { onsaved(await api.saveConnection({...body(), test_id:report.test_id})); cloudKey=''; localToken=''; }
    catch(e) { error = String(e); }
    finally { busy = ''; }
  }
  async function disconnect() {
    busy = 'Disconnecting…'; error = '';
    try { onsaved(await api.disconnect()); cloudKey=''; localToken=''; }
    catch(e) { error = String(e); }
    finally { busy = ''; }
  }
  function switchMode(value: 'cloud' | 'local') { mode=value; changed(); }
  const labels = {ready:'Ready',missing:'Missing',unknown:'Not verified',off:'Off'};
</script>

<dialog bind:this={dialog} aria-label="Connect your Comfy" oncancel={(ev) => { if(busy)ev.preventDefault();else onclose(); }}>
  <div class="sheet">
    <div class="head"><div><span class="eyebrow">BANG ON · SETUP</span><h1>Connect your Comfy.</h1></div>
      <button class="ghost small" aria-label="Close connection setup" disabled={!!busy} onclick={onclose}>Close</button></div>
    <p class="intro">Choose where your effects are generated. You can change this connection later.</p>
    <div class="setup-body">
    <fieldset disabled={!!busy}>
      <div class="modes" role="group" aria-label="Comfy connection type">
        <button class:selected={mode==='cloud'} aria-pressed={mode==='cloud'} onclick={() => switchMode('cloud')}><strong>Comfy Cloud</strong><span>Your account. Your Cloud credits.</span></button>
        <button class:selected={mode==='local'} aria-pressed={mode==='local'} onclick={() => switchMode('local')}><strong>Local ComfyUI</strong><span>Your installation and GPU.</span></button>
      </div>
      {#if mode==='cloud'}
        <label>Comfy Cloud API key
          <input type="password" bind:value={cloudKey} oninput={() => {clearCloud=false;changed();}} autocomplete="off" spellcheck="false"
            placeholder={initial?.has_cloud_key && !clearCloud ? 'Saved key · leave blank to keep it' : 'Paste your Comfy Cloud API key'} />
        </label>
        <div class="helper"><a href="https://platform.comfy.org/profile/api-keys" target="_blank" rel="noreferrer">Get a Comfy API key ↗</a>
          {#if initial?.has_cloud_key && !clearCloud}<button class="ghost small" onclick={() => {clearCloud=true;cloudKey='';changed();}}>Remove saved Cloud key</button>{/if}</div>
      {:else}
        <label>ComfyUI address <input type="url" bind:value={comfyuiUrl} oninput={changed} placeholder="http://127.0.0.1:8188" /></label>
        <label>Comfy API proxy address <input type="url" bind:value={localUrl} oninput={changed} placeholder="http://127.0.0.1:8189" /></label>
        <p class="small muted">Bang On runs workflows through the proxy and checks models on your ComfyUI installation.</p>
        <details><summary>Start the local API <span>One-time setup</span></summary><div class="instructions">
          <p>Start ComfyUI, then install and run Comfy’s API proxy in a Python environment:</p>
          <pre>python -m pip install comfy-api-proxy\ncomfy-api-proxy</pre>
          <p>The defaults above work when Bang On and ComfyUI run on the same computer. For a different machine, enter its address and the proxy’s configured port.</p>
          <a href="https://github.com/Comfy-Org/comfy-api-proxy#quickstart" target="_blank" rel="noreferrer">Proxy setup guide ↗</a>
        </div></details>
        <details><summary>Proxy authentication <span>{keepToken ? 'Token saved' : 'Optional'}</span></summary><div class="instructions">
          <label>Local proxy token <input type="password" bind:value={localToken} oninput={() => {clearToken=false;changed();}} autocomplete="off" spellcheck="false" placeholder={keepToken ? 'Saved token · leave blank to keep it' : 'Only if your proxy requires a token'} /></label>
          {#if initial?.has_local_token && !clearToken}<button class="ghost small" onclick={() => {clearToken=true;localToken='';changed();}}>Remove saved proxy token</button>{/if}
          <p>This token is only used with the proxy address. Your Cloud key stays separate. Enter the token again if you change the proxy address.</p>
        </div></details>
        <label class="check"><input type="checkbox" bind:checked={allowPartner} onchange={changed} /> Also use Comfy Cloud for Nano Banana</label>
        {#if allowPartner}<label>Comfy Cloud API key <input type="password" bind:value={cloudKey} oninput={() => {clearCloud=false;changed();}} autocomplete="off" spellcheck="false" placeholder={initial?.has_cloud_key && !clearCloud ? 'Saved Cloud key · leave blank to keep it' : 'Paste your Comfy Cloud API key'} /></label>
          <p class="small muted">Native models run locally. Nano Banana image edits use your Cloud account and credits.</p>
        {:else}<p class="small muted">Local-only setup uses Qwen / FLUX for image edits. Nano Banana is available with a Cloud connection.</p>{/if}
      {/if}
      <button class="test-button" disabled={!canTest || !!busy} onclick={test}>{busy==='Testing…' ? busy : 'Test connection & check models'}</button>
    </fieldset>
    {#if report}<section class="report" aria-live="polite" aria-label="Connection test results">
      <div class="result-head" class:success={report.connected}><strong>{report.connected ? 'Connection verified' : 'Could not connect'}</strong>
        {#if report.connected}<span>{readyCount} / {report.checks.length} tools ready</span>{/if}</div>
      <p class="small">{report.message}</p>
      {#each report.checks as check}<details class="model-check"><summary><span class="model-name">{check.label}</span><span class="status {check.status}">{labels[check.status]}</span></summary>
        <div class="check-details">{#if check.details.length}{#each check.details as detail}<p>{detail}</p>{/each}
          {:else}<p>Required nodes and model names are available in the catalogue.</p>{/if}</div>
      </details>{/each}
      {#if report.catalogue_note}<p class="small muted catalogue-note">{report.catalogue_note}</p>{/if}
      {#if report.connected && !report.ready}<p class="small muted">You can save this connection now. Install or verify the models for the tools you want, then test again.</p>{/if}
    </section>{/if}
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    <p class="small muted storage-note">Saved for this Bang On installation. Keys stay on this computer and are never included in your projects or exports.</p>
    </div>
    <div class="footer">{#if initial?.configured}<button class="ghost danger small" disabled={!!busy} onclick={disconnect}>Disconnect</button>{/if}
      <span class="grow"></span><button class="ghost" disabled={!!busy} onclick={onclose}>Set up later</button>
      <button class="primary" disabled={!!busy || !report?.connected || !report.test_id} onclick={save}>{busy==='Saving…' ? busy : 'Save connection'}</button></div>
  </div>
</dialog>

<style>
  dialog { padding:0; border:0; color:var(--text); background:transparent; max-width:96vw; max-height:94vh; }
  dialog::backdrop { background:rgba(14,10,20,.82); }
  .sheet { width:min(610px,94vw); max-height:94vh; overflow:hidden; background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:24px; display:flex; flex-direction:column; gap:17px; }
  .setup-body { min-height:0; overflow-y:auto; display:grid; align-content:start; gap:17px; padding:1px 4px 4px; margin:0 -4px; scrollbar-width:thin; }
  .head,.intro,.footer { flex-shrink:0; }
  .head { display:flex; gap:12px; justify-content:space-between; align-items:start; }.head>button { padding:6px 8px; }
  .eyebrow { font-size:9px; letter-spacing:.12em; color:var(--accent); font-weight:600; }h1 { font-size:27px; line-height:1.2; letter-spacing:-.04em; margin:8px 0 0; font-weight:750; }
  p { margin:0; }.intro { color:var(--muted); font-size:12px; line-height:1.7; }
  fieldset { border:0; padding:0; margin:0; min-width:0; display:grid; gap:15px; }
  .modes { display:grid; grid-template-columns:1fr 1fr; gap:10px; }.modes button { display:grid; text-align:left; gap:7px; padding:14px; }.modes button.selected { border-color:var(--accent); background:var(--panel3); }.modes strong { font-size:13px; font-weight:600; }.modes span { color:var(--muted); font-size:11px; line-height:1.5; }
  input { width:100%; }input[type=checkbox] { width:auto; flex:none; margin:0; }.check { display:flex; align-items:center; gap:9px; line-height:1.5; }
  .helper { display:flex; justify-content:space-between; align-items:center; gap:10px; font-size:11px; }.helper button { padding:4px; }
  .instructions { padding-top:12px; display:grid; gap:12px; font-size:11px; color:var(--muted); line-height:1.7; }pre { white-space:pre-wrap; overflow-wrap:anywhere; padding:12px; background:var(--bg); border:1px solid var(--line); border-radius:7px; margin:0; color:var(--text); }
  .test-button { width:100%; border-color:var(--line); font-weight:600; padding:11px; }.report { border:1px solid var(--line); border-radius:10px; padding:14px; display:grid; gap:11px; }.result-head { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px; color:var(--danger); font-size:12px; }.result-head.success { color:var(--accent); }.result-head>span { color:var(--muted); font-size:10px; }.model-check { border-top:1px solid var(--line); padding-top:8px; }.model-name { font-size:11px; color:var(--text); }.status { padding:3px 6px; border-radius:5px; background:var(--panel3); white-space:nowrap; }.status.ready { color:var(--accent2); }.status.missing { color:var(--danger); }.status.unknown { color:var(--accent); }.check-details { display:grid; gap:5px; padding:9px 0 3px; font-size:11px; line-height:1.6; overflow-wrap:anywhere; }.catalogue-note { border-top:1px solid var(--line); padding-top:10px; }
  .error { color:var(--danger); line-height:1.6; font-size:12px; overflow-wrap:anywhere; }.storage-note { font-size:10px; }.footer { display:flex; align-items:center; flex-wrap:wrap; gap:8px; border-top:1px solid var(--line); padding-top:16px; }.footer button { font-size:11px; }
  @media(max-width:440px) { .sheet { padding:18px; gap:15px; }h1 { font-size:24px; }.modes button { padding:11px; }.modes span { font-size:10px; }.helper { align-items:start; flex-direction:column; }.footer { gap:6px; }.footer button { padding:8px; } }
</style>
