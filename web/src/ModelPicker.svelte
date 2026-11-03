<script lang="ts">
  import type { ImageModel } from './api';
  import { nanoAvailable } from './connection';
  let { value = $bindable('qwen'), label = 'Image model', onchange }: {
    value: ImageModel; label?: string; onchange: () => void;
  } = $props();
</script>

<label>{label}
  <select bind:value onchange={onchange}>
    <option value="qwen">Qwen Image Edit · open weights</option>
    <option value="flux3">FLUX 3 · open weights</option>
    <option value="nano" disabled={!$nanoAvailable}>Nano Banana Pro{!$nanoAvailable ? ' · needs Cloud' : ''}</option>
  </select>
</label>
{#if value === 'nano' && !$nanoAvailable}<p class="small muted">Choose Qwen or FLUX for local image edits, or enable Cloud partner access in Connect Comfy.</p>{/if}
