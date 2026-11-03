// Point tracking for blocking: follows the auto-track's feature tracks around a point (same rule as keyfx/track.py `follow`).
// Precision is not the goal: the video model does the real integration; this only keeps placed effects roughly on their spot.

export type TrackData = { n: number; w: number; h: number; q: number; camera: number[][]; tracks: number[][] };
export type Anchor = { frame: number; x: number; y: number }; // normalised 0..1

export class Tracker {
  n: number; w: number; h: number;
  private byFrame: { tid: number; x: number; y: number }[][];
  private start: Int32Array; private len: Int32Array; private xs: Float32Array[]; private ys: Float32Array[];
  private cam: number[][];
  private cache = new Map<string, Float32Array>();

  constructor(d: TrackData) {
    this.n = d.n; this.w = d.w; this.h = d.h; this.cam = d.camera;
    this.byFrame = Array.from({ length: d.n }, () => []);
    this.start = new Int32Array(d.tracks.length); this.len = new Int32Array(d.tracks.length);
    this.xs = []; this.ys = [];
    d.tracks.forEach((t, tid) => {
      const s = t[0], L = (t.length - 1) >> 1;
      const xs = new Float32Array(L), ys = new Float32Array(L);
      for (let k = 0; k < L; k++) {
        xs[k] = t[1 + 2 * k] / d.q; ys[k] = t[2 + 2 * k] / d.q;
        this.byFrame[s + k].push({ tid, x: xs[k], y: ys[k] });
      }
      this.start[tid] = s; this.len[tid] = L; this.xs.push(xs); this.ys.push(ys);
    });
  }

  private at(tid: number, f: number): [number, number] | null {
    const k = f - this.start[tid];
    return k >= 0 && k < this.len[tid] ? [this.xs[tid][k], this.ys[tid][k]] : null;
  }

  /** Camera similarity for frame f (frame 0 -> f) applied incrementally: where (x,y) at fa lands at fb. */
  private camStep(fa: number, fb: number, x: number, y: number): [number, number] {
    const [a1, b1, tx1, ty1] = this.cam[fa], [a2, b2, tx2, ty2] = this.cam[fb];
    // invert fa: p0 = R1^-1 (p - t1)
    const d = a1 * a1 + b1 * b1, px = x - tx1, py = y - ty1;
    const x0 = (a1 * px + b1 * py) / d, y0 = (-b1 * px + a1 * py) / d;
    return [a2 * x0 - b2 * y0 + tx2, b2 * x0 + a2 * y0 + ty2];
  }

  /** Relative camera zoom between frames (for sizing effects as the camera pushes in). */
  zoom(fa: number, fb: number) {
    const s = (f: number) => Math.hypot(this.cam[f][0], this.cam[f][1]);
    return s(fb) / s(fa);
  }

  private step(fa: number, fb: number, cx: number, cy: number): [number, number] {
    const cand: [number, number, number][] = [];
    for (const p of this.byFrame[fa]) {
      const q = this.at(p.tid, fb);
      if (!q) continue;
      cand.push([Math.hypot(p.x - cx, p.y - cy), q[0] - p.x, q[1] - p.y]);
    }
    cand.sort((a, b) => a[0] - b[0]);
    let near = cand.filter((c) => c[0] < 90);
    if (near.length < 3) near = cand.slice(0, 6).filter((c) => c[0] < 200);
    if (near.length >= 3) {
      const med = (arr: number[]) => { const s = [...arr].sort((a, b) => a - b); return s[s.length >> 1]; };
      const mx = med(near.map((c) => c[1])), my = med(near.map((c) => c[2]));
      const dev = near.map((c) => Math.hypot(c[1] - mx, c[2] - my));
      const lim = Math.max(2, 2.5 * med(dev));
      let sw = 0, sx = 0, sy = 0, cnt = 0;
      near.forEach((c, i) => { if (dev[i] < lim) { const w = 1 / (c[0] + 20); sw += w; sx += c[1] * w; sy += c[2] * w; cnt++; } });
      if (cnt >= 2) return [cx + sx / sw, cy + sy / sw];
    }
    return this.camStep(fa, fb, cx, cy);
  }

  /** Track one anchor through the shot: Float32Array [x0,y0,x1,y1,...] in pixels. */
  follow(a: Anchor, cameraOnly=false): Float32Array {
    const key = `${cameraOnly?'camera':'features'}|${a.frame}|${a.x.toFixed(4)}|${a.y.toFixed(4)}`;
    const hit = this.cache.get(key); if (hit) return hit;
    const out = new Float32Array(this.n * 2), f0 = Math.min(this.n - 1, Math.max(0, a.frame));
    out[f0 * 2] = a.x * this.w; out[f0 * 2 + 1] = a.y * this.h;
    for (let f = f0 + 1; f < this.n; f++) { const [x, y] = cameraOnly?this.camStep(f-1,f,out[(f-1)*2],out[(f-1)*2+1]):this.step(f - 1, f, out[(f - 1) * 2], out[(f - 1) * 2 + 1]); out[f * 2] = x; out[f * 2 + 1] = y; }
    for (let f = f0 - 1; f >= 0; f--) { const [x, y] = cameraOnly?this.camStep(f+1,f,out[(f+1)*2],out[(f+1)*2+1]):this.step(f + 1, f, out[(f + 1) * 2], out[(f + 1) * 2 + 1]); out[f * 2] = x; out[f * 2 + 1] = y; }
    if (this.cache.size > 400) this.cache.clear();
    this.cache.set(key, out);
    return out;
  }

  /** Path for an effect from its anchors: one anchor = its track; several = blend between neighbouring anchors' tracks. */
  path(anchors: Anchor[], cameraOnly=false): Float32Array {
    const as = [...anchors].sort((a, b) => a.frame - b.frame);
    if (as.length === 1) return this.follow(as[0],cameraOnly);
    const tr = as.map((a) => this.follow(a,cameraOnly)), out = new Float32Array(this.n * 2);
    for (let f = 0; f < this.n; f++) {
      let i = as.findIndex((a) => a.frame > f);
      let x: number, y: number;
      if (i === 0) { x = tr[0][f * 2]; y = tr[0][f * 2 + 1]; }
      else if (i < 0) { const L = tr.length - 1; x = tr[L][f * 2]; y = tr[L][f * 2 + 1]; }
      else {
        const a = as[i - 1], b = as[i], u = (f - a.frame) / Math.max(1, b.frame - a.frame), s = u * u * (3 - 2 * u);
        x = tr[i - 1][f * 2] * (1 - s) + tr[i][f * 2] * s; y = tr[i - 1][f * 2 + 1] * (1 - s) + tr[i][f * 2 + 1] * s;
      }
      out[f * 2] = x; out[f * 2 + 1] = y;
    }
    return out;
  }
}
