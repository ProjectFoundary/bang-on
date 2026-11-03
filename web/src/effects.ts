// Shared helpers: effect colours, phase lookup, key frames (mirrors keyfx/keys.py key_frames) and an image cache.
import type { Effect, Phase, Preset } from './api';

export const FPS = 24;
export const COLORS: Record<string, string> = {
  explosion: '#ff8a3d', fire: '#ffb547', muzzle_side: '#ffe27a', muzzle_front: '#ffe27a', wall_hit: '#c9c2b8',
  dirt_hit: '#c79a6a', sparks: '#ffcc66', smoke_plume: '#8f9aa3', smoke_white: '#e2e6ea', smoke_plume_hq: '#969696', smoke_grenade: '#d9dadb',
};

export const phaseOf = (p: Preset, name: string): Phase => p.phases.find((x) => x.phase === name)!;

/** Already going at frame 0 (started before the shot): a stamped key before 0 and still active at 0. */
export function prerolled(e: Effect, p: Preset) {
  const ks = [...e.keys].filter((k) => k.enabled !== false).sort((a, b) => a.frame - b.frame);
  const past = ks.filter((k) => k.frame <= 0), k = past[past.length - 1];
  if (!k || !ks.some((q) => q.frame < 0 && phaseOf(p, q.phase)?.stamp)) return false;
  const ph = phaseOf(p, k.phase);
  return !!ph?.stamp && (k !== ks[ks.length - 1] || k.frame === 0 || !!ph.persist);
}

export const REPEAT = 16, PREROLL_MAX = 240;

/** Frames the AI is shown (mirrors keyfx/keys.py key_frames): keys inside the shot, plus frame 0 for pre-rolled effects. */
export function keyFrames(effects: Effect[], presets: Record<string, Preset>, n: number, guideMode: 'sparse' | 'full' = 'sparse') {
  const m = new Map<number, boolean>();
  for (const e of effects) {
    const p = presets[e.preset]; if (!p) continue;
    for (const k of e.keys) {
      if (guideMode === 'sparse' && p.guide_phases && !p.guide_phases.includes(k.phase)) continue;
      if (k.enabled === false || k.frame < 0 || k.frame > n - 1) continue;
      const hard = !!phaseOf(p, k.phase)?.hard;
      m.set(k.frame, (m.get(k.frame) ?? false) || hard);
    }
    if (prerolled(e, p) && !m.has(0)) m.set(0, false);
  }
  return [...m.entries()].sort((a, b) => a[0] - b[0]).map(([frame, hard]) => ({ frame, hard }));
}

/** After a retime: rebuild the repeat keys of a lasting effect (every REPEAT frames to the last frame), like presets.new_effect. */
export function respace(e: Effect, p: Preset, n: number) {
  const last = p.phases[p.phases.length - 1];
  if (!last?.persist || !last.stamp) return;
  const ks = [...e.keys].sort((a, b) => a.frame - b.frame);
  const firstLast = ks.find((k) => k.phase === last.phase); if (!firstLast) return;
  const out = ks.filter((k) => k.phase !== last.phase || k === firstLast), end = n - 1;
  let f = Math.max(firstLast.frame + REPEAT, 0), prev = firstLast.frame;
  while (f < end - REPEAT / 3) { out.push({ ...firstLast, frame: f }); prev = f; f += REPEAT; }
  if (end - Math.max(prev, 0) > REPEAT / 2 || prev < 0) out.push({ ...firstLast, frame: end });
  e.keys = out;
}

export const secs = (f: number) => (f / FPS).toFixed(2) + 's';

const imgCache = new Map<string, HTMLImageElement>();
export function image(url: string, onload?: () => void): HTMLImageElement {
  let im = imgCache.get(url);
  if (!im) {
    im = new Image(); im.src = url; imgCache.set(url, im);
  }
  if (onload && !im.complete) im.addEventListener('load', onload, { once: true });
  return im;
}
