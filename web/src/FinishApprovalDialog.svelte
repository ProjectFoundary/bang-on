<script lang="ts">
  import {onMount} from 'svelte';
  let {count,busy=false,error='',onapprove,oncancel}:{count:number;busy?:boolean;error?:string;onapprove:()=>void;oncancel:()=>void}=$props();
  let dialog:HTMLDialogElement;
  onMount(()=>dialog.showModal());
</script>
<dialog bind:this={dialog} oncancel={e=>{e.preventDefault();if(!busy)oncancel();}} aria-labelledby="approve-effects-title">
  <h2 id="approve-effects-title">Approve current effects?</h2>
  <p>Approve the {count} effect{count===1?'':'s'} shown in your combined LTX preview and continue to Finish. Existing approved versions stay pinned.</p>
  <p class="small muted">This approves the preview you reviewed. It does not generate new footage.</p>
  {#if error}<p class="err" role="alert">{error}</p>{/if}
  <div class="actions"><button disabled={busy} onclick={oncancel}>Keep reviewing</button><button class="primary" disabled={busy} onclick={onapprove}>{busy?'Approving…':'Approve effects & continue'}</button></div>
</dialog>
<style>dialog{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:14px;padding:26px;max-width:470px;width:calc(100% - 40px)}dialog::backdrop{background:#000a}h2{margin:0 0 16px}p{line-height:1.6}.actions{display:flex;gap:10px;justify-content:flex-end;margin-top:22px}.err{color:var(--danger)}</style>
