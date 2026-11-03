<script lang="ts">
  // Room design reviews the source plate and generated location. Object
  // rectangles are drawn on an approved still in DressingView instead.
  import { api, type Project } from './api';
  let { project, frame, overlay = null, mix = 0 }: {
    project: Project; frame: number; overlay: string | null; mix: number;
  } = $props();
  let canvas: HTMLCanvasElement | undefined = $state();
  let tick = $state(0);
  const images = new Map<string, HTMLImageElement>();
  function image(url: string) {
    let im = images.get(url);
    if (!im) { im = new Image(); im.onload = () => tick++; im.src = url; images.set(url, im); }
    return im.complete && im.naturalWidth ? im : null;
  }
  function draw() {
    if (!canvas) return;
    const g = canvas.getContext('2d')!;
    g.fillStyle = '#000'; g.fillRect(0,0,960,540);
    const plate = image(api.frameUrl(project.id,frame,'plate'));
    if (plate) g.drawImage(plate,0,0,960,540);
    if (overlay && mix>0) {
      const location = image(`/api/projects/${project.id}/background?file=${encodeURIComponent(overlay)}`);
      if (location) { g.globalAlpha=mix;g.drawImage(location,0,0,960,540);g.globalAlpha=1; }
    }
  }
  $effect(()=>{void tick;void project.id;void frame;void overlay;void mix;draw();});
</script>

<div class="viewer">
  <canvas bind:this={canvas} width="960" height="540" aria-label="Source plate and location reference comparison"></canvas>
  <div class="hint">Use Source plate or Wipe to check pose, perspective and floor contact</div>
</div>

<style>
  .viewer{position:relative;width:100%;min-height:min-content;justify-self:center;background:#000;border:1px solid var(--line);border-radius:8px;overflow:hidden}
  canvas{display:block;width:100%;height:auto}
  .hint{position:absolute;left:10px;bottom:10px;background:rgba(10,12,14,.75);padding:5px 10px;border-radius:14px;font-size:12px;color:#d9dde0;pointer-events:none}
</style>
