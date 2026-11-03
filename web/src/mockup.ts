// Real-time mock-up of each effect: what the user blocks with, and (at the key frames) exactly what the AI is shown.
// Deterministic per frame (seeded by effect id), so scrubbing, playback and the uploaded keys always match.
// Two layers: `fx` (composited over the plate; its alpha is the area the AI polish may change) and `glow` (added light).
import type { Effect, Key, Phase, Preset } from './api';
import { image } from './effects';
import { squibLayer, setSquibQuality, setSquibCollider, pruneSquib } from './squibfx';
import type { ColliderSequence } from './collider';

export const W = 960, H = 540;
export type Layers = { fx: CanvasRenderingContext2D; glow: CanvasRenderingContext2D };
type P2 = [number, number];

// ---------------------------------------------------------------- helpers
const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
const smooth = (u: number) => { u = clamp01(u); return u * u * (3 - 2 * u); };
const lerp = (a: number, b: number, u: number) => a + (b - a) * u;
function hashStr(s: string) { let h = 2166136261; for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619); return h >>> 0; }
function rng(seed: number) {
  let a = seed >>> 0;
  return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
const wobble = (t: number, s: number) => 0.5 + 0.28 * Math.sin(t * 1.7 + s) + 0.22 * Math.sin(t * 3.1 + s * 1.9) * Math.sin(t * 0.7 + s * 2.3);
export const dirVec = (deg: number): P2 => [Math.sin((deg * Math.PI) / 180), -Math.cos((deg * Math.PI) / 180)];

function img(p: Preset, file: string, redraw: () => void) {
  const im = image(`/api/presets/${p.id}/${file}`, redraw);
  return im.complete && im.naturalHeight ? im : null;
}

const softCache = new Map<HTMLImageElement, HTMLCanvasElement>();
/** The image with its alpha faded to zero towards the edges (a radial falloff), so rotated puffs never show a straight edge. */
function soft(im: HTMLImageElement | null): HTMLCanvasElement | null {
  if (!im) return null;
  let c = softCache.get(im);
  if (!c) {
    c = document.createElement('canvas'); c.width = im.naturalWidth; c.height = im.naturalHeight;
    const g = c.getContext('2d')!; g.drawImage(im, 0, 0);
    const r = Math.min(c.width, c.height) / 2, gr = g.createRadialGradient(c.width / 2, c.height / 2, r * 0.35, c.width / 2, c.height / 2, r);
    gr.addColorStop(0, 'rgba(0,0,0,1)'); gr.addColorStop(1, 'rgba(0,0,0,0)');
    g.globalCompositeOperation = 'destination-in'; g.fillStyle = gr; g.fillRect(0, 0, c.width, c.height);
    softCache.set(im, c);
  }
  return c;
}

function stamp(g: CanvasRenderingContext2D, im: HTMLImageElement | HTMLCanvasElement | null, x: number, y: number, h: number, anchor: P2,
               o: { rot?: number; alpha?: number; flipX?: boolean; shear?: number; sx?: number; sy?: number } = {}) {
  if (!im || h < 1) return;
  const a = clamp01(o.alpha ?? 1); if (a <= 0.003) return;
  const iw = im instanceof HTMLImageElement ? im.naturalWidth : im.width, ih = im instanceof HTMLImageElement ? im.naturalHeight : im.height;
  const hh = h * (o.sy ?? 1), w = h * (iw / ih) * (o.sx ?? 1);
  g.save(); g.translate(x, y);
  if (o.shear) g.transform(1, 0, o.shear, 1, 0, 0);
  if (o.rot) g.rotate(o.rot);
  if (o.flipX) g.scale(-1, 1);
  g.globalAlpha = a; g.drawImage(im, -anchor[0] * w, -anchor[1] * hh, w, hh); g.restore();
}

function glow(g: CanvasRenderingContext2D, x: number, y: number, R: number, s: number, rgb = '255,170,95') {
  if (s <= 0.01 || R < 1) return;
  const gr = g.createRadialGradient(x, y, 0, x, y, R * 2);
  [0, 0.15, 0.3, 0.5, 0.75, 1].forEach((t) => gr.addColorStop(t, `rgba(${rgb},${(Math.exp(-((t * 2) ** 2)) * s).toFixed(3)})`));
  g.save(); g.globalCompositeOperation = 'lighter'; g.fillStyle = gr; g.fillRect(x - R * 2, y - R * 2, R * 4, R * 4); g.restore();
}

function hole(g: CanvasRenderingContext2D, x: number, y: number, R: number, seed: number) {
  const r = rng(seed);
  g.save(); g.translate(x, y);
  const gr = g.createRadialGradient(0, 0, 0, 0, 0, R * 1.4);
  gr.addColorStop(0, 'rgba(12,10,8,0.9)'); gr.addColorStop(0.45, 'rgba(25,20,16,0.7)'); gr.addColorStop(0.7, 'rgba(150,140,130,0.25)'); gr.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = gr; g.beginPath(); g.arc(0, 0, R * 1.4, 0, Math.PI * 2); g.fill();
  g.strokeStyle = 'rgba(20,16,12,0.55)'; g.lineWidth = 1;
  for (let i = 0; i < 4; i++) { const a = r() * Math.PI * 2, L = R * (1.6 + r() * 1.6); g.beginPath(); g.moveTo(Math.cos(a) * R * 0.8, Math.sin(a) * R * 0.8); g.lineTo(Math.cos(a) * L, Math.sin(a) * L); g.stroke(); }
  g.restore();
}

// ---------------------------------------------------------------- phases
type PK = { k: Key; ph: Phase };
const phaseOf = (p: Preset, name: string) => p.phases.find((x) => x.phase === name)!;

/** Keys in time order, with runs of the same phase (repeat keys) collapsed into one. */
function phases(e: Effect, p: Preset): PK[] {
  const ks = [...e.keys].filter((k) => k.enabled !== false).sort((a, b) => a.frame - b.frame);
  const out: PK[] = [];
  for (const k of ks) { const ph = phaseOf(p, k.phase); if (!ph) continue; if (out.length && out[out.length - 1].k.phase === k.phase) continue; out.push({ k, ph }); }
  return out;
}
const frameOf = (ps: PK[], name: string, fb = 0) => ps.find((x) => x.k.phase === name)?.k.frame ?? fb;
export function onsetOf(e: Effect, p: Preset) { const ps = phases(e, p); return ps.find((x) => x.ph.stamp)?.k.frame ?? ps[0]?.k.frame ?? 0; }
export function endOf(e: Effect, p: Preset, n: number) {
  const ps = phases(e, p); const last = ps[ps.length - 1];
  return last?.ph.persist ? n - 1 : last?.k.frame ?? 0;
}

/** Cross-fade weights of the phase stamps at frame f (exactly one phase at full weight on its key frame). */
function weights(ps: PK[], f: number): { pk: PK; w: number; age: number; u: number }[] {
  let i = -1; for (let j = 0; j < ps.length; j++) if (ps[j].k.frame <= f) i = j;
  if (i < 0) return [];
  const cur = ps[i], nxt = ps[i + 1], age = f - cur.k.frame;
  const out: { pk: PK; w: number; age: number; u: number }[] = [];
  if (!cur.ph.stamp) return out;
  if (!nxt) { if (cur.ph.persist || age === 0) out.push({ pk: cur, w: 1, age, u: 0 }); return out; }
  const u = (f - cur.k.frame) / Math.max(1, nxt.k.frame - cur.k.frame);
  if (cur.ph.hard) { if (age === 0) out.push({ pk: cur, w: 1, age, u }); }
  else out.push({ pk: cur, w: 1 - smooth(u), age, u });
  if (nxt.ph.stamp && age > 0) out.push({ pk: nxt, w: smooth(u), age: f - nxt.k.frame, u });
  return out;
}

// ---------------------------------------------------------------- particle systems
type PuffOpts = {
  seed: number; start: number; end?: number; rate: number; life: number;
  rise: number; tau: number;                 // px/frame initial rise, decay (frames)
  wind: P2; windRamp: number;                // px/frame drift
  spread?: number; spreadTau?: number; flat?: number;   // radial push (px/frame), flattening of its vertical part
  size0: (birth: number) => number; grow: number; alpha: number; jitter: number;
  src: P2; im: HTMLImageElement | HTMLCanvasElement | null; burst?: { frames: number; rate: number };
};
function puffs(g: CanvasRenderingContext2D, f: number, o: PuffOpts) {
  if (!o.im || f < o.start) return;
  if (o.im instanceof HTMLImageElement) o = { ...o, im: soft(o.im) };
  const births: number[] = [];
  if (o.burst) for (let i = 0; i < o.burst.frames * o.burst.rate; i++) births.push(o.start + i / o.burst.rate);
  const s2 = o.start + (o.burst?.frames ?? 0);
  const i0 = Math.max(0, Math.floor((f - o.life - s2) * o.rate)), i1 = Math.floor((f - s2) * o.rate);
  for (let i = i0; i <= i1; i++) births.push(s2 + i / o.rate);
  births.forEach((b, idx) => {
    if (o.end != null && b > o.end) return;
    const age = f - b; if (age < 0 || age > o.life) return;
    const r = rng(o.seed + Math.round(b * 64) * 7919 + idx * 13);
    const r1 = r(), r2 = r(), r3 = r(), r4 = r(), r5 = r();
    const riseD = o.rise * (0.8 + 0.4 * r4) * o.tau * (1 - Math.exp(-age / o.tau));
    const wr = age * Math.min(1, age / o.windRamp);
    let x = o.src[0] + o.wind[0] * wr * (0.85 + 0.3 * r5) + (r2 - 0.5) * o.jitter * (1 + age * 0.04);
    let y = o.src[1] - riseD + o.wind[1] * wr * (0.85 + 0.3 * r5);
    if (o.spread) {
      const sa = r1 * Math.PI * 2, sp = o.spread * (0.4 + 0.6 * r3) * (o.spreadTau ?? 10) * (1 - Math.exp(-age / (o.spreadTau ?? 10)));
      x += Math.cos(sa) * sp; y += Math.sin(sa) * sp * (o.flat ?? 1);
    }
    const size = o.size0(b) * (0.8 + 0.4 * r3) + o.grow * age;
    const a = o.alpha * smooth(age / 3) * Math.pow(1 - age / o.life, 1.3);
    stamp(g, o.im, x, y, size, [0.5, 0.5], { rot: r1 * Math.PI * 2 + age * 0.012 * (r4 - 0.5), alpha: a });
  });
}

type PartOpts = { seed: number; birth: number; n: number; origin: P2; angle: number; cone: number; vmin: number; vmax: number; grav: number; life: number };
function particles(f: number, o: PartOpts, draw: (x: number, y: number, vx: number, vy: number, k: number, r: () => number) => void) {
  const age = f - o.birth; if (age < 0 || age > o.life) return;
  for (let i = 0; i < o.n; i++) {
    const r = rng(o.seed + i * 104729);
    const a = o.angle + (r() - 0.5) * o.cone, v = lerp(o.vmin, o.vmax, r()), life = o.life * (0.6 + 0.4 * r());
    if (age > life) continue;
    const vx = Math.sin(a) * v, vy = -Math.cos(a) * v;
    draw(o.origin[0] + vx * age, o.origin[1] + vy * age + 0.5 * o.grav * age * age, vx, vy + o.grav * age, age / life, r);
  }
}
function chip(g: CanvasRenderingContext2D, x: number, y: number, s: number, rot: number, shade: number, alpha: number) {
  g.save(); g.translate(x, y); g.rotate(rot); g.globalAlpha = clamp01(alpha); g.fillStyle = `rgb(${shade},${Math.round(shade * 0.92)},${Math.round(shade * 0.85)})`;
  g.beginPath(); g.moveTo(-s, -s * 0.3); g.lineTo(-s * 0.2, -s * 0.9); g.lineTo(s * 0.9, -s * 0.4); g.lineTo(s * 0.5, s * 0.7); g.lineTo(-s * 0.6, s * 0.5); g.closePath(); g.fill(); g.restore();
}
function spark(g: CanvasRenderingContext2D, x: number, y: number, vx: number, vy: number, k: number) {
  g.save(); g.globalCompositeOperation = 'lighter'; g.strokeStyle = `rgba(255,${Math.round(210 - 80 * k)},${Math.round(130 - 90 * k)},${(1 - k).toFixed(2)})`;
  g.lineWidth = 1.6; g.beginPath(); g.moveTo(x, y); g.lineTo(x - vx * 1.6, y - vy * 1.6); g.stroke(); g.restore();
}

// ---------------------------------------------------------------- per-effect renderers
type C = { L: Layers; e: Effect; p: Preset; f: number; n: number; path: Float32Array; at: (f: number) => P2; size: number; seed: number;
           ang: number; str: number; dir: P2; redraw: () => void };

function phaseStamps(c: C, ps: PK[], mod?: (pk: PK, age: number, u: number) => { sx?: number; sy?: number; dy?: number; rot?: number; shear?: number; alpha?: number }) {
  const [x, y] = c.at(c.f);
  for (const { pk, w, age, u } of weights(ps, c.f)) {
    const ph = pk.ph; if (!ph.stamp || ph.stamp === 'hole') continue;
    const h = (ph.scale ?? 0.3) * H * c.size, m = mod?.(pk, age, u) ?? {};
    if (ph.glow) glow(c.L.glow, x, y - h * 0.2, (ph.glow_radius ?? 0.8) * h, ph.glow * w);
    stamp(c.L.fx, img(c.p, ph.stamp, c.redraw), x, y + (m.dy ?? 0), h, (ph.anchor ?? [0.5, 0.5]) as P2,
          { alpha: w * (ph.opacity ?? 1) * (m.alpha ?? 1), sx: m.sx, sy: m.sy, rot: m.rot, shear: m.shear });
  }
}

function windPx(c: C, speed: number): P2 { return [c.dir[0] * c.str * speed, c.dir[1] * c.str * speed]; }

/** Simulate every part of the world that the moving camera can reveal, without a visible canvas edge. */
function trackedSquib(c: C, kind: string, start: number, extra: { wind: number; burnAt?: number; color?: string }) {
  const anchor = Math.max(0, Math.min(c.n - 1, start)), [x0, y0] = c.at(anchor), [x, y] = c.at(c.f);
  let left = 0, top = 0, right = W, bottom = H;
  for (let f = 0; f < c.n; f++) {
    const [xx, yy] = c.at(f), dx = xx - x0, dy = yy - y0;
    left = Math.min(left, -dx); top = Math.min(top, -dy);
    right = Math.max(right, W - dx); bottom = Math.max(bottom, H - dy);
  }
  left = Math.floor(left); top = Math.floor(top); right = Math.ceil(right); bottom = Math.ceil(bottom);
  const bounds = { x: left, y: top, width: right - left, height: bottom - top }, raster = c.L.fx.canvas.width / W;
  const sq = squibLayer(c.e.id, kind, { w: bounds.width * raster, h: bounds.height * raster, n: c.n,
    x: (x0-left)*raster, y: (y0-top)*raster, start, end: c.n-1,
    size: c.size*H/bounds.height, seed: c.seed, path: c.path, bounds, ...extra }, c.f);
  c.L.fx.drawImage(sq, x-x0+left, y-y0+top, bounds.width, bounds.height);
}

function explosion(c: C) {
  const ps = phases(c.e, c.p), on = frameOf(ps, 'onset'), pk = frameOf(ps, 'peak', on + 6), rl = frameOf(ps, 'rolling', pk + 6);
  if (c.f < on - 1) return;
  const [x, y] = c.at(c.f), S = H * c.size;
  // the light the blast throws on the scene (KeyFX light layer)
  glow(c.L.glow, x, y - S * 0.15, S * (0.3 + 0.012 * Math.min(30, c.f - on)),
       c.f < on ? 0 : c.f < pk ? 0.95 : 0.95 * Math.exp(-(c.f - pk) / 6) + 0.25 * (c.f >= rl ? 1 : 0) * (0.8 + 0.4 * wobble(c.f * 0.5, 3)));
  // Squib's animated 2D explosion (fluid-sim fireball, smoke, debris, sparks, flash) and the wreckage burning after it
  trackedSquib(c, 'explosion', on, { wind: c.str*c.dir[0]*.3, burnAt: rl+6 });
}

function fire(c: C) {
  const ps = phases(c.e, c.p), ig = frameOf(ps, 'ignite'), br = frameOf(ps, 'burning', ig + 12);
  if (c.f < ig) return;
  const [x, y] = c.at(c.f), u = smooth((c.f - ig) / Math.max(1, br - ig));
  const h = H * c.size * lerp(phaseOf(c.p, 'ignite')?.scale ?? 0.1, phaseOf(c.p, 'burning')?.scale ?? 0.4, u);
  const lean = -c.str * c.dir[0] * 0.9, im = img(c.p, 'fire.png', c.redraw);
  puffs(c.L.fx, c.f, { seed: c.seed + 3, start: ig + 4, rate: 0.45, life: 80, rise: H * 0.0045 * c.size, tau: 70, wind: windPx(c, H * 0.011), windRamp: 14,
    spread: H * 0.0012 * c.size, spreadTau: 40, flat: 0.6, size0: () => h * 0.4, grow: h * 0.018, alpha: 0.45 * u, jitter: h * 0.15, src: [x - lean * h * 0.85, y - h * 0.85], im: img(c.p, '../smoke_plume/puff.png', c.redraw) });
  glow(c.L.glow, x - lean * h * 0.3, y - h * 0.35, h * 0.75, (0.35 + 0.2 * u) * (0.75 + 0.5 * wobble(c.f * 0.9, 2)));
  for (let j = 0; j < 3; j++) {
    const wv = wobble(c.f * (0.7 + j * 0.15), j * 5.1);
    stamp(c.L.fx, im, x + (j - 1) * h * 0.16, y, h * (0.72 + 0.28 * wv) * (j === 1 ? 1 : 0.8), [0.5, 0.97],
          { alpha: 0.55 + 0.45 * wv, flipX: (Math.floor(c.f / 3) + j) % 2 === 1, shear: lean, sx: 0.9 + 0.2 * wobble(c.f * 1.1, j) });
  }
}

/** Squib smoke (fluid sim): slow, natural rise and spread; wind bends it; may start before frame 0. */
function squibSmoke(c: C, kind: string, color?: string) {
  const ps = phases(c.e, c.p), st = ps.find((q) => q.ph.stamp)?.k.frame ?? 0;
  if (c.f < st) return;
  trackedSquib(c, kind, st, { wind: c.str*c.dir[0]*.28, color });
}
function smokePlume(c: C) { squibSmoke(c, 'kfx_black'); }
function smokeWhite(c: C) {
  const st = phases(c.e, c.p).find((q) => q.ph.stamp)?.k.frame ?? 0, [x, y] = c.at(c.f), S = H * c.size;
  if (c.f >= st && c.f - st < 4) glow(c.L.glow, x, y, S * 0.08, 0.5 * (1 - (c.f - st) / 4), '255,200,190');   // grenade pop
  squibSmoke(c, 'kfx_white');
}

function muzzle(c: C, side: boolean) {
  const ps = phases(c.e, c.p), fl = frameOf(ps, 'flash');
  if (c.f < fl) return;
  const [x, y] = c.at(c.f), ph = phaseOf(c.p, 'flash'), h = (ph.scale ?? 0.08) * H * c.size;
  const rot = side ? ((c.ang - 90) * Math.PI) / 180 : 0;
  if (c.f === fl) {
    glow(c.L.glow, x + c.dir[0] * h * (side ? 1.2 : 0), y + c.dir[1] * h * (side ? 1.2 : 0), h * (ph.glow_radius ?? 1), ph.glow ?? 0.6);
    stamp(c.L.fx, img(c.p, 'flash.png', c.redraw), x, y, h, (ph.anchor ?? [0.5, 0.5]) as P2, { rot });
  }
  const sm = img(c.p, 'smoke.png', c.redraw), d = side ? c.dir : ([0, -1] as P2);
  puffs(c.L.fx, c.f, { seed: c.seed, start: fl + 1, end: fl + 3, rate: 1.5, life: 16, rise: H * 0.0015, tau: 20, wind: [d[0] * H * 0.004, d[1] * H * 0.004], windRamp: 1,
    size0: () => h * 0.5, grow: h * 0.06, alpha: 0.4, jitter: h * 0.2, src: [x + d[0] * h * 0.8, y + d[1] * h * 0.8], im: sm });
}

function sparks(c: C) {
  const ps = phases(c.e, c.p), bu = frameOf(ps, 'burst');
  if (c.f < bu) return;
  const [x, y] = c.at(c.f), S = H * c.size;
  phaseStamps(c, ps);
  particles(c.f, { seed: c.seed, birth: bu, n: 34, origin: [x, y], angle: (c.ang * Math.PI) / 180, cone: lerp(3.0, 0.7, c.str), vmin: S * 0.012, vmax: S * 0.034, grav: S * 0.0016, life: 11 },
    (px, py, vx, vy, k) => spark(c.L.glow, px, py, vx, vy, k));
}

function hit(c: C, ground: boolean) {
  const ps = phases(c.e, c.p), im = frameOf(ps, 'impact');
  if (c.f < im) return;
  const [x, y] = c.at(c.f), S = H * c.size, a = (c.ang * Math.PI) / 180;
  if (!ground && c.f >= im) hole(c.L.fx, x, y, S * 0.012, c.seed);
  phaseStamps(c, ps, (q) => (ground && q.k.phase === 'impact' ? { rot: a * 0.6 } : {}));
  particles(c.f, { seed: c.seed + 9, birth: im, n: 14, origin: [x, y], angle: a, cone: lerp(3.0, 0.8, c.str), vmin: S * 0.006, vmax: S * 0.02, grav: S * 0.0012, life: 14 },
    (px, py, _vx, _vy, k, r) => chip(c.L.fx, px, py, S * (0.003 + 0.005 * r()), r() * 6 + k * 3, 90 + 80 * r(), 1 - k));
}

/** Draw one effect at frame f into the two layers. `path` is its tracked position per frame (pixels). */
export function drawEffect(L: Layers, e: Effect, p: Preset, f: number, n: number, path: Float32Array, redraw: () => void) {
  const at = (ff: number): P2 => { const i = Math.min(n - 1, Math.max(0, Math.round(ff))); return [path[i * 2], path[i * 2 + 1]]; };
  const d = p.direction ?? { kind: 'none', angle: 0, strength: 0 };
  const ang = e.angle ?? d.angle ?? 0, str = e.strength ?? d.strength ?? 0;
  const c: C = { L, e, p, f, n, path, at, size: e.size ?? 1, seed: hashStr(e.id), ang, str, dir: dirVec(ang), redraw };
  switch (p.id) {
    case 'explosion': return explosion(c);
    case 'fire': return fire(c);
    case 'smoke_plume': return smokePlume(c);
    case 'smoke_white': return smokeWhite(c);
    case 'muzzle_side': return muzzle(c, true);
    case 'muzzle_front': return muzzle(c, false);
    case 'sparks': return sparks(c);
    case 'wall_hit': return hit(c, false);
    case 'dirt_hit': return hit(c, true);
    default: return phaseStamps(c, phases(e, p));
  }
}

/** Render all effects at frame f into fresh layer canvases (for display or for upload as keys). */
export function renderLayers(fxC: HTMLCanvasElement, glowC: HTMLCanvasElement, effects: Effect[], presets: Record<string, Preset>,
                             f: number, n: number, paths: Map<string, Float32Array>, redraw: () => void, quality: 'draft' | 'high' = 'draft', owner='preview', collider: ColliderSequence | null = null) {
  setSquibQuality(quality,owner);
  setSquibCollider(collider);
  pruneSquib(new Set(effects.map(e => e.id)),owner);
  const fx = fxC.getContext('2d')!, gl = glowC.getContext('2d')!;
  // UI coordinates remain logical pixels; generation canvases rasterize at native resolution.
  fx.setTransform(fxC.width / W, 0, 0, fxC.height / H, 0, 0);
  gl.setTransform(glowC.width / W, 0, 0, glowC.height / H, 0, 0);
  fx.clearRect(0, 0, W, H); gl.clearRect(0, 0, W, H);
  for (const e of effects) {
    const p = presets[e.preset], path = paths.get(e.id);
    if (p && path) drawEffect({ fx, glow: gl }, e, p, f, n, path, redraw);
  }
}
