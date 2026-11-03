import * as fflate from 'fflate';

/* =========================================================================
   EXR writer (scanline, HALF, ZIP compression)
   ========================================================================= */
export const HT=(()=>{ const base=new Uint16Array(512), shift=new Uint8Array(512);
  for (let i=0;i<256;i++){ const e=i-127;
    if (e<-24){ base[i]=0; base[i|256]=0x8000; shift[i]=shift[i|256]=24; }
    else if (e<-14){ base[i]=0x0400>>(-e-14); base[i|256]=(0x0400>>(-e-14))|0x8000; shift[i]=shift[i|256]=-e-1; }
    else if (e<=15){ base[i]=(e+15)<<10; base[i|256]=((e+15)<<10)|0x8000; shift[i]=shift[i|256]=13; }
    else if (e<128){ base[i]=0x7C00; base[i|256]=0xFC00; shift[i]=shift[i|256]=24; }
    else { base[i]=0x7C00; base[i|256]=0xFC00; shift[i]=shift[i|256]=13; } }
  return {base,shift}; })();
export function toHalf(f32,u16){ const u=new Uint32Array(f32.buffer,f32.byteOffset,f32.length), B=HT.base, S=HT.shift;
  for (let i=0;i<u.length;i++){ const f=u[i], e=(f>>>23)&511; u16[i]=B[e]+((f&0x7fffff)>>S[e]); } }

export function writeEXR(w,h,chans){
  chans=chans.slice().sort((a,b)=>a.name<b.name?-1:a.name>b.name?1:0);
  const useZip=typeof fflate!=='undefined';
  const bytes=[]; const enc=new TextEncoder();
  const u8=a=>{ for (const b of a) bytes.push(b); };
  const i32=v=>{ bytes.push(v&255,(v>>8)&255,(v>>16)&255,(v>>>24)&255); };
  const f32=v=>{ const b=new Uint8Array(new Float32Array([v]).buffer); u8(b); };
  const str=s=>{ u8(enc.encode(s)); bytes.push(0); };
  const attr=(name,type,size,fn)=>{ str(name); str(type); i32(size); fn(); };
  u8([0x76,0x2f,0x31,0x01]); i32(2);
  let chSize=1; for (const c of chans) chSize+=enc.encode(c.name).length+1+16;
  attr('channels','chlist',chSize,()=>{ for (const c of chans){ str(c.name); i32(1); u8([0,0,0,0]); i32(1); i32(1); } bytes.push(0); });
  attr('compression','compression',1,()=>bytes.push(useZip?3:0));
  attr('dataWindow','box2i',16,()=>{ i32(0);i32(0);i32(w-1);i32(h-1); });
  attr('displayWindow','box2i',16,()=>{ i32(0);i32(0);i32(w-1);i32(h-1); });
  attr('lineOrder','lineOrder',1,()=>bytes.push(0));
  attr('pixelAspectRatio','float',4,()=>f32(1));
  attr('screenWindowCenter','v2f',8,()=>{ f32(0); f32(0); });
  attr('screenWindowWidth','float',4,()=>f32(1));
  bytes.push(0);
  const header=new Uint8Array(bytes);
  const lpb=useZip?16:1, nb=Math.ceil(h/lpb), nch=chans.length;
  const raw=new Uint8Array(lpb*nch*w*2), raw16=new Uint16Array(raw.buffer), tmp=new Uint8Array(raw.length);
  const blocks=[]; let total=header.length+nb*8;
  for (let b=0;b<nb;b++){
    const y0=b*lpb, lines=Math.min(lpb,h-y0); let o=0;
    for (let ly=0;ly<lines;ly++){
      const row=(h-1-(y0+ly))*w*4;
      for (const c of chans){ const src=c.buf; let s=row+c.comp; for (let x=0;x<w;x++){ raw16[o++]=src[s]; s+=4; } }
    }
    const n=lines*nch*w*2; let data;
    if (useZip){
      let t1=0, t2=(n+1)>>1;
      for (let i=0;i<n;){ tmp[t1++]=raw[i++]; if (i<n) tmp[t2++]=raw[i++]; }
      let p=tmp[0]; for (let i=1;i<n;i++){ const d=(tmp[i]-p+384)&255; p=tmp[i]; tmp[i]=d; }
      const z=fflate.zlibSync(tmp.subarray(0,n),{level:4});
      data = z.length<n ? z : raw.slice(0,n);
    } else data=raw.slice(0,n);
    blocks.push({y:y0,data}); total+=8+data.length;
  }
  const out=new Uint8Array(total), dv=new DataView(out.buffer);
  out.set(header,0); let pos=header.length+nb*8;
  blocks.forEach((bl,i)=>{ dv.setUint32(header.length+i*8,pos,true); dv.setUint32(header.length+i*8+4,0,true);
    dv.setInt32(pos,bl.y,true); dv.setInt32(pos+4,bl.data.length,true); out.set(bl.data,pos+8); pos+=8+bl.data.length; });
  return out;
}
