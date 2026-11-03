import { state } from '../state.js';
import { DEG } from '../util.js';

/* =========================================================================
   Tracked 3D camera (Nuke .chan): frame tx ty tz rx ry rz [vfov]
   World is Nuke's: +Y up, the camera looks down its local -Z. Rotations in degrees,
   applied in cam.rotOrder (Nuke's default ZXY = Z first, then X, then Y).
   ========================================================================= */
export function parseChan(text){
  const frames=new Map();
  for (const line of text.split(/\r?\n/)){
    const v=line.trim().split(/[\s,]+/).map(Number);
    if (v.length<7 || v.slice(0,7).some(x=>!Number.isFinite(x))) continue;
    frames.set(Math.round(v[0]), {t:[v[1],v[2],v[3]], r:[v[4],v[5],v[6]], vfov:Number.isFinite(v[7])?v[7]:null});
  }
  if (!frames.size) throw new Error('no camera keys found');
  const nums=[...frames.keys()].sort((a,b)=>a-b);
  return {frames, nums, first:nums[0], last:nums[nums.length-1], hasFov:[...frames.values()].some(k=>k.vfov!==null)};
}

function rotAxis(ax, deg){
  const c=Math.cos(deg*DEG), s=Math.sin(deg*DEG);
  if (ax==='X') return [1,0,0, 0,c,-s, 0,s,c];
  if (ax==='Y') return [c,0,s, 0,1,0, -s,0,c];
  return [c,-s,0, s,c,0, 0,0,1];
}
const mul=(a,b)=>{ const o=new Array(9); for (let i=0;i<3;i++) for (let j=0;j<3;j++) o[i*3+j]=a[i*3]*b[j]+a[i*3+1]*b[3+j]+a[i*3+2]*b[6+j]; return o; };

/* Camera at a frame: position, world-from-camera rotation (row-major 3x3) and projection. Frames outside the file hold the nearest key. */
export function camAt(frame){
  const C=state.cam; if (!C) return null;
  const f=Math.round(frame);
  let k=C.data.frames.get(f);
  if (!k){ let best=C.data.nums[0]; for (const n of C.data.nums){ if (Math.abs(n-f)<Math.abs(best-f)) best=n; } k=C.data.frames.get(best); }
  const r={X:k.r[0], Y:k.r[1], Z:k.r[2]}, o=C.rotOrder;
  // first letter is applied first, so it sits rightmost in the product
  const M=mul(rotAxis(o[2],r[o[2]]), mul(rotAxis(o[1],r[o[1]]), rotAxis(o[0],r[o[0]])));
  // Nuke writes the .chan vertical FOV against the camera's vertical aperture, but fits the horizontal
  // aperture to the plate width. So: vfov -> focal (via vaperture), then the real FOV from haperture and the plate aspect.
  const aspect=state.plate.w/state.plate.h;
  const focal = C.fovMode==='file' && k.vfov!==null ? C.vaperture/(2*Math.tan(k.vfov*DEG/2)) : C.focal;
  const tanH=C.haperture/(2*focal), tanV=tanH/aspect;
  return {pos:k.t, M, tanV, tanH, focal,
    right:[M[0],M[3],M[6]], up:[M[1],M[4],M[7]], back:[M[2],M[5],M[8]]};
}

/* world point -> plate px (y down) and camera depth (positive in front) */
export function project(P, cam){
  const d=[P[0]-cam.pos[0], P[1]-cam.pos[1], P[2]-cam.pos[2]], M=cam.M;
  const x=M[0]*d[0]+M[3]*d[1]+M[6]*d[2], y=M[1]*d[0]+M[4]*d[1]+M[7]*d[2], z=M[2]*d[0]+M[5]*d[1]+M[8]*d[2];
  const depth=-z; if (depth<=1e-6) return null;
  return [(x/depth/cam.tanH*0.5+0.5)*state.plate.w, (0.5-y/depth/cam.tanV*0.5)*state.plate.h, depth];
}

/* plate px -> world ray {o, d} (d unit length) */
export function rayThrough(px, py, cam){
  const nx=(px/state.plate.w-0.5)*2*cam.tanH, ny=(0.5-py/state.plate.h)*2*cam.tanV;
  const d=[0,1,2].map(i=>cam.right[i]*nx+cam.up[i]*ny-cam.back[i]);
  const L=Math.hypot(...d);
  return {o:cam.pos, d:d.map(v=>v/L)};
}

/* Where a click lands in the world: on the ground plane if the ray hits it in front of the camera, otherwise at `fallback` distance. */
export function placeInWorld(px, py, frame, fallback){
  const cam=camAt(frame), ray=rayThrough(px,py,cam), gy=state.cam.groundY;
  let t = Math.abs(ray.d[1])>1e-6 ? (gy-ray.o[1])/ray.d[1] : -1;
  if (!(t>0) || t>fallback*20) t=fallback;
  const P=ray.o.map((o,i)=>o+ray.d[i]*t);
  // world size of one plate height at that depth, so presets (fractions of plate height) keep their look
  return {P, wscale:2*t*cam.tanV};
}

/* world direction for a screen angle (0 = up, clockwise), in the camera plane at `frame` */
export function worldDir(angleDeg, frame){
  const cam=camAt(frame), a=angleDeg*DEG, s=Math.sin(a), c=Math.cos(a);
  return [0,1,2].map(i=>cam.right[i]*s+cam.up[i]*c);
}

export function loadCameraText(text, name){
  const data=parseChan(text);
  state.cam={name, text, data, rotOrder:'ZXY', fovMode:data.hasFov?'file':'lens', focal:35, haperture:24.576, vaperture:18.672, groundY:0,
    res:128, adaptive:true, multigrid:true, box:null};
  return data;
}
