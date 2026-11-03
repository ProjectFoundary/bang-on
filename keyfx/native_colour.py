"""Explicit native RGB colour transforms for plate-preserving compositing.

Frame arrays are RGB, not OpenCV BGR. Linear values use 100 cd/m² as 1.0.
Integer native frames retain the source transfer and primaries. PQ is absolute;
HLG uses the BT.2100 reference 1000-nit/zero-black display, gamma1.2 unless a
different nominal peak is explicitly recorded. The display proxy's HDR roll-off
is *not* used on masters. Untagged/log/unsupported encodings fail explicitly.

References: IEC61966-2-1, ITU-R BT.709, BT.2020 and BT.2100-2 (Tables4/5).
"""
from __future__ import annotations

from functools import lru_cache
import json
import numpy as np

WHITE_NITS = 100.0
_PRIMARIES = {
    'bt709': ((.64, .33), (.30, .60), (.15, .06)),
    'bt2020': ((.708, .292), (.170, .797), (.131, .046)),
    'smpte432': ((.68, .32), (.265, .69), (.15, .06)),
    'smpte170m': ((.630, .340), (.310, .595), (.155, .070)),
    'bt470bg': ((.640, .330), (.290, .600), (.150, .060)),
}
_TRANSFERS = {'iec61966-2-1', 'srgb', 'bt709', 'smpte170m', 'bt2020-10',
              'bt2020-12', 'smpte2084', 'arib-std-b67', 'linear', 'gamma22', 'gamma28'}
_YUV_COEFFICIENTS = {'bt709': (.2126, .0722), 'bt2020nc': (.2627, .0593),
                     'smpte170m': (.299, .114), 'bt470bg': (.299, .114)}


class NativeColourError(ValueError):
    pass


def colour(profile: dict) -> dict:
    """Accept a complete native profile or its colour dictionary."""
    c = profile.get('colour', profile)
    transfer = c.get('transfer', c.get('color_transfer', 'unknown'))
    primaries = c.get('primaries', c.get('color_primaries', 'unknown'))
    if transfer not in _TRANSFERS or primaries not in _PRIMARIES:
        raise NativeColourError(f'Native colour transform needs supported explicit transfer/primaries; '
                                f'got {transfer}/{primaries}. Assign the source colour profile before compositing.')
    return {**c, 'transfer': transfer, 'primaries': primaries}


def _yuv44416_to_rgb(planes: np.ndarray, profile: dict) -> np.ndarray:
    """Unclipped encoded RGB from swscale's same-range planar YUV16 output.

    This private decode boundary takes Y,U,V planes, not a public RGB frame.
    The integer YUV conversion only upsamples chroma/increases precision; RGB
    matrix/range arithmetic stays float64 so negative RGB and signal headroom
    remain detectable before the integer-master boundary. swscale expands full
    range luma to65535 but shifts chroma to preserve its32768 neutral code.
    No transfer, primaries, gamut mapping or clipping is performed here.
    """
    c = colour(profile)
    if c.get('matrix') not in _YUV_COEFFICIENTS or c.get('range') not in ('tv', 'pc'):
        raise NativeColourError('Native YUV decoding needs explicit supported matrix and range.')
    values = np.asarray(planes)
    if values.dtype != np.uint16 or values.ndim != 3 or values.shape[0] != 3:
        raise NativeColourError('The native YUV boundary expects three full-resolution uint16 Y,U,V planes.')
    source_depth = int(profile['bit_depth'])
    if not 8 <= source_depth <= 16:
        raise NativeColourError('Native YUV component precision must be8–16bits.')
    y, cb, cr = values.astype(np.float64)
    if c['range'] == 'tv':
        y = (y - 16 * 256) / (219 * 256)
        cb = (cb - 128 * 256) / (224 * 256)
        cr = (cr - 128 * 256) / (224 * 256)
    else:
        y /= 65535
        span = ((1 << source_depth) - 1) * (1 << (16 - source_depth))
        cb = (cb - 32768) / span
        cr = (cr - 32768) / span
    kr, kb = _YUV_COEFFICIENTS[c['matrix']]
    r, b = y + 2 * (1 - kr) * cr, y + 2 * (1 - kb) * cb
    g = (y - kr * r - kb * b) / (1 - kr - kb)
    return np.stack([r, g, b], axis=-1)


def _matrix(name: str) -> np.ndarray:
    points = np.array(_PRIMARIES[name], dtype=np.float64)
    xyz = np.stack([points[:, 0] / points[:, 1], np.ones(3),
                    (1 - points.sum(axis=1)) / points[:, 1]])
    white = np.array([.3127 / .3290, 1, (1 - .3127 - .3290) / .3290])
    return xyz * np.linalg.solve(xyz, white)[None, :]


def convert_primaries(rgb: np.ndarray, source: str, target: str) -> np.ndarray:
    if source not in _PRIMARIES or target not in _PRIMARIES:
        raise NativeColourError('Unsupported RGB primaries conversion.')
    if source == target:
        return np.asarray(rgb, dtype=np.float64)
    transform = np.linalg.solve(_matrix(target), _matrix(source))
    return np.asarray(rgb, dtype=np.float64) @ transform.T


def _hlg_decode(v: np.ndarray, c: dict) -> np.ndarray:
    a, b, cc = .17883277, .28466892, .55991073
    scene = np.where(v <= .5, v * v / 3,
                     (np.exp((v - cc) / a) + b) / 12)
    peak = float(c.get('hlg_peak_nits', 1000))
    if not 400 <= peak <= 2000:
        raise NativeColourError('HLG reference peak must be400–2000nits for this supported transform.')
    gamma = 1.2 + .42 * np.log10(peak / 1000)
    # BT.2100 OOTF applies system gamma to luminance, not each RGB channel.
    luminance = np.maximum(scene @ _matrix(c['primaries'])[1], 0)
    return scene * np.power(luminance, gamma - 1)[..., None] * (peak / WHITE_NITS)


def decode(encoded: np.ndarray, profile: dict) -> np.ndarray:
    """Encoded RGB[0,1] -> display/transfer-linear RGB in source primaries."""
    c = colour(profile)
    v = np.maximum(np.asarray(encoded, dtype=np.float64), 0)
    t = c['transfer']
    if t in ('srgb', 'iec61966-2-1'):
        return np.where(v <= .04045, v / 12.92, ((v + .055) / 1.055) ** 2.4)
    if t in ('bt709', 'smpte170m', 'bt2020-10', 'bt2020-12'):
        alpha, beta = (1.0993, .0181) if t == 'bt2020-12' else (1.099, .018)
        return np.where(v < 4.5 * beta, v / 4.5, ((v + alpha - 1) / alpha) ** (1 / .45))
    if t == 'smpte2084':
        m1, m2, c1, c2, c3 = 2610 / 16384, 2523 / 32, 3424 / 4096, 2413 / 128, 2392 / 128
        p = np.power(v, 1 / m2)
        return np.power(np.maximum(p - c1, 0) / np.maximum(c2 - c3 * p, 1e-12), 1 / m1) * 100
    if t == 'arib-std-b67':
        return _hlg_decode(v, c)
    if t == 'linear':
        return v
    return np.power(v, 2.2 if t == 'gamma22' else 2.8)


def encode(linear: np.ndarray, profile: dict) -> np.ndarray:
    """Source-linear RGB -> encoded RGB; no SDR tonemap is applied."""
    c = colour(profile)
    x = np.maximum(np.asarray(linear, dtype=np.float64), 0)
    t = c['transfer']
    if t in ('srgb', 'iec61966-2-1'):
        return np.where(x <= .0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - .055)
    if t in ('bt709', 'smpte170m', 'bt2020-10', 'bt2020-12'):
        alpha, beta = (1.0993, .0181) if t == 'bt2020-12' else (1.099, .018)
        return np.where(x < beta, x * 4.5, alpha * x ** .45 - (alpha - 1))
    if t == 'smpte2084':
        m1, m2, c1, c2, c3 = 2610 / 16384, 2523 / 32, 3424 / 4096, 2413 / 128, 2392 / 128
        p = np.power(x / 100, m1)
        return np.power((c1 + c2 * p) / (1 + c3 * p), m2)
    if t == 'arib-std-b67':
        peak = float(c.get('hlg_peak_nits', 1000))
        if not 400 <= peak <= 2000:
            raise NativeColourError('HLG reference peak must be400–2000nits.')
        gamma = 1.2 + .42 * np.log10(peak / 1000)
        display = x / (peak / WHITE_NITS)
        luminance = np.maximum(display @ _matrix(c['primaries'])[1], 0)
        scene = display * np.power(np.maximum(luminance, 1e-30), (1 - gamma) / gamma)[..., None]
        a, b, cc = .17883277, .28466892, .55991073
        return np.where(scene <= 1 / 12, np.sqrt(3 * scene),
                        a * np.log(np.maximum(12 * scene - b, 1e-30)) + cc)
    if t == 'linear':
        return x
    return x ** (1 / (2.2 if t == 'gamma22' else 2.8))


def to_linear(frame: np.ndarray, profile: dict) -> np.ndarray:
    """Native RGB uint8/uint16 -> float RGB,1.0=100nits in source gamut."""
    a = np.asarray(frame)
    if a.dtype not in (np.uint8, np.uint16) or a.ndim != 3 or a.shape[-1] != 3:
        raise NativeColourError('Native frames must be three-channel RGB uint8/uint16.')
    return decode(a.astype(np.float64) / np.iinfo(a.dtype).max, profile)


def from_linear(linear: np.ndarray, profile: dict, dtype=np.uint16) -> np.ndarray:
    if np.dtype(dtype) not in (np.dtype('uint8'), np.dtype('uint16')):
        raise NativeColourError('Native output must be uint8 or uint16.')
    values = np.clip(encode(linear, profile), 0, 1)
    return np.rint(values * np.iinfo(dtype).max).astype(dtype)


def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    values = np.asarray(rgb)
    if np.issubdtype(values.dtype, np.integer):
        values = values.astype(np.float64) / np.iinfo(values.dtype).max
    return decode(values, {'transfer': 'srgb', 'primaries': 'bt709'})


@lru_cache(maxsize=16)
def _display_lut(bits: int, encoded_colour: str) -> np.ndarray:
    """Exact integer-code lookup for channel-independent source→sRGB previews."""
    c = json.loads(encoded_colour)
    values = decode(np.arange(1 << bits, dtype=np.float64) / ((1 << bits) - 1), c)
    if c['transfer'] == 'smpte2084':
        values = np.maximum(values, 0) / (1 + np.maximum(values, 0))
    result = from_linear(values, {'transfer': 'srgb', 'primaries': 'bt709'}, np.uint8)
    result.flags.writeable = False
    return result


def display_frame(frame: np.ndarray, profile: dict, max_size: tuple[int, int] | None = None) -> np.ndarray:
    """RGB8sRGB proxy only. HDR uses documented100nit Reinhard preview rolloff."""
    import cv2
    c = colour(profile)
    a = np.asarray(frame)
    if c['primaries'] == 'bt709' and c['transfer'] != 'arib-std-b67' and a.dtype in (np.uint8, np.uint16) and a.ndim == 3 and a.shape[-1] == 3:
        # This is the same colour arithmetic for every possible integer code,
        # not a display-grade approximation. HLG and gamut matrices mix RGB
        # channels and must retain the full transform below.
        out = _display_lut(a.dtype.itemsize * 8, json.dumps(c, sort_keys=True))[a]
    else:
        linear = convert_primaries(to_linear(frame, profile), c['primaries'], 'bt709')
        if c['transfer'] in ('smpte2084', 'arib-std-b67'):
            linear = np.maximum(linear, 0) / (1 + np.maximum(linear, 0))
        out = from_linear(linear, {'transfer': 'srgb', 'primaries': 'bt709'}, np.uint8)
    if max_size:
        factor = min(1, max_size[0] / out.shape[1], max_size[1] / out.shape[0])
        if factor < 1:
            out = cv2.resize(out, (max(1, round(out.shape[1] * factor)), max(1, round(out.shape[0] * factor))), interpolation=cv2.INTER_AREA)
    return out


def display_to_native(rgb: np.ndarray, profile: dict, dtype=np.uint16, *, white_nits=100.0) -> np.ndarray:
    """sRGB model/display RGB -> source-encoded RGB; not an inverse of HDR tonemapping."""
    c = colour(profile)
    linear = convert_primaries(srgb_to_linear(rgb) * (float(white_nits) / WHITE_NITS), 'bt709', c['primaries'])
    return from_linear(linear, profile, dtype)


def composite_native(base: np.ndarray, profile: dict, fg_srgb: np.ndarray | None = None,
                     alpha: np.ndarray | None = None, *, premultiplied: bool = False,
                     light_rgb: np.ndarray | None = None, shadow: np.ndarray | None = None,
                     white_nits: float = 100.0) -> np.ndarray:
    """Composite layers in linear source gamut; untouched encoded base pixels exact.

    fg_srgb is RGB8 or encoded float[0,1]. ``premultiplied=True`` treats its
    linearized black-backed RGB as already attenuated/emissive. light_rgb is
    signed linear-sRGB light in100nit units, and shadow is scalar transmittance.
    Alpha is data, never gamma transformed. No native master tonemapping.
    """
    c = colour(profile)
    base = np.asarray(base)
    linear = to_linear(base, profile)
    mask = np.zeros(base.shape[:2], bool)
    if shadow is not None:
        s = np.asarray(shadow, dtype=np.float64)
        if s.shape == base.shape[:2] + (1,): s = s[..., 0]
        if s.shape != base.shape[:2] or not np.isfinite(s).all() or np.any((s < 0) | (s > 1)):
            raise NativeColourError('Shadow must be matching finite transmittance[0,1].')
        linear *= s[..., None]
        mask |= s != 1
    if light_rgb is not None:
        light = np.asarray(light_rgb, dtype=np.float64)
        if light.shape != base.shape or not np.isfinite(light).all():
            raise NativeColourError('Light must be matching finite linear RGB.')
        linear += convert_primaries(light, 'bt709', c['primaries'])
        mask |= np.any(light != 0, axis=-1)
    if fg_srgb is not None:
        if alpha is None or np.asarray(fg_srgb).shape != base.shape:
            raise NativeColourError('Foreground needs matching RGB and alpha.')
        a = np.asarray(alpha)
        if a.shape == base.shape[:2] + (1,): a = a[..., 0]
        if a.shape != base.shape[:2]: raise NativeColourError('Alpha dimensions differ.')
        a = a.astype(np.float64) / (np.iinfo(a.dtype).max if np.issubdtype(a.dtype, np.integer) else 1)
        if not np.isfinite(a).all() or np.any((a < 0) | (a > 1)):
            raise NativeColourError('Alpha must be finite data[0,1].')
        fg = convert_primaries(srgb_to_linear(fg_srgb) * (float(white_nits) / WHITE_NITS), 'bt709', c['primaries'])
        if not np.isfinite(fg).all(): raise NativeColourError('Foreground contains nonfinite values.')
        linear = linear * (1 - a[..., None]) + fg * (1 if premultiplied else a[..., None])
        mask |= (a != 0) | (np.any(fg != 0, axis=-1) if premultiplied else False)
    out = from_linear(linear, profile, base.dtype)
    out[~mask] = base[~mask]
    return out
