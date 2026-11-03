"""Local connected depth-region picking; this is not semantic segmentation."""
from __future__ import annotations

import hashlib
import json
import math

import cv2
import numpy as np

from . import geometry


def _unit(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f'{name} must be between zero and one.')
    return float(value)


def select(root, cfg, points, tolerance=.04, bounds=None):
    entry = cfg.get('depth')
    if not entry or entry.get('reference_frame', cfg['reference_frame']) != cfg['reference_frame']:
        raise ValueError('Import or generate depth for the current reference frame first.')
    if not isinstance(points, list) or not 1 <= len(points) <= 32:
        raise ValueError('Choose one to 32 points on the depth image.')
    seeds = []
    for point in points:
        if not isinstance(point, dict) or set(point) != {'x', 'y'}:
            raise ValueError('Depth points need normalized x and y coordinates.')
        seeds.append({'x': _unit(point['x'], 'Point X'), 'y': _unit(point['y'], 'Point Y')})
    tolerance = _unit(tolerance, 'Depth tolerance')
    if tolerance > .5:
        raise ValueError('Depth tolerance must not exceed 0.5 of the depth range.')
    if bounds is None:
        bounds = [0., 0., 1., 1.]
    if not isinstance(bounds, list) or len(bounds) != 4:
        raise ValueError('Region bounds need x0, y0, x1 and y1.')
    bounds = [_unit(value, 'Region bound') for value in bounds]
    x0, y0, x1, y1 = bounds
    if x0 >= x1 or y0 >= y1:
        raise ValueError('The selection region must have positive width and height.')
    data = geometry.depth_data(root, cfg)
    w, h = data['width'], data['height']
    values = np.asarray(data['values'], np.float32).reshape(h, w)
    yy, xx = np.mgrid[:h, :w]
    region = ((xx + .5) / w >= x0) & ((xx + .5) / w <= x1) & ((yy + .5) / h >= y0) & ((yy + .5) / h <= y1)
    result = np.zeros((h, w), np.uint8)
    for seed in seeds:
        x, y = min(w-1, int(seed['x']*w)), min(h-1, int(seed['y']*h))
        if not region[y, x]:
            raise ValueError('Selection points must be inside the region bounds.')
        eligible = np.uint8((np.abs(values-values[y, x]) <= tolerance + 1e-7) & region) * 255
        cv2.floodFill(eligible, None, (x, y), 128, flags=4)
        result[eligible == 128] = 255
    count = int(np.count_nonzero(result))
    if not count:
        raise ValueError('This depth selection is empty.')
    recipe = {'depth_id': entry['id'], 'depth_sha256': entry['sha256'], 'reference_frame': cfg['reference_frame'],
              'points': seeds, 'tolerance': tolerance, 'bounds': bounds, 'width': w, 'height': h}
    key = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode() + result.tobytes()).hexdigest()
    return result, np.uint8(np.clip(values, 0, 1)*255), {**recipe, 'selection_key': key, 'pixels': count, 'coverage': count/(w*h)}
