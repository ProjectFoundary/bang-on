"""Quick auto-track of a plate, run once when it loads.

Two things come out, both for blocking only (the video model does the real integration):
- camera motion: a similarity transform per frame (frame 0 -> frame f), from feature flow with RANSAC;
- feature tracks: a few hundred KLT tracks, reseeded as they die, so the browser can track any point the user drops an effect
  on by following the tracks around it (it falls back to the camera motion where there are none).
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from .config import CW, CH

TARGET = 320          # live tracks to keep
MIN_DIST = 14         # px between features
FB_MAX = 1.0          # forward-backward error limit (px)
LK = dict(winSize=(21, 21), maxLevel=3, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
Q = 4                 # positions stored in quarter pixels


def _flow(a: np.ndarray, b: np.ndarray, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Track pts from a to b with a forward-backward check. Returns (new points, ok mask)."""
    if len(pts) == 0:
        return pts, np.zeros(0, bool)
    p1, st1, _ = cv2.calcOpticalFlowPyrLK(a, b, pts, None, **LK)
    p0, st0, _ = cv2.calcOpticalFlowPyrLK(b, a, p1, None, **LK)
    fb = np.linalg.norm((p0 - pts).reshape(-1, 2), axis=1)
    h, w = a.shape
    inside = (p1[:, 0, 0] >= 0) & (p1[:, 0, 0] < w) & (p1[:, 0, 1] >= 0) & (p1[:, 0, 1] < h)
    ok = (st1[:, 0] == 1) & (st0[:, 0] == 1) & (fb < FB_MAX) & inside
    return p1, ok


def _detect(gray: np.ndarray, existing: np.ndarray, want: int) -> np.ndarray:
    mask = np.full(gray.shape, 255, np.uint8)
    for x, y in existing.reshape(-1, 2):
        cv2.circle(mask, (int(x), int(y)), MIN_DIST, 0, -1)
    pts = cv2.goodFeaturesToTrack(gray, maxCorners=max(want, 1), qualityLevel=0.01, minDistance=MIN_DIST, mask=mask, blockSize=7)
    return np.zeros((0, 1, 2), np.float32) if pts is None else pts.astype(np.float32)


def compute(frames_dir: Path, n: int) -> dict:
    if n < 1: raise ValueError('Tracking needs at least one source frame.')
    def read_gray(i):
        gray = cv2.imread(str(frames_dir / f'{i:05d}.png'), cv2.IMREAD_GRAYSCALE)
        if gray is None: raise ValueError(f'Tracking source frame {i} is missing.')
        return cv2.resize(gray, (CW, CH), interpolation=cv2.INTER_AREA) if gray.shape != (CH, CW) else gray
    # Keep only two reduced frames; native4K/high-FPS imports must not allocate
    # the entire decoded shot before resizing it to logical blocking pixels.
    a = read_gray(0)
    h, w = a.shape
    glob = [np.eye(3)]
    tracks: list[list] = []                 # [start, x0, y0, x1, y1, ...] in quarter px
    live_pts = _detect(a, np.zeros((0, 1, 2), np.float32), TARGET)
    live_ids = []
    for p in live_pts.reshape(-1, 2):
        tracks.append([0, *(np.round(p * Q).astype(int).tolist())]); live_ids.append(len(tracks) - 1)
    for i in range(1, n):
        b = read_gray(i)
        new, ok = _flow(a, b, live_pts)
        # camera motion from all good flow (RANSAC rejects the actors)
        M = None
        if ok.sum() >= 8:
            M, _inl = cv2.estimateAffinePartial2D(live_pts[ok], new[ok], method=cv2.RANSAC, ransacReprojThreshold=2.0)
        step = np.vstack([M, [0, 0, 1]]) if M is not None else np.eye(3)
        glob.append(step @ glob[-1])
        keep_pts, keep_ids = [], []
        for j, tid in enumerate(live_ids):
            if ok[j]:
                tracks[tid] += np.round(new[j, 0] * Q).astype(int).tolist()
                keep_pts.append(new[j]); keep_ids.append(tid)
        live_pts = np.array(keep_pts, np.float32).reshape(-1, 1, 2) if keep_pts else np.zeros((0, 1, 2), np.float32)
        live_ids = keep_ids
        if len(live_ids) < TARGET * 0.8:            # reseed empty areas
            fresh = _detect(b, live_pts, TARGET - len(live_ids))
            for p in fresh.reshape(-1, 2):
                tracks.append([i, *(np.round(p * Q).astype(int).tolist())]); live_ids.append(len(tracks) - 1)
            if len(fresh):
                live_pts = np.concatenate([live_pts, fresh], 0)
        a = b
    tracks = [t for t in tracks if (len(t) - 1) // 2 >= 3]
    sim = [[round(float(G[0, 0]), 6), round(float(G[1, 0]), 6), round(float(G[0, 2]), 3), round(float(G[1, 2]), 3)] for G in glob]
    return {'n': n, 'w': w, 'h': h, 'q': Q, 'camera': sim, 'tracks': tracks}


def path(pdir: Path, base: str) -> Path:
    return pdir / f'track_{base}.json'


def ensure(pdir: Path, frames_dir: Path, base: str, n: int) -> Path:
    f = path(pdir, base)
    if not f.exists():
        data = compute(frames_dir, n)
        tmp = f.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, separators=(',', ':')))
        tmp.replace(f)
    return f


def follow(data: dict, f0: int, x: float, y: float) -> np.ndarray:
    """Track a point (pixels at frame f0) through the shot with the stored tracks; same rule as the browser. Returns (n, 2) px."""
    n, q = data['n'], data['q']
    by_frame: list[list[tuple[int, float, float]]] = [[] for _ in range(n)]
    for tid, t in enumerate(data['tracks']):
        s = t[0]
        for k in range((len(t) - 1) // 2):
            by_frame[s + k].append((tid, t[1 + 2 * k] / q, t[2 + 2 * k] / q))
    pos = {tid: {} for tid in range(len(data['tracks']))}
    for f, pts in enumerate(by_frame):
        for tid, px, py in pts:
            pos[tid][f] = (px, py)
    cam = [np.array([[a, -b, tx], [b, a, ty], [0, 0, 1]]) for a, b, tx, ty in data['camera']]
    out = np.zeros((n, 2)); out[f0] = (x, y)

    def step(fa: int, fb: int, cx: float, cy: float) -> tuple[float, float]:
        cand = []
        for tid, px, py in by_frame[fa]:
            q2 = pos[tid].get(fb)
            if q2 is None:
                continue
            d = np.hypot(px - cx, py - cy)
            cand.append((d, q2[0] - px, q2[1] - py))
        cand.sort()
        near = [c for c in cand if c[0] < 90] or [c for c in cand[:6] if c[0] < 200]
        if len(near) >= 3:
            d = np.array(near)
            wts = 1 / (d[:, 0] + 20)
            mx, my = np.median(d[:, 1]), np.median(d[:, 2])
            good = np.hypot(d[:, 1] - mx, d[:, 2] - my) < max(2.0, 2.5 * np.median(np.hypot(d[:, 1] - mx, d[:, 2] - my)))
            if good.sum() >= 2:
                wg = wts[good]
                return cx + float((d[good, 1] * wg).sum() / wg.sum()), cy + float((d[good, 2] * wg).sum() / wg.sum())
        S = cam[fb] @ np.linalg.inv(cam[fa])
        v = S @ np.array([cx, cy, 1.0])
        return float(v[0]), float(v[1])

    for f in range(f0 + 1, n):
        out[f] = step(f - 1, f, *out[f - 1])
    for f in range(f0 - 1, -1, -1):
        out[f] = step(f + 1, f, *out[f + 1])
    return out
