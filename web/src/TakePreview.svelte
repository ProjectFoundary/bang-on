<script lang="ts">
  import { onDestroy } from 'svelte';
  import { frameAtTime, frameTime, safeAspect, previewCrop } from './nativeTiming';
  let { src, frame = $bindable(0), playing = $bindable(false), frames, controls = true, fps = 24, timestamps, aspectRatio = 16 / 9, pictureWidth, pictureHeight }: {
    src: string; frame: number; playing?: boolean; frames: number; controls?: boolean;
    fps?: number; timestamps?: number[]; aspectRatio?: number;
    pictureWidth?: number; pictureHeight?: number;
  } = $props();
  let video = $state<HTMLVideoElement>();
  let request = 0;
  let encodedWidth = $state(0), encodedHeight = $state(0);
  const crop = $derived(previewCrop(encodedWidth, encodedHeight, pictureWidth, pictureHeight));
  function sync() {
    if (video) frame = frameAtTime(video.currentTime, frames, fps, timestamps);
    if (video && !video.paused) request = requestAnimationFrame(sync);
  }
  $effect(() => {
    if (!video) return;
    if (playing) video.play().catch(() => playing = false);
    else video.pause();
  });
  $effect(() => {
    if (video && !playing && frameAtTime(video.currentTime, frames, fps, timestamps) !== frame)
      video.currentTime = frameTime(frame, fps, timestamps);
  });
  onDestroy(() => { cancelAnimationFrame(request); video?.pause(); });
</script>
<div class="preview" style:aspect-ratio={safeAspect(aspectRatio)}>
<!-- svelte-ignore a11y_media_has_caption -->
<video bind:this={video} {src} {controls} style:width="{crop[0] * 100}%" style:height="{crop[1] * 100}%" loop playsinline onloadedmetadata={() => {if(video){encodedWidth=video.videoWidth;encodedHeight=video.videoHeight;if(!playing)video.currentTime=frameTime(frame,fps,timestamps);}}} onplay={() => { playing = true; cancelAnimationFrame(request); sync(); }} onseeked={() => { if (video?.paused) sync(); }} onpause={() => { playing = false; cancelAnimationFrame(request); }} aria-label="Generated result"></video>
</div>
<style>.preview{position:relative;width:100%;overflow:hidden;background:#110f16}video{position:absolute;inset:0;object-fit:fill;display:block}</style>
