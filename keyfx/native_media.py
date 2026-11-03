"""Native-source path alongside the deliberately unchanged legacy1080p/24 path.

Masters are lossless RGB PNG frames (16-bit for source precision above8-bit),
with original coded geometry, rational PTS and an immutable uploaded source copy.
Their transfer/primaries are *not* converted to the sRGB UI/model proxies.
All public array APIs use RGB order. PNG-in-MOV is used for the downloadable
master because it preserves16-bit RGB, odd dimensions and exact MOV timebase;
browser MP4 is a separately labelled SDR preview, never the master input.
"""
from __future__ import annotations

from contextlib import closing
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

import cv2
import numpy as np

from .native_colour import (NativeColourError, colour, composite_native, convert_primaries, display_frame,
                            display_to_native, from_linear, srgb_to_linear, to_linear, _yuv44416_to_rgb)

PROFILE_VERSION = 'bang-native-source-v1'
_UNKNOWN = {'', 'unknown', 'unspecified', 'reserved', None}
_MATRICES = {'bt709': 'bt709', 'bt2020nc': 'bt2020', 'smpte170m': 'bt601',
             'bt470bg': 'bt601', 'gbr': None, 'rgb': None}


def _run(args: list[str]) -> str:
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode:
        raise ValueError((p.stderr.strip() or 'Media conversion failed.')[-2500:])
    return p.stdout


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def _fraction(value, fallback='0/1') -> Fraction:
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError, TypeError):
        return Fraction(fallback)


@lru_cache(maxsize=1)
def _pixel_formats() -> dict:
    formats = json.loads(_run(['ffprobe', '-v', 'error', '-show_pixel_formats', '-of', 'json']))
    return {item['name']: item for item in formats.get('pixel_formats', [])}


def _format(stream: dict) -> dict:
    fmt = stream.get('pix_fmt', '')
    descriptor = _pixel_formats().get(fmt)
    if not descriptor or descriptor.get('flags', {}).get('hwaccel') or not descriptor.get('components'):
        raise ValueError(f'Unsupported decoded source pixel format {fmt}; native precision was not guessed.')
    return descriptor


def _depth(stream: dict) -> int:
    fmt = stream.get('pix_fmt', '')
    if any(part in fmt for part in ('f16', 'f32', 'f64', 'xyz')):
        raise ValueError('Floating-point/XYZ source formats need a configured colour transform; they are not converted silently.')
    descriptor = _format(stream)
    raw = str(stream.get('bits_per_raw_sample', ''))
    return max([int(raw) if raw.isdigit() else 0,
                *[int(component['bit_depth']) for component in descriptor['components']]])


def inspect_source(path: Path, *, colour_overrides: dict | None = None,
                   assume_sdr: bool = False) -> dict:
    """Probe actual decoded frame PTS, precision and colour, with no conversion.

    Untagged colour remains unknown unless the caller explicitly assigns it or
    opts into the recorded SDR assumption. Overrides use transfer/primaries/
    matrix/range keys; source tags are retained independently either way.
    """
    path = Path(path)
    data = json.loads(_run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_streams', '-show_format', '-show_frames', '-show_entries',
        'stream:format:frame=best_effort_timestamp,pts,duration,pkt_duration,side_data_list',
        '-of', 'json', str(path)]))
    if not data.get('streams'): raise ValueError('The source has no decodable video stream.')
    s = data['streams'][0]
    frames = data.get('frames', [])
    if not frames: raise ValueError('No video frames were decoded from the source.')
    tb = _fraction(s.get('time_base'))
    if tb <= 0: raise ValueError('Source has no valid timestamp timebase.')
    try:
        pts = [int(f.get('best_effort_timestamp', f.get('pts'))) for f in frames]
    except (TypeError, ValueError) as exc:
        raise ValueError('A decoded source frame has no usable PTS; timestamps were not invented.') from exc
    if any(b <= a for a, b in zip(pts, pts[1:])):
        raise ValueError('Nonmonotonic/duplicate source timestamps are unsupported; no normalization was performed.')
    fps = _fraction(s.get('avg_frame_rate')) or _fraction(s.get('r_frame_rate'))
    if fps <= 0:
        if len(pts) < 2: raise ValueError('Source frame rate could not be determined.')
        fps = 1 / ((pts[-1] - pts[0]) * tb / (len(pts) - 1))
    durations = []
    for i, f in enumerate(frames):
        duration = f.get('duration', f.get('pkt_duration'))
        duration = int(duration) if str(duration).lstrip('-').isdigit() else 0
        if i + 1 < len(pts): duration = pts[i + 1] - pts[i]
        if duration <= 0: duration = int(round(float(1 / fps / tb)))
        if duration <= 0: raise ValueError('A source frame has no usable duration.')
        durations.append(duration)
    fmt = s.get('pix_fmt', '')
    bit_depth = _depth(s)
    if bit_depth > 16: raise ValueError('Source precision above16bits is unsupported by the native integer master path.')
    descriptor = _format(s)
    rgb = bool(descriptor.get('flags', {}).get('rgb'))
    src_colour = {'transfer': s.get('color_transfer', 'unknown'),
                  'primaries': s.get('color_primaries', 'unknown'),
                  'matrix': s.get('color_space', 'gbr' if rgb else 'unknown'),
                  'range': s.get('color_range', 'pc' if rgb else 'unknown')}
    c = {**src_colour, **(colour_overrides or {})}
    assumptions = []
    if assume_sdr:
        defaults = {'transfer': 'iec61966-2-1' if rgb else 'bt709', 'primaries': 'bt709',
                    'matrix': 'gbr' if rgb else 'bt709', 'range': 'pc' if rgb else 'tv'}
        for key, value in defaults.items():
            if c.get(key) in _UNKNOWN:
                c[key] = value
                assumptions.append(f'Caller explicitly assumed SDR {key}={value}; source tag remains unspecified.')
    if c.get('range') in ('full', 'jpeg'): c['range'] = 'pc'
    if c.get('range') in ('limited', 'mpeg'): c['range'] = 'tv'
    if c.get('matrix') == 'bt2020': c['matrix'] = 'bt2020nc'
    if c.get('transfer') == 'srgb': c['transfer'] = 'iec61966-2-1'
    c['linear_reference_nits'] = 100
    if c.get('transfer') == 'arib-std-b67':
        c.setdefault('hlg_peak_nits', 1000)
        c['hlg_black_nits'] = 0
        c['hlg_transform'] = 'BT.2100 reference display EOTF; luminance system gamma1.2 at1000nits'
    guards = []
    try: colour(c)
    except NativeColourError as exc: guards.append(str(exc))
    if c.get('matrix') not in _MATRICES: guards.append('Assign a supported source YUV/RGB matrix before native decoding.')
    if c.get('range') not in ('pc', 'tv'): guards.append('Assign full or limited source range before native decoding.')
    if rgb and c.get('range') == 'tv':
        guards.append('Limited-range RGB requires an explicit float working path; it was not silently interpreted as full range.')
    if not rgb and c.get('matrix') in ('gbr', 'rgb'):
        guards.append('A YUV source cannot use an RGB matrix assignment; assign its actual YUV matrix.')
    if descriptor.get('flags', {}).get('alpha'):
        guards.append('Alpha-bearing source video is not supported by this opaque-plate compositor; its alpha is not silently discarded.')
    if fmt.startswith('bayer'):
        guards.append('Bayer/camera RAW needs explicit demosaic and camera colour transforms; no implicit RGB conversion was performed.')
    sar = _fraction(s.get('sample_aspect_ratio', '1:1').replace(':', '/'), '1/1')
    if sar != 1: guards.append('Non-square source pixels require an explicit display-coordinate transform; import was not stretched.')
    rotation = 0
    for side in s.get('side_data_list', []):
        rotation = int(side.get('rotation', rotation))
    frame_side_groups = {}
    for index, frame in enumerate(frames):
        sides = frame.get('side_data_list', [])
        if sides:
            key = json.dumps(sides, sort_keys=True)
            group = frame_side_groups.setdefault(key, {'source_frame_indices': [], 'side_data_list': sides})
            group['source_frame_indices'].append(index)
    side_types = {str(side.get('side_data_type', '')).lower()
                  for side in [*s.get('side_data_list', []),
                               *[side for group in frame_side_groups.values() for side in group['side_data_list']]]}
    if any('dovi' in name or 'dolby vision' in name for name in side_types):
        guards.append('Dolby Vision dynamic metadata is unsupported for native composition; source is retained without silently dropping it.')
    if any('hdr dynamic' in name or '2094' in name for name in side_types):
        guards.append('Dynamic HDR10+ metadata needs a supported frame-metadata export path; it was not silently discarded.')
    if any('icc profile' in name for name in side_types):
        guards.append('An embedded ICC profile requires an explicit ICC colour transform; transfer/primaries tags alone were not substituted for it.')
    rotation = int(s.get('tags', {}).get('rotate', rotation))
    if rotation % 360: guards.append('Rotated source metadata needs an explicit coordinate transform; native pixels were not silently rotated.')
    errors = [abs(float((b - a) * tb - 1 / fps)) for a, b in zip(pts, pts[1:])]
    is_vfr = any(e > float(tb) * 1.01 + 1e-9 for e in errors)
    return {'version': PROFILE_VERSION, 'width': int(s['width']), 'height': int(s['height']),
            'fps': float(fps), 'fps_rational': str(fps), 'fps_num': fps.numerator, 'fps_den': fps.denominator,
            'r_frame_rate': s.get('r_frame_rate'), 'avg_frame_rate': s.get('avg_frame_rate'),
            'time_base': str(tb), 'source_time_base': str(tb), 'frames': len(pts), 'frame_count': len(pts),
            'source_pts': pts, 'frame_durations_ticks': durations,
            'timestamps': [float((t - pts[0]) * tb) for t in pts],
            'duration': float((pts[-1] - pts[0] + durations[-1]) * tb), 'is_vfr': is_vfr,
            'bit_depth': bit_depth, 'storage_bit_depth': 16 if bit_depth > 8 else 8,
            'pix_fmt': fmt, 'source_is_rgb': rgb, 'storage_order': 'RGB', 'storage_range': 'full', 'storage_matrix': 'gbr',
            'colour': c, 'source_colour': src_colour, 'colour_overrides': colour_overrides or {},
            'colour_interpretation': 'assigned' if colour_overrides else ('explicit_sdr_assumption' if assumptions else 'source_metadata'),
            'colour_assumptions': assumptions, 'unsupported_reasons': guards,
            'sample_aspect_ratio': str(sar), 'rotation': rotation,
            'source_stream_metadata': s, 'source_format_metadata': data.get('format', {}),
            'frame_side_data': frames[0].get('side_data_list', []),
            'source_frame_side_data_groups': list(frame_side_groups.values())}


def colour_profile_overrides(name: str, source_profile: dict) -> dict | None:
    """Explicit UI assignment, preserving known code-value matrix/range semantics.

    For untagged YUV the selected video preset explicitly assigns limited range;
    RGB storage explicitly assigns full range. Source metadata remains separate.
    This assigns interpretation, not a conversion of the native pixels.
    """
    if name == 'auto': return None
    presets = {'rec709': ('bt709', 'bt709'), 'srgb': ('iec61966-2-1', 'bt709'),
               'pq2020': ('smpte2084', 'bt2020'), 'hlg2020': ('arib-std-b67', 'bt2020')}
    if name not in presets: raise ValueError('Choose auto,rec709,srgb,pq2020 orhlg2020 source colour.')
    transfer, primaries = presets[name]
    rgb = source_profile.get('source_is_rgb', source_profile.get('pix_fmt', '').startswith(('rgb', 'bgr', 'gbr')))
    original = source_profile.get('source_colour', source_profile.get('colour', {}))
    matrix = 'gbr' if rgb else original.get('matrix')
    if matrix in _UNKNOWN: matrix = 'bt2020nc' if primaries == 'bt2020' else 'bt709'
    range_ = 'pc' if rgb else original.get('range')
    if range_ in _UNKNOWN: range_ = 'tv'
    return {'transfer': transfer, 'primaries': primaries, 'matrix': matrix, 'range': range_}


def _decoded_yuv_frames(source: Path, profile: dict, first: int, last: int):
    """Yield unclipped YUV code planes with high-precision chroma interpolation.

    FFmpeg's RGB-float conversion also clips out-of-gamut RGB on some builds;
    using it to validate headroom would give false assurances. Same-range YUV
    planes retain superblack/superwhite codes, then Python performs the matrix
    calculation in float64. At most one decoded source frame is buffered.
    """
    c = profile['colour']
    matrix = _MATRICES[c['matrix']]
    filters = [f'select=between(n\\,{first}\\,{last})',
               f'scale=in_color_matrix={matrix}:out_color_matrix={matrix}:in_range={c["range"]}:out_range={c["range"]}:flags=accurate_rnd+full_chroma_int',
               'format=yuv444p16le']
    args = ['ffmpeg', '-v', 'error', '-noautorotate', '-i', str(source), '-map', '0:v:0',
            '-vf', ','.join(filters), '-fps_mode', 'passthrough', '-frames:v', str(last - first + 1),
            '-f', 'rawvideo', '-pix_fmt', 'yuv444p16le', 'pipe:1']
    count = profile['width'] * profile['height'] * 3
    with tempfile.TemporaryFile() as error_log:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=error_log)
        try:
            for _ in range(last - first + 1):
                block = process.stdout.read(count * 2)
                if len(block) != count * 2:
                    raise ValueError('Native YUV decoder returned an incomplete source frame.')
                yield np.frombuffer(block, dtype='<u2').reshape(3, profile['height'], profile['width'])
            if process.stdout.read(1):
                raise ValueError('Native YUV decoder returned an unexpected extra frame.')
            process.wait()
            if process.returncode:
                error_log.seek(0)
                raise ValueError(error_log.read().decode(errors='replace')[-2500:] or 'Native YUV decoding failed.')
        finally:
            process.stdout.close()
            if process.poll() is None: process.kill()
            process.wait()


def _write_validated_yuv_master(source: Path, profile: dict, frames_dir: Path,
                                first: int, last: int, *, rgb_range: str = 'preserve') -> dict:
    """Keep the strict boundary unless a legal-range working copy is explicit."""
    dtype = np.uint16 if profile['storage_bit_depth'] == 16 else np.uint8
    peak = np.iinfo(dtype).max
    # Values that round to an endpoint differ by no more than normal integer
    # quantization. Larger signal excursions require a float/EXR working path.
    tolerance = .500001 / peak
    minimum, maximum = math.inf, -math.inf
    clipped_frames, clipped_components = [], 0
    # Bounded PNG writes overlap the decoder; never retain a whole native shot.
    pending = deque()
    with ThreadPoolExecutor(max_workers=2) as pool, closing(_decoded_yuv_frames(source, profile, first, last)) as decoded:
        for i, planes in enumerate(decoded):
            frame = np.empty((profile['height'], profile['width'], 3), dtype=dtype)
            lo, hi, count = math.inf, -math.inf, 0
            # Cache-sized strips use identical float64 arithmetic/quantization,
            # with much less memory traffic than full-frame float RGB arrays.
            for y in range(0, profile['height'], 64):
                rgb = _yuv44416_to_rgb(planes[:, y:y+64], profile)
                if not np.isfinite(rgb).all():
                    raise NativeColourError('Source contains nonfinite decoded RGB; import was stopped.')
                low, high = float(np.min(rgb)), float(np.max(rgb))
                lo, hi = min(lo, low), max(hi, high)
                if low < -tolerance or high > 1 + tolerance:
                    count += int(np.count_nonzero((rgb < -tolerance) | (rgb > 1 + tolerance)))
                frame[y:y+64] = np.rint(np.clip(rgb, 0, 1) * peak).astype(dtype)
            outside = lo < -tolerance or hi > 1 + tolerance
            if outside and rgb_range == 'preserve':
                raise NativeColourError(
                    f'Source frame {first + i} contains superblack/superwhite or out-of-gamut encoded RGB '
                    f'({lo:.8g} to {hi:.8g}); the integer RGB master would clip it. '
                    'Native import was stopped. Use a float/EXR-capable compositor for this signal, '
                    'or choose “Legal-range RGB working copy” in New shot to clip those values explicitly. '
                    'The original file was not modified.')
            if outside:
                clipped_frames.append(first + i)
                clipped_components += count
            minimum, maximum = min(minimum, lo), max(maximum, hi)
            pending.append(pool.submit(write_native_frame, frames_dir, i, frame))
            if len(pending) >= 4: pending.popleft().result()
        for future in pending: future.result()
    return {'method': 'unclipped float64 RGB matrix/range calculation from same-range YUV44416 code planes',
            'frames_checked': last - first + 1, 'encoded_rgb_min': minimum, 'encoded_rgb_max': maximum,
            'endpoint_rounding_tolerance': tolerance,
            'outside_representable_range': 'explicit clamp to encoded RGB[0,1]' if rgb_range == 'legalize' else 'reject before integer master conversion',
            'clipped_source_frames': clipped_frames, 'clipped_components': clipped_components}


def ingest_native(src: Path, master_dir: Path, start_s: float = 0.0,
                  length_s: float | None = None, *, colour_overrides: dict | None = None,
                  assume_sdr: bool = False, rgb_range: str = 'preserve', display_dir: Path | None = None) -> dict:
    """Copy original and extract contiguous source frames without FPS/size/grade conversion."""
    src, master_dir = Path(src), Path(master_dir)
    if rgb_range not in ('preserve', 'legalize'):
        raise ValueError('Choose preserve or legalize for the RGB working range.')
    if not math.isfinite(start_s) or start_s < 0: raise ValueError('Start time must be nonnegative.')
    if length_s is not None and (not math.isfinite(length_s) or length_s <= 0): raise ValueError('Length must be positive.')
    if (master_dir / 'profile.json').exists():
        raise ValueError('A native master already exists; create a new version rather than overwriting it.')
    p = inspect_source(src, colour_overrides=colour_overrides, assume_sdr=assume_sdr)
    if p['unsupported_reasons']: raise ValueError(' '.join(p['unsupported_reasons']))
    selected = [i for i, t in enumerate(p['timestamps']) if t + 1e-9 >= start_s and (length_s is None or t < start_s + length_s - 1e-9)]
    if not selected: raise ValueError('Selected source range has no native frames.')
    first, last = selected[0], selected[-1]
    master_dir.mkdir(parents=True, exist_ok=True)
    fd = master_dir / 'frames'
    if fd.exists() and any(fd.iterdir()): raise ValueError('Native frame folder is not empty; existing frames were not overwritten.')
    fd.mkdir(exist_ok=True)
    copied = master_dir / ('source' + src.suffix.lower())
    shutil.copy2(src, copied)
    source_hash = _sha(src)
    if _sha(copied) != source_hash: raise ValueError('Native original source copy failed its integrity check.')
    if p['source_is_rgb']:
        pixel_format = 'rgb48be' if p['storage_bit_depth'] == 16 else 'rgb24'
        _run(['ffmpeg', '-v', 'error', '-noautorotate', '-i', str(copied), '-map', '0:v:0',
              '-vf', f'select=between(n\\,{first}\\,{last})', '-fps_mode', 'passthrough', '-frames:v', str(len(selected)),
              '-pix_fmt', pixel_format, '-start_number', '0', str(fd / '%05d.png')])
        headroom = {'method': 'source already integer RGB; no YUV-to-RGB clipping boundary',
                    'frames_checked': len(selected), 'outside_representable_range': 'floating-point RGB sources rejected at probe'}
    else:
        headroom = _write_validated_yuv_master(copied, p, fd, first, last, rgb_range=rgb_range)
    paths = sorted(fd.glob('*.png'))
    if len(paths) != len(selected): raise ValueError('Native extraction returned an incomplete frame sequence.')
    def verify_and_proxy(item):
        i, path = item
        im = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if im is None or im.shape != (p['height'], p['width'], 3) or im.dtype != (np.uint16 if p['storage_bit_depth'] == 16 else np.uint8):
            raise ValueError('Native extraction changed geometry/precision or returned unreadable data.')
        if display_dir is not None:
            write_native_frame(display_dir, i, display_frame(cv2.cvtColor(im, cv2.COLOR_BGR2RGB), p))
    if display_dir is None:
        for item in enumerate(paths): verify_and_proxy(item)
    else:
        # Reuse the mandatory verification read for the display proxy rather
        # than decoding every large master PNG a second time afterward.
        pending = deque()
        with ThreadPoolExecutor(max_workers=2) as pool:
            for item in enumerate(paths):
                pending.append(pool.submit(verify_and_proxy, item))
                if len(pending) >= 4: pending.popleft().result()
            for future in pending: future.result()
    old_pts = p['source_pts']; old_durations = p['frame_durations_ticks']; tb = Fraction(p['time_base'])
    p.update(source_file=copied.name, source_sha256=source_hash, frames_dir='frames',
             source_frame_count=p['frames'], source_frame_indices=selected,
             frames=len(selected), frame_count=len(selected),
             source_pts=[old_pts[i] for i in selected],
             frame_durations_ticks=[old_durations[i] for i in selected],
             timestamps=[float((old_pts[i] - old_pts[first]) * tb) for i in selected],
             trim_requested_start_s=start_s, trim_requested_length_s=length_s,
             trim_actual_start_s=float(old_pts[first] * tb),
             duration=float((old_pts[last] - old_pts[first] + old_durations[last]) * tb),
             preview_transform='sRGB8; HDR-only Reinhard100nit display roll-off; never used for native master',
             native_transform='Source integer RGB or unclipped float64 YUV matrix/range decode to validated integer RGB; source transfer/primaries preserved',
             rgb_headroom_validation=headroom,
             rgb_range_policy=rgb_range,
             working_copy_warning=('Explicit legal-range RGB working copy: out-of-range encoded RGB is clipped to[0,1]. '
                                   'Resolution, source timing and colour interpretation are retained; original encoded file is byte-identical.'
                                   if rgb_range == 'legalize' else None),
             export_timeline='Trimmed master and preview begin at0; original absolute source PTS remain in source_pts/sidecar and source copy',
             native_master_scope='Lossless decoded RGB master, not raw/YUV bitstream preservation. Uploaded original retained byte-identical.')
    (master_dir / 'profile.json').write_text(json.dumps(p, indent=2) + '\n')
    return p


def read_profile(master_dir: Path) -> dict:
    return json.loads((Path(master_dir) / 'profile.json').read_text())


def _frames_dir(path: Path) -> Path:
    path = Path(path)
    return path / 'frames' if (path / 'profile.json').is_file() else path


def read_native_frame(master_dir: Path, i: int) -> np.ndarray:
    path = _frames_dir(master_dir) / f'{i:05d}.png'
    im = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if im is None or im.ndim != 3 or im.shape[2] != 3 or im.dtype not in (np.uint8, np.uint16):
        raise ValueError('Requested native RGB frame is missing or invalid.')
    return cv2.cvtColor(im, cv2.COLOR_BGR2RGB)


def write_native_frame(frames_dir: Path, i: int, rgb: np.ndarray) -> Path:
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype not in (np.uint8, np.uint16):
        raise ValueError('Native frame writer expects RGB uint8/uint16 without implicit quantization.')
    frames_dir = Path(frames_dir); frames_dir.mkdir(parents=True, exist_ok=True)
    path = frames_dir / f'{i:05d}.png'
    if not cv2.imwrite(str(path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)):
        raise ValueError('Could not write the native frame.')
    return path


def write_display_proxies(master_dir: Path, out_dir: Path, max_size: tuple[int, int] | None = None) -> dict:
    p = read_profile(master_dir); out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(p['frames']): write_native_frame(out_dir, i, display_frame(read_native_frame(master_dir, i), p, max_size))
    return {'frames': p['frames'], 'path': str(out_dir), 'colour': 'sRGB8 display/model proxy only',
            'hdr_tonemapped': p['colour']['transfer'] in ('smpte2084', 'arib-std-b67')}


def lift_display_edit(base_native: np.ndarray, display_before: np.ndarray,
                      display_after: np.ndarray, profile: dict, mask: np.ndarray,
                      *, white_nits: float = 100.0) -> np.ndarray:
    """Lift localized signed linear-sRGB edit difference onto original native RGB.

    This is an additive edit/light approximation, not an inversion of HDR proxy
    tonemapping or a replacement of the native photograph by an8-bit edit. Source
    highlights remain in native light units; pixels outside the explicit mask or
    with identical before/after pixels are restored exactly.
    """
    c = colour(profile)
    if display_before.shape != base_native.shape or display_after.shape != base_native.shape:
        raise ValueError('Display edit and native frame geometry must match.')
    a = np.asarray(mask)
    if a.shape == base_native.shape[:2] + (1,): a = a[..., 0]
    if a.shape != base_native.shape[:2]: raise ValueError('Native muzzle mask geometry differs.')
    a = a.astype(np.float64) / (np.iinfo(a.dtype).max if np.issubdtype(a.dtype, np.integer) else 1)
    if not np.isfinite(a).all() or np.any((a < 0) | (a > 1)): raise ValueError('Native edit mask must be finite[0,1].')
    delta = (srgb_to_linear(display_after) - srgb_to_linear(display_before)) * (float(white_nits) / 100)
    delta = convert_primaries(delta, 'bt709', c['primaries']) * a[..., None]
    result = from_linear(to_linear(base_native, profile) + delta, profile, base_native.dtype)
    changed = (a != 0) & np.any(display_after != display_before, axis=-1)
    result[~changed] = base_native[~changed]
    return result


def _concat(frames_dir: Path, profile: dict, dest: Path) -> None:
    pts, durations = profile['source_pts'], profile['frame_durations_ticks']
    tb = Fraction(profile['time_base'])
    # High-rate image demux timebase avoids the concat demuxer's default25fps
    # rounding. MOV output timebase is restored to source ticks below.
    rows = ['ffconcat version 1.0']
    for i in range(profile['frames']):
        path = (frames_dir / f'{i:05d}.png').resolve()
        quoted = str(path).replace("'", "'\\''")
        duration = (pts[i + 1] - pts[i]) * tb if i + 1 < len(pts) else durations[i] * tb
        rows += [f"file '{quoted}'", 'option framerate 1000000', f'duration {float(duration):.12f}']
    dest.write_text('\n'.join(rows) + '\n')


def _verify_export(path: Path, profile: dict, *, require_precision: bool = True) -> dict:
    actual = inspect_source(path)
    if (actual['width'], actual['height'], actual['frames']) != (profile['width'], profile['height'], profile['frames']):
        raise ValueError('Native master failed geometry/frame-count verification.')
    if require_precision and actual['bit_depth'] < profile['storage_bit_depth']:
        raise ValueError('Native master export lost channel precision.')
    if actual['source_pts'][0] != 0:
        raise ValueError('Trimmed native master must begin at0; absolute source PTS are retained in its sidecar.')
    source_tb, output_tb = Fraction(profile['time_base']), Fraction(actual['time_base'])
    expected = [(i - profile['source_pts'][0]) * source_tb for i in profile['source_pts']]
    found = [(i - actual['source_pts'][0]) * output_tb for i in actual['source_pts']]
    tolerance = max(float(output_tb), float(source_tb)) + 1e-8
    if any(abs(float(a - b)) > tolerance for a, b in zip(expected, found)):
        raise ValueError('Native export could not preserve source PTS; result was not accepted as a master.')
    if abs(actual['duration'] - profile['duration']) > tolerance:
        raise ValueError('Native export changed final-frame duration; result was not accepted as a master.')
    expected_transfer = 'iec61966-2-1' if profile['colour']['transfer'] == 'srgb' else profile['colour']['transfer']
    if actual['colour']['primaries'] != profile['colour']['primaries'] or actual['colour']['transfer'] != expected_transfer:
        raise ValueError('Native master did not retain source transfer/primaries tags.')
    return actual


def _timing(profile: dict) -> tuple[str, str]:
    """Image2 rate supplies final duration; explicit PTS preserve VFR intervals."""
    tb = Fraction(profile['time_base'])
    rate = 1 / (profile['frame_durations_ticks'][-1] * tb) if profile['is_vfr'] else Fraction(profile['fps_rational'])
    start = profile['source_pts'][0]
    terms = '+'.join(f'eq(N,{i})*{pts-start}' for i, pts in enumerate(profile['source_pts']))
    return str(rate), f"settb=expr={tb.numerator}/{tb.denominator},setpts='{terms}'"


def export_native(frames_dir: Path, profile: dict, out_dir: Path,
                  source_audio: Path | None = None) -> dict:
    """Lossless source-colour master + separate browser preview; no frame normalization.

    Source audio, when supplied, comes from the original source timeline and is
    trimmed at absolute selected video PTS, encoded as PCM in the master, AAC in
    the preview. The original uploaded file remains
    byte-identical and the exact source colour/timing profile ships alongside.
    """
    colour(profile)
    frames_dir, out_dir = _frames_dir(Path(frames_dir)), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    expected_dtype = np.uint16 if profile['storage_bit_depth'] == 16 else np.uint8
    for i in range(profile['frames']):
        im = read_native_frame(frames_dir, i)
        if im.shape != (profile['height'], profile['width'], 3) or im.dtype != expected_dtype:
            raise ValueError('Native export input changed source dimensions/precision.')
    concat = out_dir / 'native_frames.ffconcat'; _concat(frames_dir, profile, concat)
    master = out_dir / 'master.mov'
    encoded_master = out_dir / '.master_encoded.mov'
    c = colour(profile); tb = Fraction(profile['time_base'])
    # A source timebase numerator>1 can still be represented by a denominator
    # timescale, without rounding any integer source tick.
    input_rate, timing_filter = _timing(profile)
    args = ['ffmpeg', '-v', 'error', '-y', '-framerate', input_rate, '-i', str(frames_dir / '%05d.png')]
    if source_audio is not None:
        # -ss normally adds format.start_time. trim_actual_start_s already is
        # absolute source PTS, so adding a5s source origin would seek to10s.
        args += ['-seek_timestamp', '1', '-ss', str(profile.get('trim_actual_start_s', 0)), '-i', str(source_audio)]
    args += ['-map', '0:v:0']
    if source_audio is not None: args += ['-map', '1:a:0?', '-c:a', 'pcm_s24le']
    else: args += ['-an']
    args += ['-frames:v', str(profile['frames']), '-fps_mode', 'passthrough', '-vf', timing_filter,
             '-enc_time_base:v', f'1:{tb.denominator}',
             '-c:v', 'png', '-pix_fmt', 'rgb48be' if profile['storage_bit_depth'] == 16 else 'rgb24',
             '-color_primaries', c['primaries'], '-color_trc', 'iec61966-2-1' if c['transfer'] == 'srgb' else c['transfer'],
             '-colorspace', 'rgb', '-color_range', 'pc', '-video_track_timescale', str(tb.denominator),
             '-t', f"{profile['duration']:.12f}", '-movflags', '+faststart', str(encoded_master)]
    _run(args)
    # PNG encoder does not consistently propagate transfer/primaries into MOV's
    # stream parameters. A lossless remux assigns verified colr metadata; merely
    # passing encoder flags can leave the resulting master untagged.
    _run(['ffmpeg', '-v', 'error', '-y', '-i', str(encoded_master), '-map', '0', '-c', 'copy',
          '-color_primaries', c['primaries'], '-color_trc', 'iec61966-2-1' if c['transfer'] == 'srgb' else c['transfer'],
          '-colorspace', 'rgb', '-color_range', 'pc', '-video_track_timescale', str(tb.denominator),
          '-movflags', '+faststart+write_colr', str(master)])
    actual = _verify_export(master, profile)
    encoded_master.unlink()
    # Decode master again and compare actual channel values. RGB PNG video must
    # not conceal an8-bit or YUV intermediate, even if its stream tag says16bit.
    raw = subprocess.Popen(['ffmpeg', '-v', 'error', '-noautorotate', '-i', str(master), '-map', '0:v:0',
                            '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'rgb48le' if expected_dtype == np.uint16 else 'rgb24', '-'],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    count = profile['width'] * profile['height'] * 3
    for i in range(profile['frames']):
        block = raw.stdout.read(count * np.dtype(expected_dtype).itemsize)
        frame = np.frombuffer(block, dtype='<u2' if expected_dtype == np.uint16 else np.uint8)
        if frame.size != count or not np.array_equal(frame.reshape(profile['height'], profile['width'], 3), read_native_frame(frames_dir, i)):
            raw.kill(); raw.communicate(); raise ValueError('Native master failed exact decoded RGB verification.')
    _, err = raw.communicate()
    if raw.returncode: raise ValueError(err.decode(errors='replace')[-2000:])
    proxy_dir = out_dir / 'display_frames'; proxy_dir.mkdir(exist_ok=True)
    for i in range(profile['frames']): write_native_frame(proxy_dir, i, display_frame(read_native_frame(frames_dir, i), profile))
    _concat(proxy_dir, profile, out_dir / 'display_frames.ffconcat')
    preview = out_dir / 'result.mp4'
    args = ['ffmpeg', '-v', 'error', '-y', '-framerate', input_rate, '-i', str(proxy_dir / '%05d.png')]
    if source_audio is not None:
        args += ['-seek_timestamp', '1', '-ss', str(profile.get('trim_actual_start_s', 0)), '-i', str(source_audio)]
    args += ['-map', '0:v:0']
    if source_audio is not None: args += ['-map', '1:a:0?', '-c:a', 'aac', '-b:a', '192k']
    else: args += ['-an']
    args += ['-frames:v', str(profile['frames']), '-fps_mode', 'passthrough',
             '-enc_time_base:v', f'1:{tb.denominator}', '-video_track_timescale', str(tb.denominator),
             '-vf', timing_filter + ',pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0:black,scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int',
             '-c:v', 'libx264', '-crf', '16', '-pix_fmt', 'yuv420p',
             '-bsf:v', 'h264_metadata=colour_primaries=1:transfer_characteristics=13:matrix_coefficients=1:video_full_range_flag=0',
             '-color_trc', 'iec61966-2-1', '-color_primaries', 'bt709', '-colorspace', 'bt709', '-color_range', 'tv',
             '-t', f"{profile['duration']:.12f}", '-movflags', '+faststart', str(preview)]
    _run(args)
    poster = display_frame(read_native_frame(frames_dir, profile['frames'] // 2), profile)
    cv2.imwrite(str(out_dir / 'poster.jpg'), cv2.cvtColor(poster, cv2.COLOR_RGB2BGR))
    verification = {'master': 'master.mov', 'preview': 'result.mp4', 'result': 'result.mp4', 'profile': 'native_profile.json',
                    'poster': 'poster.jpg', 'frames': profile['frames'], 'width': profile['width'], 'height': profile['height'],
                    'fps': profile['fps'], 'fps_rational': profile['fps_rational'], 'is_vfr': profile['is_vfr'],
                    'storage_bit_depth': profile['storage_bit_depth'], 'master_pixels_verified_exact': True,
                    'master_colour': {'primaries': actual['colour']['primaries'], 'transfer': actual['colour']['transfer'],
                                      'matrix': 'RGB', 'range': 'full'},
                    'preview_colour': 'sRGB8; HDR roll-off only in preview', 'preview_even_padding': [profile['width'] % 2, profile['height'] % 2],
                    'source_hdr_side_data_preserved_in_profile': True,
                    'master_codec': 'lossless PNG RGB in MOV; original source codec/container retained separately'}
    (out_dir / 'native_profile.json').write_text(json.dumps(profile, indent=2) + '\n')
    (out_dir / 'master_verification.json').write_text(json.dumps(verification, indent=2) + '\n')
    return verification
