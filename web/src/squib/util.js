
export const $ = s => document.querySelector(s);
export const DEG = Math.PI/180;
export const SUBSTEPS = 2, JACOBI = 26, SNAP_EVERY = 4;
export const FLOW_PERIOD3 = 2.0;   // slower material-coordinate renewal for stable volume detail
export const FLOW_PERIOD = 0.5;   // s: how long 2D render detail rides the flow before it's renewed (see the flow pass)

/* ---------- colour helpers ---------- */
export function hexToRgb(h){ h=h.replace('#',''); return [0,2,4].map(i=>parseInt(h.substr(i,2),16)/255); }
export function s2l(c){ return c<=0.04045 ? c/12.92 : Math.pow((c+0.055)/1.055,2.4); }
export function lin(hex){ return hexToRgb(hex).map(s2l); }

/* ---------- deterministic RNG ---------- */
export function rng(seed){ let s=seed>>>0; return ()=>{ s=(s+0x6D2B79F5)|0; let t=Math.imul(s^(s>>>15),1|s); t=(t+Math.imul(t^(t>>>7),61|t))^t; return ((t^(t>>>14))>>>0)/4294967296; }; }
export function gauss(r){ const u=Math.max(r(),1e-7), v=r(); return Math.sqrt(-2*Math.log(u))*Math.cos(6.2831853*v); }
export function seedMix(a,b,c){ return (Math.imul(a|0,0x9E3779B1) ^ Math.imul((b|0)+0x632BE5AB,0x85EBCA77) ^ Math.imul(c|0,0xC2B2AE3D))>>>0; }
