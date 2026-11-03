<script lang="ts">
  import type { Snippet } from 'svelte';
  import { api } from './api';
  import { safeAspect } from './nativeTiming';
  let { projectId, frame, mode = $bindable('result'), split = $bindable(50), children, aspectRatio = 16 / 9 }: {
    projectId: string; frame: number; mode: 'result' | 'source' | 'wipe'; split: number; children: Snippet;
    aspectRatio?: number;
  } = $props();
</script>

<div class="comparison" class:source={mode === 'source'} style:--aspect={safeAspect(aspectRatio)}>
  <div class="result">{#if mode !== 'source'}{@render children()}{/if}</div>
  {#if mode !== 'result'}
    <div class="original" style:clip-path={mode === 'wipe' ? `inset(0 ${100-split}% 0 0)` : undefined}>
      <img src={api.frameUrl(projectId, frame, 'plate')} alt={`Original source plate, frame ${frame}`} />
      <span>Source plate · {frame}</span>
    </div>
    {#if mode === 'wipe'}<div class="wipe-line" style:left="{split}%"></div>{/if}
  {/if}
</div>

<style>
  .comparison{position:relative;width:100%;aspect-ratio:var(--aspect);border-radius:12px;overflow:hidden;background:#110f16;isolation:isolate}
  .result{width:100%;min-height:1px}.source .result{aspect-ratio:var(--aspect);pointer-events:none}
  .original{position:absolute;inset:0;pointer-events:none;background:#110f16}
  .original img{width:100%;height:100%;object-fit:fill;display:block}
  .original span{position:absolute;top:12px;left:12px;background:#17141dcc;border:1px solid #ffffff20;border-radius:6px;padding:5px 8px;font-size:11px}
  .wipe-line{position:absolute;top:0;bottom:0;width:2px;background:var(--accent);pointer-events:none}
</style>
