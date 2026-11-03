<script lang="ts">
  import {offsetColliderPlane,blurPlane} from './volumeGeometry';
  let {src,displacement=0,blur=0,name='object'}:{src:string;displacement?:number;blur?:number;name?:string}=$props();
  let canvas:HTMLCanvasElement;
  let decoded=$state.raw<{width:number;height:number;values:Float32Array}|null>(null),error=$state('');
  $effect(()=>{
    const url=src;let active=true;decoded=null;error='';
    (async()=>{
      const response=await fetch(url);if(!response.ok)throw Error('Collision mask preview unavailable.');
      const image=await createImageBitmap(await response.blob());
      try{
        const c=document.createElement('canvas');c.width=image.width;c.height=image.height;const g=c.getContext('2d')!;g.drawImage(image,0,0);
        const rgba=g.getImageData(0,0,c.width,c.height).data,values=new Float32Array(c.width*c.height);
        for(let i=0;i<values.length;i++)values[i]=rgba[i*4]/255;
        if(active)decoded={width:c.width,height:c.height,values};
      }finally{image.close();}
    })().catch(e=>{if(active)error=String(e);});
    return ()=>{active=false;};
  });
  $effect(()=>{
    if(!canvas||!decoded)return;
    const {width,height}=decoded;let values=decoded.values;
    if(displacement)values=offsetColliderPlane(values,values,width,height,displacement*width/960).values;
    values=blurPlane(values,width,height,blur*width/960);
    canvas.width=width;canvas.height=height;const g=canvas.getContext('2d')!,pixels=g.createImageData(width,height);
    for(let i=0;i<values.length;i++){const v=Math.round(values[i]*255);pixels.data[i*4]=pixels.data[i*4+1]=pixels.data[i*4+2]=v;pixels.data[i*4+3]=255;}
    g.putImageData(pixels,0,0);
  });
</script>
<canvas bind:this={canvas} aria-label={`Adjusted collision mask for ${name}`}></canvas>
{#if error}<span class="small muted">{error}</span>{/if}
<style>canvas{display:block;width:100%;height:auto;background:#000;}</style>
