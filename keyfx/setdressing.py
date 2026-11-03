"""Native FLUX 3 regional still edits with exact source-pixel protection.

Placement coordinates, denoise working room and final permission to change
pixels are separate. No placeholder rectangles are painted on the photograph.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import uuid

import cv2
import numpy as np

from . import comfy, image_models

MODELS = {'unet': 'flux3-image-klein-int8-convrot.safetensors',
          'clip': 'qwen3vl_4b_bf16.safetensors', 'vae': 'flux2-vae.safetensors'}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value, name: str, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f'{name} must be between {lo:g} and {hi:g}.')
    return float(value)


def box(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError('Draw a placement or protected area first.')
    b = {k: _number(value.get(k), k, 0, 1) for k in ('x0', 'y0', 'x1', 'y1')}
    if b['x1'] - b['x0'] < .005 or b['y1'] - b['y0'] < .005:
        raise ValueError('Areas must have a positive width and height.')
    return b


def validate(draft: dict) -> dict:
    if not isinstance(draft, dict):
        raise ValueError('Invalid object-placement draft.')
    source = draft.get('source')
    if source is not None and not isinstance(source, str):
        raise ValueError('Choose an approved location first.')
    objects, protected = draft.get('objects', []), draft.get('protected', [])
    if not isinstance(objects, list) or not isinstance(protected, list) or len(objects) > 8 or len(protected) > 30:
        raise ValueError('Use up to 8 objects and 30 protected areas per edit.')
    ids = set()
    def identity(item):
        value = item.get('id') if isinstance(item, dict) else None
        if not isinstance(value, str) or not value or len(value) > 40 or value in ids:
            raise ValueError('Every area needs a unique id.')
        ids.add(value)
        return value
    out = []
    for item in objects:
        uid = identity(item)
        text = item.get('text', '')
        if not isinstance(text, str) or len(text) > 500:
            raise ValueError('Object descriptions must be text, up to 500 characters.')
        action = item.get('action', 'add')
        if action not in ('add', 'replace'):
            raise ValueError('Object edits support Add or Replace.')
        out.append({'id': uid, 'text': text.strip(), 'action': action, 'box': box(item.get('box'))})
    keep = [{'id': identity(item), 'box': box(item.get('box'))} for item in protected]
    seed = _number(draft.get('seed', 281040), 'Image seed', 0, 2**53 - 1)
    if not seed.is_integer():
        raise ValueError('Image seed must be a whole number.')
    people = draft.get('people', True)
    if not isinstance(people, bool): raise ValueError('People in this reference must be on or off.')
    return {'source': source, 'objects': out, 'protected': keep, 'seed': int(seed), 'people': people,
            'margin': _number(draft.get('margin', .04), 'Shadow room', 0, .15)}


def _rect(mask: np.ndarray, b: dict, grow: int = 0) -> None:
    h, w = mask.shape
    x0, y0 = max(0, math.floor(b['x0'] * w) - grow), max(0, math.floor(b['y0'] * h) - grow)
    x1, y1 = min(w, math.ceil(b['x1'] * w) + grow), min(h, math.ceil(b['y1'] * h) + grow)
    mask[y0:y1, x0:x1] = 255


def masks(size: tuple[int, int], draft: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return noise mask, final permission mask, protected mask and finite feather."""
    w, h = size
    permission = np.zeros((h, w), np.uint8)
    noise = np.zeros_like(permission)
    protected = np.zeros_like(permission)
    margin = round(draft['margin'] * w)
    for obj in draft['objects']:
        _rect(permission, obj['box'], margin)
        _rect(noise, obj['box'], margin + max(8, round(w * .0125)))
    # Feather only the outside boundary, never introduce original-object rims
    # beside a protected person's silhouette or a protected prop.
    distance = cv2.distanceTransform((permission > 0).astype(np.uint8), cv2.DIST_L2, 5)
    alpha = np.minimum(distance / max(2, round(w / 128)), 1)
    for region in draft['protected']:
        _rect(protected, region['box'])
    permission[protected > 0] = 0
    noise[protected > 0] = 0
    alpha[permission == 0] = 0
    for obj in draft['objects']:
        target = np.zeros_like(permission); _rect(target, obj['box'])
        if not np.any((target > 0) & (permission > 0)):
            raise ValueError('An object is completely covered by protected areas. Move or resize its box.')
    return noise, permission, protected, alpha.astype(np.float32)


def caption(draft: dict, content_size: tuple[int, int], canvas_size: tuple[int, int]) -> str:
    """Expanded edit instruction and native FLUX layout table, 0–1000 yxyx."""
    w, h = content_size; cw, ch = canvas_size
    clauses, rows = [], []
    for i, obj in enumerate(draft['objects'], 1):
        b = obj['box']; token = f'obj_{i}'
        description = obj['text'].rstrip('.')
        operation = 'Add' if obj['action'] == 'add' else 'Replace the content inside this target with'
        clauses.append(f'{operation} {description} <{token}>, filling its specified placement rectangle. '
                       'Use the rectangle to set the complete visible silhouette, apparent scale and position.')
        rows.append({'id': token, 'bbox': [round(b['y0'] * h / ch * 1000), round(b['x0'] * w / cw * 1000),
                                          round(b['y1'] * h / ch * 1000), round(b['x1'] * w / cw * 1000)],
                     'desc': description})
    instruction = ('Edit the supplied photograph with these local object changes: ' + ' '.join(clauses) +
                   ' Match each object to the existing camera height, perspective, depth, focus, window or practical '
                   'illumination, colour and photographic texture. For objects standing on the floor, anchor the base '
                   'at the lower edge of its rectangle, with a narrow contact shadow and a natural soft cast shadow '
                   'on nearby surfaces. Preserve the existing scene behind and between the objects. Where a new '
                   'object covers an existing surface, form a coherent occlusion. Keep all people, their complete '
                   'bodies, faces, clothing, hands and held objects, every protected detail, existing props outside '
                   'replacement targets, background geometry, camera framing and exposure unchanged. '
                   'Limit other changes to the immediate shadows and contact around the requested objects.')
    return instruction + ' ' + json.dumps(rows, ensure_ascii=False)


def graph(prompt: str, seed: int) -> dict:
    def n(cls, **inputs): return {'class_type': cls, 'inputs': inputs}
    return {
        'img': n('LoadImage', image='source.png'),
        'mask': n('LoadImageMask', image='noise.png', channel='red'),
        'unet': n('UNETLoader', unet_name=MODELS['unet'], weight_dtype='default'),
        'clip': n('CLIPLoader', clip_name=MODELS['clip'], type='flux3', device='default'),
        'vae': n('VAELoader', vae_name=MODELS['vae']),
        'pos': n('CLIPTextEncode', text=prompt, clip=['clip', 0]),
        'neg': n('ConditioningZeroOut', conditioning=['pos', 0]),
        'ref': n('ReferenceMultiLatent', positive=['pos', 0], negative=['neg', 0], vae=['vae', 0],
                 resolution='keep resolution', **{'images.image0': ['img', 0]}),
        'enc': n('VAEEncode', pixels=['img', 0], vae=['vae', 0]),
        'region': n('SetLatentNoiseMask', samples=['enc', 0], mask=['mask', 0]),
        'sample': n('KSampler', model=['unet', 0], seed=seed, steps=16, cfg=1., sampler_name='euler',
                    scheduler='simple', denoise=1., positive=['ref', 0], negative=['ref', 1], latent_image=['region', 0]),
        'decode': n('VAEDecode', samples=['sample', 0], vae=['vae', 0]),
        'save': n('SaveImage', images=['decode', 0], filename_prefix='keyfx/set_dressing'),
    }


def _save(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2))


def workflow_differences(expected: dict, actual: dict) -> list:
    differences = []
    for node, spec in expected.items():
        if actual.get(node, {}).get('class_type') != spec['class_type']:
            differences.append([node, 'class_type'])
        for field, value in spec['inputs'].items():
            if (node, field) in [('img', 'image'), ('mask', 'image')]: continue
            if actual.get(node, {}).get('inputs', {}).get(field) != value:
                differences.append([node, field])
    return differences


def _render(g: dict, inputs: Path, out: Path, on_status=None) -> Path:
    """Persist a fixed idempotency key and job receipt; never retry an unknown submit."""
    receipt = out / 'cloud.json'; marker = out / 'submission_started.json'
    c = comfy.client()
    try:
        if receipt.exists():
            job = c.jobs.get(json.loads(receipt.read_text())['job_id'])
        else:
            if marker.exists():
                raise RuntimeError('Cloud submission outcome is unknown. Check this render before submitting again.')
            wf = c.workflows.from_json(g)
            for node, filename in [('img', 'source_padded.png'), ('mask', 'noise_padded.png')]:
                wf.set_input(node, 'image', c.assets.from_file(inputs / filename))
            key = str(uuid.uuid5(uuid.NAMESPACE_URL, str(out.resolve())))
            _save(marker, {'idempotency_key': key})
            job = c.submit(wf, idempotency_key=key)
            _save(receipt, {'job_id': job.id, 'status': 'submitted'})
        if on_status: on_status(job.id, 'running')
        job.result()
        files = list(job.get_outputs('save'))
        if len(files) != 1:
            raise RuntimeError('FLUX returned an unexpected number of stills.')
        raw = out / 'raw.png'; files[0].to_file(raw)
        actual = job.get_workflow()
        _save(out / 'executed_graph.json', {'format': actual.format, 'graph': actual.graph})
        differences = workflow_differences(g, actual.graph)
        _save(out / 'graph_verification.json', {'format': actual.format, 'non_upload_differences': differences})
        if actual.format != 'api' or differences:
            raise RuntimeError('The executed Cloud workflow differs from the saved object-edit request. Review this render before using it.')
        _save(receipt, {'job_id': job.id, 'status': 'done'})
        if on_status: on_status(job.id, 'done')
        return raw
    finally:
        c.close()


def build(source: Path, draft: dict, out: Path, on_status=None, model: str = 'flux3') -> Path:
    draft = validate(draft)
    if not draft['objects'] or any(not obj['text'] for obj in draft['objects']):
        raise ValueError('Draw and describe at least one object.')
    src = cv2.imread(str(source))
    if src is None: raise ValueError('The selected location image could not be read.')
    h, w = src.shape[:2]
    if min(w, h) < 32: raise ValueError('The location image is too small to edit.')
    noise, permission, protected, alpha = masks((w, h), draft)
    out.mkdir(parents=True, exist_ok=True)
    inputs = out / 'inputs'; inputs.mkdir(exist_ok=True)
    # Keep source detail in both model input and protected-pixel compositing.
    ww, wh = w, h
    pw, ph = math.ceil(ww / 32) * 32, math.ceil(wh / 32) * 32
    work = cv2.resize(src, (ww, wh), interpolation=cv2.INTER_AREA) if (ww, wh) != (w, h) else src.copy()
    nm = cv2.resize(noise, (ww, wh), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(inputs / 'source_original.png'), src)
    cv2.imwrite(str(inputs / 'source_padded.png'), cv2.copyMakeBorder(work, 0, ph - wh, 0, pw - ww, cv2.BORDER_REPLICATE))
    cv2.imwrite(str(inputs / 'noise_padded.png'), cv2.copyMakeBorder(nm, 0, ph - wh, 0, pw - ww, cv2.BORDER_CONSTANT))
    for name, mask in [('permission', permission), ('protected', protected)]:
        cv2.imwrite(str(inputs / f'{name}.png'), mask)
    np.save(inputs / 'blend.npy', alpha)
    text = caption(draft, (ww, wh), (pw, ph))
    g = image_models.graph(model, text, draft['seed'])
    (out / 'prompt.txt').write_text(text)
    _save(out / 'graph.json', g)
    _save(out / 'manifest.json', {'source_sha256': digest(source), 'source_size': [w, h],
                                'working_size': [ww, wh], 'canvas_size': [pw, ph], 'draft': draft, 'models': MODELS if model == 'flux3' else model})
    if model == 'flux3':
        raw_path = _render(g, inputs, out, on_status)
    else:
        # Every alternative gets the clean source and placement words; final
        # pixel permissions and protected areas are enforced identically.
        placement = ' '.join(f"Object {i+1}: x={o['box']['x0']:.1%}..{o['box']['x1']:.1%}, y={o['box']['y0']:.1%}..{o['box']['y1']:.1%}." for i,o in enumerate(draft['objects']))
        raw_path = image_models.edit(inputs / 'source_padded.png', text + ' ' + placement, draft['seed'], out / 'raw', model, inputs / 'noise_padded.png')
    raw = cv2.imread(str(raw_path))
    if raw is not None and model != 'flux3' and raw.shape[:2] != (ph, pw):
        if abs(raw.shape[1] / raw.shape[0] / (pw/ph) - 1) > .03:
            raise RuntimeError('The image editor returned an unexpected aspect ratio.')
        if raw.shape[1] < pw or raw.shape[0] < ph:
            raise RuntimeError('The image editor returned a lower-resolution canvas.')
        raw = cv2.resize(raw, (pw,ph), interpolation=cv2.INTER_AREA)
    if raw is None or raw.shape[:2] != (ph, pw):
        raise RuntimeError('FLUX returned an image with unexpected dimensions.')
    patch = raw[:wh, :ww]
    if (ww, wh) != (w, h): patch = cv2.resize(patch, (w, h), interpolation=cv2.INTER_LANCZOS4)
    result = np.rint(src * (1 - alpha[..., None]) + patch * alpha[..., None]).astype(np.uint8)
    diff = np.max(np.abs(result.astype(np.int16) - src.astype(np.int16)), axis=2)
    checks = {'protected_pixels_changed': int(np.count_nonzero(diff[protected > 0])),
              'outside_permission_pixels_changed': int(np.count_nonzero(diff[permission == 0])),
              'source_sha256': digest(source), 'source_unchanged': np.array_equal(src, cv2.imread(str(source)))}
    _save(out / 'verification.json', checks)
    if checks['protected_pixels_changed'] or checks['outside_permission_pixels_changed'] or not checks['source_unchanged']:
        raise RuntimeError('The source-pixel protection check failed; this result was not saved as a candidate.')
    dest = out / 'location.png'; cv2.imwrite(str(dest), result)
    return dest
