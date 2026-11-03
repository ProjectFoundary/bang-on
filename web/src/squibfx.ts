// @ts-nocheck — drives Squib's (plain JS) 2D engine, vendored in ./squib (see squib/SQUIB_VERSION).
// Squib's live 2D fluid effects in the KeyFX mock-up: the explosion (fireball, smoke, flash, then a ground
// fire on the wreckage), the black smoke column and the grenade plume. Each KeyFX effect gets its own Squib sim, so it can be
// carried along its track; the effects-only layer (Squib's PNG element mode: straight sRGB + alpha) is drawn into the mock-up.
// Deterministic per frame (snapshots + fixed substeps), so previews, scrubbing and the uploaded AI keys all match.
// Smoke may start before frame 0: the sim simply begins earlier and is already going when the shot starts.
import { P, glCanvas, pass } from './squib/gl/context.js';
import { PRESETS } from './squib/presets.js';
import { freeTargets, makeTargets, renderLayers } from './squib/render/layers.js';
import { Sim } from './squib/sim/Sim.js';
import { app, state } from './squib/state.js';
import { colliderForSim, type ColliderSequence, type SimBounds } from './collider';
// The independent 3D path returns full plate-space elements. Callers draw these
// directly; routing them through trackedSquib's 2D translation would double-track.
export { setVolumeFXScene, volumeFXScene, renderVolumeElement, captureVolumeGuides, pruneVolumeFX, clearVolumeFX } from './volumeFX';
export type { VolumeFXScene, VolumeMatte, DepthReference, VolumeElementOptions, VolumeElementFrame } from './volumeFX';

export type SquibOpts = { w: number; h: number; n: number; x: number; y: number; start: number; end: number; size: number; seed: number;
                          wind: number; angle?: number; burnAt?: number; burnWidth?: number; color?: string; path?: Float32Array; bounds?: SimBounds };
// KeyFX variants of Squib presets, tuned for short (2-5 s) shots. Squib's are tuned for long element shots: its grenade plume
// grows over ~18 s and its black column burns at the base.
const clone = (o: any) => JSON.parse(JSON.stringify(o));
const black = clone(PRESETS.column);
black.label = 'KeyFX black smoke'; black.smoke.fire = null; black.defaults.color = '#2b2826';
black.smoke.amount = 0.45; black.smoke.ramp = 4; black.smoke.radius = 0.06; black.smoke.spread = 30;
Object.assign(black.defaults, { turb: 0.45, curl: 8, dissipate: 0.12, linger: 2.5 });
Object.assign(black.smoke.draft, { base: 3, cone: 0.32, wander: 1.6, temp: 0.07, rise: 0.075 });   // gentler draft: smoke stays dense at its base
PRESETS.kfx_black = black;
const white = clone(PRESETS.plume);
white.label = 'KeyFX white smoke'; white.defaults.color = '#d9dadb';
Object.assign(white.smoke.sheet, { initialAmount: 14, growthTime: 2.5, sourceGrowth: 3.5, growthPower: 1.5 });
// a grenade rolls out low and wide: weak upward jet and no warm draft, so it stalls and spreads instead of climbing
white.smoke.ramp = 2; white.smoke.radius = 0.07; white.smoke.speed = 0.06; white.smoke.push = 0.06; white.smoke.temp = 0.03; white.smoke.spread = 80;
white.smoke.draft = null;
Object.assign(white.smoke.sheet, { sourceTempCurve: [[0, 0.04], [6, 0.02]], initialAmount: 8, entrain: 0 });
Object.assign(white.defaults, { turb: 0.5, curl: 10, dissipate: 0.2, linger: 2 });
PRESETS.kfx_white = white;

export const SQUIB_KIND: Record<string, string> = { explosion: 'explosion', smoke_plume: 'kfx_black', smoke_white: 'kfx_white' };
const SCALE: Record<string, number> = { explosion: 1.35, kfx_black: 1.0, kfx_white: 1.4, groundfire: 1 };

let W = 0, H = 0;
let quality: 'draft' | 'high' = 'draft';
let scope = 'preview';
let collider: ColliderSequence | null = null;
export function setSquibCollider(value: ColliderSequence | null) { collider = value; }
export function setSquibQuality(value: 'draft' | 'high', owner = 'preview') { quality = value; scope = owner; }
const sims = new Map<string, { sim: any; key: string; matte?: any }>();
export let lastMs = 0;

function setup(w: number, h: number, n: number) {
  state.plate.last = n - 1;
  if (W === w && H === h) return;
  W = w; H = h;
  Object.assign(state.plate, { kind: 'keyfx', w, h, first: 0, last: n - 1 });
  state.fps = 24; state.scene.quality = 512;
  glCanvas.width = w; glCanvas.height = h;
  freeTargets(app.PT);
  app.PT = makeTargets(w, h, false);
}

/** A Squib instance (mirrors Squib's presetDefaults + createInstance). */
function instance(preset: string, id: number, o: SquibOpts, extra: Record<string, unknown> = {}) {
  const d = PRESETS[preset].defaults;
  return {
    scale: (d.scale ?? 1) * o.size * (SCALE[preset] ?? 1), intensity: 1, fineGrain: 1, gravityMul: 1, angle: d.angle ?? 0,
    debrisMul: d.debrisMul ?? 1, sparksMul: d.sparksMul ?? 1, heat: 1, color: o.color ?? d.color, flash: d.flash ?? true, floor: d.floor ?? false,
    duration: d.duration ?? 96, speedMul: 1, curl: d.curl ?? 18, turb: d.turb ?? 0.6, dissipate: d.dissipate ?? 0.3, linger: d.linger ?? 1,
    haze: 0.35, enabled: true, full: true, area: 0.8, sizeMul: 1, depth: 0.7, gust: 0.6, ridge: 20, width: d.width ?? 0.35, length: 0.9,
    spreadMul: 1, lift: 0, facing: d.facing ?? 0, id, preset, name: preset, x: Math.round(o.x), y: Math.round(o.y), frame: o.start,
    seed: (o.seed % 9000) + 1000 + id, ...extra,
  };
}

function instancesFor(kind: string, o: SquibOpts) {
  const id = (o.seed % 5000) + 1;
  if (kind === 'explosion') {
    // Keep the guide focused on fluid/fire; debris is a separate element.
    const out = [instance('explosion', id, o, { flash: true, debrisMul: 0, sparksMul: 0 })];
    if (o.burnAt != null && o.burnAt < o.end) // the wreckage keeps burning after the fireball
      // low, cooler flames on the wreckage: they must not feed the fireball (it lifts off on its own, as in Squib)
      out.push(instance('groundfire', id + 1, { ...o, y: o.y + 0.03 * o.h * o.size, size: o.size * 0.7 },
        { frame: o.burnAt, duration: o.end - o.burnAt + 8, width: o.burnWidth ?? 0.22, speedMul: 0.45, heat: 0.75, intensity: 0.8 }));
    return out;
  }
  // smoke: runs from its start (possibly before frame 0) to the end of the shot
  return [instance(kind, id, o, { duration: o.end - o.start + 8, angle: o.angle ?? 0 })];
}

/** Render one effect's Squib layer at frame f. Returns the WebGL canvas (valid until the next call). */
export function squibLayer(effectId: string, kind: string, o: SquibOpts, f: number) {
  const t0 = performance.now();
  setup(o.w, o.h, o.n);
  const grid = Math.round((quality === 'draft' ? 256 : 512) * (o.bounds?.width ?? 960) / 960);
  state.scene.quality = grid; state.scene.depth2d = quality === 'draft' ? 1 : 3;
  if (collider && collider.data.length !== o.n) throw new Error('Collider sequence does not match this shot.');
  const insts = instancesFor(kind, o), key = JSON.stringify([insts, o.wind, W, H, o.bounds, collider?.key ?? '', collider ? Array.from(o.path ?? []) : null]);
  const cacheId = `${scope}:${quality}:${effectId}`;
  let s = sims.get(cacheId);
  const gw = grid, gh = Math.max(16, Math.round(grid * H / W));
  if (s && (s.sim.gw !== gw || s.sim.gh !== gh)) { s.sim.destroy(); sims.delete(cacheId); s = undefined; }
  if (!s) {
    s = { sim: new Sim(gw, gh), key: '' }; sims.set(cacheId, s);
  }
  state.instances = insts; state.scene.wind = o.wind;
  if (s.key !== key) {
    const anchor = Math.max(0, Math.min(o.n - 1, Math.round(o.start)));
    s.matte = collider ? colliderForSim(collider, s.sim.gw, s.sim.gh, o.path, anchor, o.bounds) : null;
    s.sim.invalidateAll();
    for (const sheet of s.sim.sheets) sheet.obsKey = null;
    s.key = key;
  }
  state.matte = s.matte ?? null;
  state.scene.matteUse = 'collide';
  app.sim = s.sim;
  s.sim.goto(f);
  // Restoring an exact snapshot can skip step(), which normally uploads its mask.
  for (const sheet of s.sim.sheets) sheet.uploadObstacle(f);
  renderLayers(app.PT, f, false);
  const T = app.PT;
  pass(P.comp, null, W, H, { uS: T.S.tex, uF: T.F.tex, uDB: T.DB.tex, uDF: T.DF.tex, uK: T.K.tex, uN: T.N.tex, uPlate: T.S.tex,
    uObs: s.sim.obs, uTexel: s.sim.texel, uMode: { i: 2 }, uView: { i: 0 }, uHasPlate: 0, uShowObs: 0 });
  lastMs = performance.now() - t0;
  return glCanvas as HTMLCanvasElement;
}

/** Free sims for effects that no longer exist. */
export function pruneSquib(liveIds: Set<string>, owner = 'preview') {
  for (const [id, s] of sims) if (id.startsWith(owner+':') && !liveIds.has(id.split(':').slice(2).join(':'))) { s.sim.destroy(); sims.delete(id); }
}
