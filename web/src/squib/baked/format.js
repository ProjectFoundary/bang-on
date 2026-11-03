import {unzlibSync} from 'fflate';

const MAX_BLOCK=128*1024*1024,MAX_CACHE=256*1024*1024;
const finiteArray=(a,n)=>Array.isArray(a)&&a.length===n&&a.every(Number.isFinite);
export class SmokeCache{
 constructor(input,budget=MAX_CACHE){
  const bytes=input instanceof Uint8Array?input:new Uint8Array(input);if(bytes.length<8||String.fromCharCode(...bytes.subarray(0,4))!=='SQC1')throw Error('Not a Squib smoke cache');
  const size=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength).getUint32(4,true);if(size>4*1024*1024||size>bytes.length-8)throw Error('Invalid cache header');
  const meta=JSON.parse(new TextDecoder().decode(bytes.subarray(8,8+size)));
  if(meta.version!==1||meta.codec!=='zlib'||!Array.isArray(meta.frames)||!meta.frames.length||meta.frames.length>4000||!Number.isFinite(meta.duration)||meta.duration<=0)throw Error('Unsupported smoke cache');
  if(meta.densityEncoding!=null&&!['r8sqrt','gaussian2d'].includes(meta.densityEncoding)||['baseScale','albedoScale'].some(k=>meta[k]!=null&&(!Number.isFinite(meta[k])||meta[k]<=0)))throw Error('Unsupported density calibration');
  if(!finiteArray(meta.bounds?.min,3)||!finiteArray(meta.bounds?.size,3)||meta.bounds.size.some(x=>x<=0)||
    !['densityScale','baseSceneDensity','sheetTauMax'].every(k=>Number.isFinite(meta[k])&&meta[k]>0)||
    !['baseDissipate','baseLinger'].every(k=>Number.isFinite(meta[k])&&meta[k]>=0))throw Error('Invalid smoke cache calibration');
  this.meta=meta;this.bytes=bytes;this.payload=8+size;this.budget=budget;this.used=0;this.decoded=new Map();this.generation=0;
  for(const [n,frame]of meta.frames.entries()){
   if(!Number.isFinite(frame.time)||frame.time<0||frame.time>meta.duration||n&&frame.time<=meta.frames[n-1].time)throw Error('Invalid cache timing');
   for(const mode of['density','sheet'])if(frame[mode]){
    const b=frame[mode],parametric=mode==='density'&&meta.densityEncoding==='gaussian2d',axis=mode==='density'&&!parametric?3:2,spatial=mode==='density'?3:2;
    if(!finiteArray(b.dim,axis)||b.dim.some(x=>!Number.isInteger(x)||x<1||x>1024)||!finiteArray(b.min,spatial)||!finiteArray(b.size,spatial)||b.size.some(x=>x<=0))throw Error('Invalid cache dimensions');
    const expected=b.dim.reduce((a,x)=>a*x,1)*(mode==='sheet'||parametric?8:1);
    if(expected!==b.bytes||expected>MAX_BLOCK||!Number.isSafeInteger(b.offset)||b.offset<0||!Number.isSafeInteger(b.length)||b.length<1||b.offset+b.length>bytes.length-this.payload)throw Error('Invalid cache block');
    if(mode==='density'&&!parametric&&(!Number.isFinite(b.scale)||b.scale<=0))throw Error('Invalid cache density scale');
   }
  }
  this.samples=Object.fromEntries(['density','sheet'].map(mode=>[mode,meta.frames.map((f,n)=>({f,n})).filter(x=>x.f[mode])]));
  if(!this.samples.density.length||!this.samples.sheet.length)throw Error('Cache must contain volume and projection data');
 }
 pair(mode,time){
  const list=this.samples[mode];let lo=0,hi=list.length-1;
  if(time<=list[0].f.time)return {a:list[0].n,b:list[0].n,weight:0};
  if(time>=list[hi].f.time)return {a:list[hi].n,b:list[hi].n,weight:0};
  while(hi-lo>1){const mid=(lo+hi)>>1;if(list[mid].f.time<=time)lo=mid;else hi=mid;}
  return {a:list[lo].n,b:list[hi].n,weight:(time-list[lo].f.time)/(list[hi].f.time-list[lo].f.time)};
 }
 get(index,mode){
  const key=`${mode}:${index}`,cached=this.decoded.get(key);if(cached){this.decoded.delete(key);this.decoded.set(key,cached);return cached;}
  const block=this.meta.frames[index]?.[mode];if(!block)throw Error('Missing smoke cache frame');
  const start=this.payload+block.offset,out=new Uint8Array(block.bytes),data=unzlibSync(this.bytes.subarray(start,start+block.length),{out});
  if(data.length!==block.bytes)throw Error('Truncated smoke cache data');
  while(this.used+data.byteLength>this.budget&&this.decoded.size){const k=this.decoded.keys().next().value;this.used-=this.decoded.get(k).byteLength;this.decoded.delete(k);}
  if(data.byteLength>this.budget)throw Error('Smoke frame exceeds decoded-cache budget');
  this.decoded.set(key,data);this.used+=data.byteLength;return data;
 }
 clear(){this.decoded.clear();this.used=0;this.generation++;}
}
