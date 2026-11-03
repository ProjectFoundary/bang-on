"""Plate import, structure video, frame I/O and result post-processing (ffmpeg + OpenCV)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

from .config import CH, CW, FPS, H, MAX_FRAMES, PAD, RENDER_CH, RENDER_CW, RENDER_H, RENDER_PAD, RENDER_W, W, render_length


def probe(path: Path) -> dict:
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=width,height,r_frame_rate,nb_frames,duration:format=duration', '-of', 'json', str(path)],
                       capture_output=True, text=True, check=True)
    data = json.loads(r.stdout)
    s = data['streams'][0]
    num, den = (s.get('r_frame_rate') or '24/1').split('/')
    return {'width': int(s['width']), 'height': int(s['height']), 'fps': float(num) / float(den or 1),
            'duration': float(s.get('duration') if s.get('duration') not in (None, 'N/A') else data.get('format', {}).get('duration') or 0),
            'frames': int(s.get('nb_frames')) if str(s.get('nb_frames', '')).isdigit() else 0}


def ingest(src: Path, frames_dir: Path, start_s: float = 0.0, max_frames: int = MAX_FRAMES) -> int:
    """Resample the plate to 24 fps (nearest frame in time) and fit it into native 1920x1080. Returns the number of frames."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    for f in frames_dir.glob('*.png'):
        f.unlink()
    vf = (f'fps={FPS},scale={RENDER_CW}:{RENDER_CH}:force_original_aspect_ratio=decrease:flags=lanczos,'
          f'pad={RENDER_CW}:{RENDER_CH}:(ow-iw)/2:(oh-ih)/2:black')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{start_s:.3f}', '-i', str(src), '-vf', vf, '-frames:v', str(max_frames),
                    '-pix_fmt', 'rgb24', '-start_number', '0', str(frames_dir / '%05d.png')], check=True)
    return len(list(frames_dir.glob('*.png')))


def frame_path(frames_dir: Path, i: int) -> Path:
    return frames_dir / f'{i:05d}.png'


def read_frame(frames_dir: Path, i: int, *, native: bool = True) -> np.ndarray:
    """BGR float32 content at stored resolution. Explicit native=False is display-only."""
    n = len(list(frames_dir.glob('*.png')))
    im = cv2.imread(str(frame_path(frames_dir, min(max(i, 0), n - 1))))
    if im is None:
        raise ValueError('The requested video frame is missing or unreadable.')
    if not native and im.shape[:2] != (CH, CW):
        im = cv2.resize(im, (CW, CH), interpolation=cv2.INTER_AREA)
    return im.astype(np.float32)


def pad(content: np.ndarray) -> np.ndarray:
    """Add model bars to a working or native content frame, preserving its resolution."""
    if content.shape[:2] == (CH, CW):
        border = PAD
    elif content.shape[:2] == (RENDER_CH, RENDER_CW):
        border = RENDER_PAD
    else:
        raise ValueError(f'Unsupported content frame size: {content.shape[1]}x{content.shape[0]}')
    return cv2.copyMakeBorder(np.clip(content + .5, 0, 255).astype(np.uint8), border, border, 0, 0, cv2.BORDER_CONSTANT, value=0)


def unpad(frame: np.ndarray) -> np.ndarray:
    """Remove known model bars without downsampling native output or legacy video."""
    h, w = frame.shape[:2]
    if (w, h) == (W, H):
        return frame[PAD:PAD + CH]
    if (w, h) == (RENDER_W, RENDER_H):
        return frame[RENDER_PAD:RENDER_PAD + RENDER_CH]
    if (w, h) in ((CW, CH), (RENDER_CW, RENDER_CH)):
        return frame
    raise ValueError(f'Unexpected video canvas {w}x{h}; expected working or native Bang On video.')


def _writer(path: Path, w: int, h: int, lossless: bool, fps: int = FPS, *, crf: int = 16):
    enc = ['-c:v', 'libx264', '-qp', '0', '-pix_fmt', 'yuv444p'] if lossless else ['-c:v', 'libx264', '-crf', str(crf), '-preset', 'medium', '-pix_fmt', 'yuv420p']
    return subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{w}x{h}', '-r', str(fps), '-i', '-',
                             '-an', *enc, '-movflags', '+faststart', str(path)], stdin=subprocess.PIPE)


def structure_video(frames_dir: Path, n: int, out: Path) -> Path:
    """Pad to the minimal frame grid, retaining native source-stage detail when available."""
    first = pad(read_frame(frames_dir, 0, native=True))
    w = _writer(out, first.shape[1], first.shape[0], lossless=True)
    for i in range(render_length(n)):
        w.stdin.write(pad(read_frame(frames_dir, min(i, n - 1), native=True)).tobytes())
    w.stdin.close()
    if w.wait() != 0:
        raise RuntimeError('ffmpeg failed writing the structure video')
    return out


def content_video(frames_dir: Path, n: int, out: Path) -> Path:
    """Exact source frames for segmentation/analysis; no H3 padding or held tail."""
    first = read_frame(frames_dir, 0)
    w = _writer(out, first.shape[1], first.shape[0], lossless=True)
    for i in range(n):
        w.stdin.write(np.clip(read_frame(frames_dir, i), 0, 255).astype(np.uint8).tobytes())
    w.stdin.close()
    if w.wait(): raise RuntimeError('Could not prepare the source clip.')
    return out


def read_video(path: Path, limit: int | None = None) -> list[np.ndarray]:
    c = cv2.VideoCapture(str(path)); out = []
    while limit is None or len(out) < limit:
        ok, f = c.read()
        if not ok:
            break
        out.append(f)
    c.release()
    return out


def video_to_frames(path: Path, n: int, frames_dir: Path) -> int:
    """Unpack native or legacy video into content frames without reducing resolution."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    for f in frames_dir.glob('*.png'):
        f.unlink()
    frames = read_video(path, n)
    for i, f in enumerate(frames):
        cv2.imwrite(str(frame_path(frames_dir, i)), unpad(f))
    return len(frames)


def finish_render(raw: Path, n: int, plate_dir: Path, out_dir: Path, *, crf: int = 16) -> dict:
    """Trim the render to the plate's moving frames, drop the bars, and write result + side-by-side comparison."""
    frames = read_video(raw, n)
    if len(frames) != n:
        raise RuntimeError(f'Render returned {len(frames)} of the required {n} frames; the incomplete result was not accepted.')
    if max(float(f.mean()) for f in frames[:5]) < 1.0:
        raise RuntimeError('render came back black (a known intermittent failure); try again or change the seed')
    height, width = unpad(frames[0]).shape[:2]
    res = _writer(out_dir / 'result.mp4', width, height, lossless=False, crf=crf)
    cmp_ = _writer(out_dir / 'compare.mp4', width * 2, height, lossless=False)
    plw = _writer(out_dir / 'plate.mp4', width, height, lossless=False)
    for i, f in enumerate(frames):
        f = unpad(f)
        if f.shape[:2] != (height, width):
            raise ValueError('Render frame dimensions changed within the video.')
        res.stdin.write(f.tobytes())
        plate = np.clip(read_frame(plate_dir, i, native=True), 0, 255).astype(np.uint8)
        if plate.shape[:2] != (height, width):
            plate = cv2.resize(plate, (width, height), interpolation=cv2.INTER_LANCZOS4)
        cmp_.stdin.write(np.hstack([plate, f]).tobytes())
        plw.stdin.write(plate.tobytes())
        if i == min(len(frames) - 1, n // 2):
            cv2.imwrite(str(out_dir / 'poster.jpg'), f, [cv2.IMWRITE_JPEG_QUALITY, 88])
    for w in (res, cmp_, plw):
        w.stdin.close()
        if w.wait(): raise RuntimeError('Could not encode the finished shot.')
    return {'frames': len(frames), 'width': width, 'height': height, 'result': 'result.mp4', 'compare': 'compare.mp4', 'plate': 'plate.mp4', 'poster': 'poster.jpg'}
