<script lang="ts">
  import mark from './assets/bang-on-mark.png';
  let { tagline = true }: { tagline?: boolean } = $props();
</script>

<div class="brand" aria-label="Bang On">
  <img class="brand-mark" src={mark} alt="" width="40" height="40" />
  <div class="brand-copy">
    <div class="wordmark" aria-hidden="true">BANG <span>ON</span><small>BETA</small></div>
    {#if tagline}<span class="tagline">Big effects. Right on cue.</span>{/if}
  </div>
</div>

<style>
  .brand { display:flex; align-items:center; gap:9px; flex-shrink:0; color:var(--text); }
  .brand-mark { display:block; width:40px; height:40px; object-fit:contain; flex-shrink:0; }
  .brand-copy { display:grid; gap:4px; }
  .wordmark { display:flex; align-items:center; gap:5px; font-size:24px; font-weight:900; line-height:1; letter-spacing:-1.2px; white-space:nowrap; }
  .wordmark>span { color:var(--accent); }
  small { align-self:center; font-size:8px; font-weight:600; letter-spacing:.08em; border:1px solid var(--line); padding:3px 4px; border-radius:4px; color:var(--muted); margin-left:4px; }
  .tagline { font-size:10px; color:var(--muted); letter-spacing:.015em; white-space:nowrap; }
  @media(max-width:760px) { .brand { gap:7px; }.brand-mark { width:34px; height:34px; }.wordmark { font-size:21px; }.tagline { font-size:9px; } }
  @media(max-width:440px) { .brand-mark { width:30px; height:30px; }.wordmark { font-size:19px; } small { display:none; } }
</style>
