"""Bang On's per-element lifecycle and photographic-plate compositor."""
from __future__ import annotations
from pathlib import Path
import copy
import bisect
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

import cv2
import numpy as np

from . import connections, element_graphs, elements, geometry, media, native_colour, native_media, post_holdouts, matte_treatment, painted_guides, element_finishing

KINDS = {'explosion', 'fire', 'smoke_white', 'smoke_plume', 'smoke_plume_hq', 'smoke_grenade', 'wall_hit', 'dirt_hit'}
SMOKE_KINDS = {'smoke_white', 'smoke_plume', 'smoke_plume_hq', 'smoke_grenade'}
SIMULATION_REVISION = 'matte2d-depth3d-wind-dispersion-v3'
DEPTH_HOLDOUT_VISIBILITY_PROFILE = 'camera-depth-pixel-foreground-v3'
AUTHORED_BOX_COLLISION_PROFILE = 'conservative-voxel-overlap-v1'
SIMULATION_PROFILES = {'smoke_plume_hq':'squib-plume-hq-r18-v1', 'smoke_grenade':'bang-smoke-grenade-v1', 'fire':'squib-groundfire-v1'}
DEFAULT = {'duration_s': 2., 'cadence': 10, 'frame_blend': 10, 'guide_style': 'painted', 'guide_displacement': 0., 'guide_displacement_blur': 32., 'light_intensity': 1., 'light_rotation': 0., 'light_elevation': 60., 'blur': 2., 'shading': 1., 'seed': 42,
           'opacity': 1., 'gain': 1., 'light': 0., 'shadow': 0., 'depth_offset': 0., 'colliders': [], 'wind_dispersion': 1.,
           'density': 1., 'brightness': 1., 'smoke_dispersion': 0., 'fill_intensity': 1., 'rim_width': 16., 'rim_strength': 0., 'alpha_seed': 1234, **matte_treatment.DEFAULT, **element_finishing.DEFAULT}


def digest(data): return element_graphs.digest(data)


def enabled(p): return p.get('settings', {}).get('pipeline') == 'ltx_elements'


def effect_config(p, e):
    o = {**copy.deepcopy(DEFAULT), **copy.deepcopy(e.get('element', {}))}
    saved = e.get('element', {})
    # Existing takes retain the recipe they were actually rendered with. New
    # effects store DEFAULT explicitly; changing authoring defaults must not
    # silently repaint/reblend every previously approved element.
    if 'cadence' in saved and 'frame_blend' not in saved and 'guide_style' not in saved:
        for key, value in {'cadence': 1, 'frame_blend': 1, 'guide_style': 'rendered',
                           'light_elevation': 40. if e.get('preset') == 'smoke_plume_hq' else 60.}.items():
            if key not in saved: o[key] = value
    matte_treatment.policy(o)
    element_finishing.policy(o)
    for k, lo, hi in [('duration_s', .1, 5), ('blur', 0, 24), ('shading', .1, 4),
                      ('opacity', 0, 1), ('gain', 0, 4), ('light', 0, 2), ('shadow', 0, 1), ('depth_offset', -5, 5), ('wind_dispersion', 0, 2),
                      ('density', 0, 4), ('brightness', 0, 4), ('smoke_dispersion', 0, 2),
                      ('light_intensity', 0, 4), ('fill_intensity', 0, 2), ('rim_width', 1, 128), ('rim_strength', 0, 1),
                      ('light_rotation', -180, 180), ('light_elevation', -90, 90),
                      ('guide_displacement', 0, 100), ('guide_displacement_blur', 1, 128)]:
        if isinstance(o[k], bool): raise ValueError(f'Invalid element {k}.')
        v = float(o[k])
        if not np.isfinite(v) or not lo <= v <= hi: raise ValueError(f'Invalid element {k}.')
        o[k] = v
    for key, limit in [('cadence', 120), ('frame_blend', 48)]:
        if isinstance(o[key], bool) or not isinstance(o[key], int) or not 1 <= o[key] <= limit:
            raise ValueError(f'Choose an integer {key} between1and{limit}.')
    if o['guide_style'] not in ('painted', 'rendered'): raise ValueError('Choose painted or rendered guides.')
    if isinstance(o['seed'], bool) or not isinstance(o['seed'], int) or not 0 <= o['seed'] < 2**63: raise ValueError('Invalid element seed.')
    if isinstance(o['alpha_seed'], bool) or not isinstance(o['alpha_seed'], int) or not 0 <= o['alpha_seed'] < 2**63: raise ValueError('Invalid AlphaGen seed.')
    if (not isinstance(o['colliders'], list) or any(not isinstance(mid,str) for mid in o['colliders'])
            or len(o['colliders']) != len(set(o['colliders']))):
        raise ValueError('Choose unique named collider/holdout mattes.')
    ids = {m['id'] for m in p.get('geometry', {}).get('mattes', [])}
    if any(mid not in ids for mid in o['colliders']): raise ValueError('An assigned matte no longer exists.')
    if 'alpha_support' in o and (not isinstance(o['alpha_support'],str) or len(o['alpha_support'])!=12 or any(c not in '0123456789abcdef' for c in o['alpha_support'])):
        raise ValueError('Invalid local opacity-guide attachment.')
    return o


def source_profile(p):
    if p.get('native_profile'): return p['native_profile']
    # Explicit compatibility with old, already-normalized projects, not an
    # interpretation of their original uploads.
    from . import projects
    im = media.read_frame(projects.plate_dir(p), 0)
    n = p['frames']
    return {'width': im.shape[1], 'height': im.shape[0], 'frames': n, 'fps': 24., 'fps_rational': '24/1',
            'fps_num': 24, 'fps_den': 1, 'time_base': '1/24000', 'timestamps': [i/24 for i in range(n)],
            'source_pts': [i*1000 for i in range(n)], 'frame_durations_ticks': [1000]*n, 'duration': n/24,
            'storage_bit_depth': 8, 'bit_depth': 8, 'is_vfr': False,
            'colour': {'transfer': 'srgb', 'primaries': 'bt709', 'matrix': 'gbr', 'range': 'pc'}}


def uses_authored_box_collision(g, assigned):
    mode = g.get('collision_mode', 'depth3d' if g.get('depth') else 'matte2d')
    return mode == 'depth3d' and any(
        m['id'] in assigned and m.get('cameraBox') and m.get('kind', 'hard') == 'hard'
        and m.get('role', 'both') != 'holdout'
        and not any(m.get(key) for key in ('depthOffset', 'colliderBlur', 'colliderDisplacement'))
        for m in g.get('mattes', [])
    )


def generation_signature(p, e):
    from . import projects
    cfg = effect_config(p, e)
    g = geometry.validate(p.get('geometry', {}), p['frames'])
    g, simulation_ids = post_holdouts.simulation_geometry(g,cfg['colliders'])
    fields = {k: e.get(k) for k in ('id', 'preset', 'keys', 'anchors', 'size', 'angle', 'strength', 'tracking')}
    if projects.camera_locked(p):
        fields['camera_motion'] = 'locked-camera-v1'
    if uses_authored_box_collision(g, simulation_ids):
        fields['authored_box_collision'] = AUTHORED_BOX_COLLISION_PROFILE
    if e.get('preset') in SIMULATION_PROFILES:
        fields['simulation_profile'] = SIMULATION_PROFILES[e['preset']]
    if e.get('preset') == 'smoke_plume_hq' and any(
        k['phase'] == 'start' and k['frame'] < 0 and k.get('enabled') is not False
        for k in e.get('keys', [])
    ):
        fields['preroll_profile'] = 'hq-visible-emission-duration-v1'
    # Earlier 3D guides ignored a foreground holdout whose surface lay outside
    # the solver box. Invalidate only affected optical guides, not 2D/collider-
    # only caches or post-only mattes that do not bake visibility into guides.
    if g['collision_mode'] == 'depth3d' and any(
        m['id'] in simulation_ids and m.get('role', 'both') != 'collide'
        for m in g['mattes']
    ):
        fields['holdout_visibility_profile'] = DEPTH_HOLDOUT_VISIBILITY_PROFILE
    sim = {k: v for k, v in cfg.items() if k not in ('opacity', 'gain', 'light', 'shadow','alpha_support', *matte_treatment.FIELDS, *element_finishing.FIELDS)}
    if sim['alpha_seed'] == 1234: sim.pop('alpha_seed')
    # Defaults preserve the signatures of existing guides. Appearance affects
    # only smoke guides; these controls are not final-composite gain/opacity.
    for key in ('density', 'brightness'):
        if e['preset'] not in SMOKE_KINDS or sim[key] == 1: sim.pop(key)
    # Older smoke stored wind_dispersion=1 but never used it. A separate,
    # default-off setting adds grenade dispersion without changing old guides.
    if e['preset'] not in ('smoke_grenade', 'smoke_white') or sim['smoke_dispersion'] == 0:
        sim.pop('smoke_dispersion')
    sim['colliders'] = simulation_ids
    if sim['light_intensity'] == 1: sim.pop('light_intensity')
    if sim['fill_intensity'] == 1: sim.pop('fill_intensity')
    if sim['rim_strength'] == 0 or sim['guide_style'] != 'painted':
        sim.pop('rim_strength'); sim.pop('rim_width')
    else:
        sim['rim_processing_revision'] = painted_guides.RIM_REVISION
    if sim['light_rotation'] == 0: sim.pop('light_rotation')
    if sim['light_elevation'] == (40 if e['preset']=='smoke_plume_hq' else 60): sim.pop('light_elevation')
    if sim['guide_displacement'] == 0:
        sim.pop('guide_displacement'); sim.pop('guide_displacement_blur')
    if sim['frame_blend'] == 1: sim.pop('frame_blend')
    if sim['guide_style'] == 'rendered': sim.pop('guide_style')
    if any(k in sim for k in ('guide_style', 'frame_blend', 'guide_displacement')):
        sim['guide_processing_revision'] = painted_guides.REVISION
    model_profile=element_graphs.normalize_recipe({'models':p.get('settings',{}).get('element_models',{}),
                                                 'model_revisions':p.get('settings',{}).get('element_model_revisions',{})})
    return digest([SIMULATION_REVISION, fields, sim, g, p.get('native_profile', {}).get('source_sha256'),
                   model_profile['model_profile_hash']])


def plan(p, eid):
    e = next((e for e in p['effects'] if e['id'] == eid), None)
    if not e or e['preset'] not in KINDS: raise ValueError('Choose a smoke, fire, dust or explosion element supported by 3D simulation.')
    o = effect_config(p, e); profile = source_profile(p); times = profile['timestamps']
    # Match web/src/elementOptions.ts exactly: canonical onset wins even if
    # another legacy phase was retimed earlier; otherwise first enabled action.
    active = sorted((k for k in e['keys'] if k.get('enabled') is not False), key=lambda k:k['frame'])
    phase = 'onset' if e['preset']=='explosion' else 'ignite' if e['preset']=='fire' else 'start' if e['preset'].startswith('smoke_') else 'impact'
    key = next((k for k in active if k['phase']==phase), None)
    if key is None: key = next((k for k in active if k['phase']!='before'), None)
    if key is None: raise ValueError('Enable at least one effect phase before capturing this element.')
    onset = key['frame']
    start = max(0, min(p['frames']-1, int(onset)))
    duration = min(o['duration_s'], max(1/24, profile['duration']-times[start]))
    n = min(121, max(2, math.ceil(duration*24)))
    model = element_graphs.model_frames(n)
    scale = min(1024/profile['width'], 576/profile['height'], 1.)
    w, h = max(16, round(profile['width']*scale)), max(16, round(profile['height']*scale))
    source_frames = [int(np.argmin(np.abs(np.asarray(times)-(times[start]+k/24)))) for k in range(n)]
    return {'effect_id': eid, 'signature': generation_signature(p, e), 'start_frame': start,
            'onset_frame': onset, 'start_time': times[start], 'end_time': min(profile['duration'],times[start]+duration), 'source_times': times,
            'source_frames': source_frames, 'active_frames': n, 'model_frames': model,
            'width': w, 'height': h, 'fps': 24, 'settings': o,
            'source_profile': {'width': profile['width'], 'height': profile['height'], 'fps': profile['fps']}}


def model_index(spec, native_time):
    """Nearest generated frame only inside its explicit timeline interval."""
    elapsed = float(native_time) - spec['start_time']
    end_time = spec.get('end_time', spec['start_time']+spec['active_frames']/24)
    if elapsed < 0 or float(native_time) >= end_time-1e-9:
        return None
    return min(spec['active_frames'] - 1, int(math.floor(elapsed * 24 + .5)))


def _asset_path(root, value, *, directory=False):
    root = Path(root).resolve(); path = (root / value).resolve()
    valid = path.is_dir() if directory else path.is_file()
    if not path.is_relative_to(root) or path == root or not valid:
        raise ValueError('An immutable element input/output is missing or outside this project.')
    return path


def _post_names(n, alpha_core=False):
    kinds=('guide_frames','visibility','guide_alpha') if alpha_core else ('guide_frames','visibility')
    return {'guide.mp4'} | {f'{kind}/{f:05d}.png' for kind in kinds for f in range(n)}


def _freeze_post_inputs(rd, spec):
    assisted=bool(spec.get('alpha_core'))
    manifest = {'version': 2 if assisted else 1, 'signature': spec['signature'], 'active_frames': spec['active_frames'],
                'width': spec['width'], 'height': spec['height'],
                'files': {name: elements._sha(rd / name) for name in sorted(_post_names(spec['active_frames'],assisted))}}
    path = rd / 'manifest.json'; path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    return elements._sha(path)


def _verify_inputs(root, row):
    """Verify frozen request and geometric post assets before either model spend or composite."""
    try:
        return _read_inputs(root, row)
    except (OSError, TypeError, KeyError, elements.ElementError):
        raise ValueError('A frozen element input is missing or invalid; capture a new version.') from None


def _read_inputs(root, row):
    td = _asset_path(root, row['take_dir'], directory=True)
    take = elements.inspect(td)
    if (not row.get('request_sha256') or elements._sha(td / 'request.json') != row['request_sha256']
            or take.get('request_sha256') != row['request_sha256']):
        raise ValueError('The frozen element request changed; capture a new version.')
    spec = row['plan']; provenance = take['provenance']
    if provenance.get('plan') != spec or provenance.get('post_assets') != row['inputs']:
        raise ValueError('Element timing or geometric post assets changed; capture a new version.')
    rd = _asset_path(root, row['inputs'], directory=True)
    manifest_file = _asset_path(root, rd / 'manifest.json')
    expected = row.get('post_manifest_sha256')
    if (not expected or provenance.get('post_manifest_sha256') != expected
            or elements._sha(manifest_file) != expected):
        raise ValueError('The frozen visibility/guide manifest changed; capture a new version.')
    manifest = json.loads(manifest_file.read_text())
    if spec.get('alpha_core'):
        from . import alpha_core
        alpha_core.validate_policy(spec['alpha_core'])
    if (manifest.get('version')!=(2 if spec.get('alpha_core') else 1)
            or manifest.get('signature') != row['generation_signature'] or manifest.get('active_frames') != spec['active_frames']
            or (manifest.get('width'), manifest.get('height')) != (spec['width'], spec['height'])
            or set(manifest.get('files', {})) != _post_names(spec['active_frames'],bool(spec.get('alpha_core')))):
        raise ValueError('The frozen visibility/guide manifest does not match this element.')
    for name, sha in manifest['files'].items():
        if elements._sha(_asset_path(root, rd / name)) != sha:
            raise ValueError('A frozen geometric visibility/guide input changed; capture a new version.')
    for field in ('source_guide', 'guide'):
        entry = take[field]
        if elements._sha(_asset_path(root, entry['path'])) != entry['sha256']:
            raise ValueError('A frozen element guide changed; capture a new version.')
    return take


def _validated_take(root, row):
    try:
        return _read_completed(root, row)
    except (OSError, TypeError, KeyError, elements.ElementError):
        raise ValueError('A cached RGB/alpha asset is missing or changed; review the saved take before creating a new version.') from None


def _read_completed(root, row):
    take = _verify_inputs(root, row)
    if take.get('status') != 'done' or take.get('assets') != row.get('assets'):
        raise ValueError('Completed RGB/alpha do not match the saved element take.')
    for stage in ('rgb','alpha'):
        _read_stage(root,row,take,stage)
    return take


def _read_stage(root, row, take, stage):
    if stage not in ('rgb','alpha') or not take.get('assets',{}).get(stage) or take.get('stages',{}).get(stage,{}).get('status')!='done':
        raise ValueError('This element stage has no verified completed asset.')
    asset=take['assets'][stage]
    if row.get('assets',{}).get(stage) is not None and row['assets'][stage]!=asset:
        raise ValueError('The saved element stage no longer matches its verified asset.')
    spec, g = row['plan'], take['geometry']
    x, y, w, h = g['content_rect']
    if (w, h) != (spec['width'], spec['height']) or min(x, y) < 0 or x+w > g['width'] or y+h > g['height']:
        raise ValueError('The element content window differs from its frozen guide.')
    for field in ('path', 'frame_manifest'):
        _asset_path(root, asset[field])
    fd = _asset_path(root, asset['frames_dir'], directory=True)
    manifest = json.loads(Path(asset['frame_manifest']).read_text())
    expected = {str((fd/f'{k:05d}.png').resolve()) for k in range(spec['active_frames'])}
    if (set(manifest) != expected or asset['frames'] != spec['active_frames']
            or asset['model_frames'] != spec['model_frames'] or abs(asset['fps']-24) > .01
            or (asset['width'], asset['height']) != (g['width'], g['height'])):
        raise ValueError('The completed element frame sequence differs from its frozen recipe.')
    elements._verify_asset(asset)
    if ((stage=='alpha' and (asset.get('colour_space')!='linear_data' or asset.get('colour_association')!='opacity'))
            or (stage=='rgb' and (asset.get('colour_space')!='srgb' or asset.get('colour_association') not in ('black_premultiplied','straight')))):
        raise ValueError('Element colour/alpha association is unsupported or changed.')
    return copy.deepcopy(asset)


def validated_stage(root, row, stage):
    """A completed RGB remains reviewable even if the subsequent alpha failed."""
    try:
        return _read_stage(root,row,_verify_inputs(root,row),stage)
    except (OSError,TypeError,KeyError,elements.ElementError):
        raise ValueError('A saved element stage is missing or changed; inspect the cached take before a new attempt.') from None


def _partial_assets(root, row):
    result={}
    try:take=_verify_inputs(root,row)
    except (ValueError,OSError,TypeError,KeyError,elements.ElementError):return result
    for stage in ('rgb','alpha'):
        try:result[stage]=_read_stage(root,row,take,stage)
        except (ValueError,OSError,TypeError,KeyError,elements.ElementError):pass
    return result


def _recovery_candidates(root,row,completed):
    candidates=[]
    try:take=_verify_inputs(root,row)
    except (ValueError,OSError,TypeError,KeyError,elements.ElementError):return candidates
    if take.get('status')!='failed':return candidates
    for stage in ('rgb','alpha'):
        if stage in completed:continue
        try:candidates.append(elements.downloaded_stage_candidate(Path(row['take_dir']),stage))
        except (ValueError,OSError,TypeError,KeyError,elements.ElementError):pass
    return candidates


def backend_current(pid, row, *, identity_provider=None):
    """Prepared work is provider-bound; completed local assets need no connection.

    Only a boolean crosses the HTTP boundary. Read the frozen backend rather
    than attaching account fingerprints or secrets to a public take record.
    """
    if row.get('status') != 'prepared':
        return True
    try:
        from . import projects
        td = _asset_path(projects.pdir(pid),row['take_dir'],directory=True)
        take = elements.inspect(td)
        if take.get('status') == 'done':
            return True
        if elements._sha(td/'request.json') != row['request_sha256']:
            return False
        frozen = take['backend']
        # An explicitly unbound recipe can adopt the selected executor. App
        # prepare requests always freeze a Connection, including disconnected.
        if frozen is None:
            return True
        identity = identity_provider() if identity_provider else elements._connection_identity(connections.current())
        return frozen == identity
    except (OSError, ValueError, TypeError, KeyError):
        return False


def records(p):
    out = []
    identity_cache = []
    def identity():
        if not identity_cache:
            try: value = elements._connection_identity(connections.current())
            except ValueError: value = None
            identity_cache.append(value)
        return identity_cache[0]
    for row in p.get('element_takes', []):
        e = next((e for e in p['effects'] if e['id'] == row['effect_id']), None)
        item = copy.deepcopy(row)
        if row.get('status')=='error':
            from . import projects
            item['assets']=_partial_assets(projects.pdir(p['id']),row)
            item['recoverable_stages']=_recovery_candidates(projects.pdir(p['id']),row,item['assets'])
        item['current'] = bool(e and row['generation_signature'] == generation_signature(p, e))
        pinned = p.get('element_approvals', {}).get(row['effect_id']) == row['id']
        item['approved'] = bool(pinned and item['current'] and row['status'] == 'done')
        item['needs_alpha_core']=False  # Raw-matte treatment also works on legacy completed takes.
        item['approval_outdated'] = bool(pinned and not item['approved'])
        item['backend_current'] = backend_current(p['id'],row,identity_provider=identity)
        item.pop('take_dir', None)
        for asset in item.get('assets', {}).values():
            if isinstance(asset, dict):
                asset.pop('path', None); asset.pop('frames_dir', None); asset.pop('frame_manifest', None)
        out.append(item)
    return out


def selected_take(p, e):
    """Pin reviewed output; an outdated approval must never silently swap takes."""
    signature = generation_signature(p, e)
    approved = p.get('element_approvals', {}).get(e['id'])
    if approved:
        row = next((r for r in p.get('element_takes', []) if r['id'] == approved and r['effect_id'] == e['id']), None)
        if not row or row.get('status') != 'done' or row.get('generation_signature') != signature:
            raise ValueError('An approved element changed. Review and approve a current version before compositing.')
        return row
    return next((r for r in p.get('element_takes', []) if r['effect_id'] == e['id']
                 and r.get('status') == 'done' and r.get('generation_signature') == signature), None)


def approve(pid, take_id):
    """Approve one completed immutable version, without generating anything."""
    from . import projects
    with projects._lock(pid):
        p = projects.load(pid)
        if projects.generation_active(p):
            raise ValueError('Wait for the current generation before approving an element.')
        row = next((r for r in p.get('element_takes', []) if r['id'] == take_id), None)
        if not row or row.get('status') != 'done':
            raise ValueError('Complete this element RGB and alpha before approving it.')
        e = next((e for e in p['effects'] if e['id'] == row['effect_id']), None)
        if not e or row.get('generation_signature') != generation_signature(p, e):
            raise ValueError('This element version is outdated. Generate and review a current version before approving it.')
        _validated_take(projects.pdir(pid), row)
        p.setdefault('element_approvals', {})[e['id']] = row['id']
        projects.save(p)
        return next(r for r in records(p) if r['id'] == take_id)


def new_version(pid, effect_id):
    """Explicit local reseed of only one element; capture/render is still separate."""
    from . import projects
    with projects._lock(pid):
        p = projects.load(pid)
        if projects.generation_active(p):
            raise ValueError('Wait for the current generation before making a new element version.')
        e = next((e for e in p['effects'] if e['id'] == effect_id), None)
        if not e or e.get('preset') not in KINDS:
            raise ValueError('Choose an LTX element before making a new version.')
        cfg = effect_config(p, e)
        used = {cfg['seed']}
        used.update(r.get('plan', {}).get('settings', {}).get('seed') for r in p.get('element_takes', [])
                    if r['effect_id'] == effect_id)
        # Remain exactly representable in browser JSON/number inputs.
        seed = uuid.uuid4().int & 0xffffffff
        while seed in used:
            seed = uuid.uuid4().int & 0xffffffff
        e['element'] = {**cfg, 'seed': seed}
        e['element'].pop('alpha_support',None)
        p.get('element_approvals', {}).pop(effect_id, None)
        spec = plan(p, effect_id)
        projects.save(p)
        return {'effect_id': effect_id, 'seed': seed, 'plan': spec}


def _recipe(p, e, spec):
    prompts = {'explosion': 'one isolated explosive fireball expands outward then rises with darker curling smoke',
               'smoke_white': 'broad pale smoke from a recently discharged smoke grenade rapidly expands and rolls around obstacles',
               'smoke_plume': 'dense shaded black smoke billows and curls upward',
               'smoke_plume_hq': 'a continuous neutral grey smoke source feeds a warm rising plume, with broad curling billows and persistent fine density detail',
               'smoke_grenade': 'dense pale smoke rapidly expands low and wide from a fixed smoke grenade source and rolls around obstacles, smoke only with no flame or fireball',
               'wall_hit': 'a short localized impact ejects dusty plaster chips and a fine expanding dust cloud',
               'dirt_hit': 'a ground impact kicks up shaded dirt and dust, then it settles',
               'fire': 'a continuous ground fire catches and keeps burning with tall flickering orange flames, fed by a fixed source and releasing wisps of smoke, with no explosive fireball'}
    subject = prompts[e['preset']]
    return {'active_frames': spec['active_frames'], 'seed': spec['settings']['seed'], 'alpha_seed': spec['settings'].get('alpha_seed', 1234),
            'start_frame': spec['start_frame'], 'start_time_seconds': spec['start_time'],
            'source_fps': source_profile(p)['fps_rational'], 'source_window': spec['source_frames'],
            'prompt': ('ainvfxfluid, an isolated VFX element on a uniformly pure black background: '+subject+'. '
                       'The supplied painted video is the authority for the effect silhouette, screen position, scale, timing and lighting. '
                       'Replace its flat painted regions with natural volumetric detail while retaining those guided shapes and motion. '
                       'The frame contains only this effect surrounded by empty black. The camera stays locked for one continuous shot.')
                      + painted_guides.lighting_prompt(spec['settings'],base_azimuth=120 if e['preset']=='smoke_plume_hq' else -25),
            'negative_prompt': ('blurry, low quality, distorted, watermark, text, people, person, human, cyclist, bicycle, '
                                'vehicles, aircraft, airplane, furniture, room, architecture, trees, forest, scenery, landscape'),
            'models': p.get('settings', {}).get('element_models', {}),
            'model_revisions': p.get('settings', {}).get('element_model_revisions', {}),
            'colour_association': 'black_premultiplied'}


def prepare_upload(pid, eid, meta, images, visibility, guide_alpha=None):
    from . import projects
    with projects._lock(pid):
        p = projects.load(pid)
        if projects.generation_active(p): raise ValueError('Wait for the current generation before preparing an element.')
        spec = plan(p, eid)
        if meta.get('signature') != spec['signature']: raise ValueError('Element or geometry changed; capture its guide again.')
        if 'source_depth' in meta:
            depth=meta['source_depth']
            if isinstance(depth,bool) or not isinstance(depth,(int,float)) or not math.isfinite(depth) or not .001<=depth<=1e6:
                raise ValueError('The captured effect source depth is invalid; capture its guide again.')
            spec['source_depth']=float(depth)
        n = spec['active_frames']
        if set(images) != set(range(n)) or set(visibility) != set(range(n)):
            raise ValueError('Capture every guide and geometric visibility frame before submitting.')
        if painted_guides.rim_enabled(spec['settings']) and guide_alpha is None:
            raise ValueError('Capture simulated alpha with the rim guide so dark smoke retains its silhouette.')
        e = next(e for e in p['effects'] if e['id'] == eid)
        if guide_alpha is not None:
            if set(guide_alpha)!=set(range(n)):
                raise ValueError('Original body support needs every simulated alpha frame, including sparse guide slots.')
            from . import alpha_core
            spec['alpha_core']=alpha_core.policy()
        rid = uuid.uuid4().hex[:12]; root = projects.pdir(pid); rd = root/'element_inputs'/rid
        (rd/'guide_frames').mkdir(parents=True); (rd/'visibility').mkdir()
        (rd/'painted_frames').mkdir(); (rd/'raw_guide_frames').mkdir()
        if guide_alpha is not None:(rd/'guide_alpha').mkdir()
        nonempty = False
        for f in range(n):
            im = cv2.imdecode(np.frombuffer(images[f], np.uint8), cv2.IMREAD_COLOR)
            vis = cv2.imdecode(np.frombuffer(visibility[f], np.uint8), cv2.IMREAD_GRAYSCALE)
            if im is None or vis is None or im.shape[:2] != (spec['height'], spec['width']) or vis.shape != im.shape[:2]:
                raise ValueError('Guide/visibility canvas differs from this element plan.')
            if guide_alpha is not None:
                data=cv2.imdecode(np.frombuffer(guide_alpha[f],np.uint8),cv2.IMREAD_UNCHANGED)
                if data is None or data.dtype!=np.uint8 or data.shape[:2]!=(spec['height'],spec['width']):
                    raise ValueError('Simulated alpha differs from this element plan.')
                if data.ndim==3:
                    if data.shape[2] not in (3,4) or not np.array_equal(data[...,0],data[...,1]) or not np.array_equal(data[...,0],data[...,2]):
                        raise ValueError('Simulated alpha must contain grayscale data, not coloured guide RGB.')
                    data=data[...,0]
                if data.ndim!=2 or not cv2.imwrite(str(rd/'guide_alpha'/f'{f:05d}.png'),data):
                    raise ValueError('Could not preserve the simulated explosion alpha.')
            raw = im
            im = painted_guides.paint(im, spec['settings'], support=data if guide_alpha is not None else None,
                                     base_azimuth=120 if e['preset']=='smoke_plume_hq' else -25)
            if (not cv2.imwrite(str(rd/'raw_guide_frames'/f'{f:05d}.png'), raw)
                    or not cv2.imwrite(str(rd/'painted_frames'/f'{f:05d}.png'), im)
                    or not cv2.imwrite(str(rd/'visibility'/f'{f:05d}.png'), vis)):
                raise ValueError('Could not preserve the isolated guide and geometric visibility.')
        # Blend the complete sequence in linear light BEFORE sparse sampling.
        # The rolling sum is bounded in memory and excludes black placeholders.
        displace = painted_guides.Displacement(projects.pdir(pid), p, e, spec)
        for f, im in enumerate(painted_guides.blend(rd/'painted_frames', n, spec['settings']['frame_blend'])):
            im = displace.apply(im, f)
            im = painted_guides.finish(im, spec['settings'], base_azimuth=120 if e['preset']=='smoke_plume_hq' else -25)
            if f not in (0, n-1) and f % spec['settings']['cadence']: im[:] = 0
            nonempty |= bool(im.any())
            if not cv2.imwrite(str(rd/'guide_frames'/f'{f:05d}.png'), im):
                raise ValueError('Could not preserve the processed guide.')
        if not nonempty:
            shutil.rmtree(rd)
            help_text = ('Check smoke density/brightness, origin/depth and holdout placement'
                         if e['preset'] in SMOKE_KINDS else 'Move the origin/depth or review collision placement')
            raise ValueError(f'The processed isolated guide is entirely black. {help_text}, then capture again; no model generation was prepared.')
        subprocess.run(['ffmpeg','-v','error','-y','-framerate','24','-i',str(rd/'guide_frames/%05d.png'),
                        '-c:v','libx264','-qp','0','-pix_fmt','yuv444p',str(rd/'guide.mp4')], check=True)
        post_manifest_sha = _freeze_post_inputs(rd, spec)
        provenance = {'kind':'isolated_simulation_guide','contains_plate':False,'simulation':SIMULATION_PROFILES.get(e['preset'],'finite_depth_volume_v1'),
                      'simulation_revision':SIMULATION_REVISION,
                      'generation_signature':spec['signature'],'geometry':copy.deepcopy(p.get('geometry',{})),
                      'guide_processing':painted_guides.recipe(spec['settings']),
                      'guide_space':'source_camera; no second homography during composite',
                      'post_assets':str(rd.relative_to(root)), 'post_manifest_sha256':post_manifest_sha, 'plan':spec}
        take = elements.prepare(root/'elements', eid, rd/'guide.mp4', _recipe(p,e,spec), provenance=provenance,
                                connection=connections.current())
        row = {'id':take['id'],'effect_id':eid,'generation_signature':spec['signature'],'status':take['status'],
               'take_dir':take['take_dir'],'inputs':str(rd.relative_to(root)),'plan':spec,
               'request_sha256':take['request_sha256'], 'post_manifest_sha256':post_manifest_sha,
               'created':time.strftime('%Y-%m-%d %H:%M:%S')}
        p.setdefault('element_takes',[])[:] = [x for x in p.get('element_takes',[]) if x['id'] != row['id']]
        p['element_takes'].insert(0,row); projects.save(p)
    return row


def start(pid, take_id, *, skip_if_approved=False):
    from . import projects
    with projects._lock(pid):
        p=projects.load(pid)
        if projects.generation_active(p): raise ValueError('Wait for the current generation.')
        row=next((x for x in p.get('element_takes',[]) if x['id']==take_id),None)
        if not row: raise ValueError('Prepare this element first.')
        e=next((x for x in p['effects'] if x['id']==row['effect_id']),None)
        if e and skip_if_approved:
            pin=p.get('element_approvals',{}).get(e['id'])
            approved=next((r for r in p.get('element_takes',[]) if r['id']==pin and r['effect_id']==e['id']
                           and r.get('status')=='done' and r.get('generation_signature')==generation_signature(p,e)),None)
            if approved:
                _validated_take(projects.pdir(pid),approved)
                return {'skipped_approved':True,'take_id':approved['id'],'effect_id':e['id']}
        if not e or row['generation_signature']!=generation_signature(p,e): raise ValueError('This prepared element is outdated.')
        if row['status']=='done':
            _validated_take(projects.pdir(pid), row)
            return row
        if row['status'] not in ('prepared',): raise ValueError('This submission already ran or failed; explicitly capture a new version to retry.')
        _verify_inputs(projects.pdir(pid), row)
        if not backend_current(pid,row):
            raise ValueError('The selected Comfy account/target changed. Prepare a new guide version for this connection before generating.')
        rid=uuid.uuid4().hex[:12]
        r={'id':rid,'kind':'element','element_id':e['id'],'status':'queued','files':{},'seed':effect_config(p,e)['seed'],
           'strength':'','pid':os.getpid(),'created':time.strftime('%Y-%m-%d %H:%M:%S')}
        p['renders'].insert(0,r); row.update(status='queued',render_id=rid); projects.save(p)
    def worker():
        try:
            published={}
            def status(value):
                phase=value if isinstance(value,str) else ('alpha' if 'rgb' in value.get('assets',{}) else 'rgb')
                if isinstance(value,dict) and value.get('status')=='done': phase='validating'
                completed={}
                if isinstance(value,dict):
                    for stage,asset in value.get('assets',{}).items():
                        if stage in ('rgb','alpha') and published.get(stage)!=asset.get('sha256'):
                            try:
                                completed[stage]=validated_stage(projects.pdir(pid),row,stage)
                                published[stage]=completed[stage]['sha256']
                            except ValueError:pass
                def progress(q):
                    next(x for x in q['renders'] if x['id']==rid).update(status=phase)
                    target=next(x for x in q['element_takes'] if x['id']==take_id);target.update(status=phase)
                    if completed:target.setdefault('assets',{}).update(completed)
                projects.update(pid,progress)
            result=elements.run(Path(row['take_dir']),on_status=status)
            def keep(q):
                target=next(x for x in q['element_takes'] if x['id']==take_id)
                target.update(status='done',assets=result['assets'])
                next(x for x in q['renders'] if x['id']==rid).update(status='done',element_take=take_id)
            projects.update(pid,keep)
        except Exception as exc:
            completed=_partial_assets(projects.pdir(pid),row)
            projects.update(pid,lambda q:[x.update(status='error',error=str(exc)[:500],assets=completed) for x in q.get('element_takes',[]) if x['id']==take_id])
            projects._set(pid,rid,status='error',error=f'{type(exc).__name__}: {exc}'[:500])
    threading.Thread(target=worker,daemon=True).start()
    return r


def _light(linear_rgb, visibility, strength, shadow_strength):
    """Separate approximate receiver light/shadow, never model photo texture."""
    if strength == 0 and shadow_strength == 0:
        return None, None
    h,w=linear_rgb.shape[:2]
    hot=linear_rgb.mean(2)
    field=cv2.GaussianBlur(hot,(0,0),max(2,w*.10))
    light=field[...,None]*np.array([1.,.38,.08])*strength if strength else None
    trans=1-np.clip((1-visibility)*field*shadow_strength,0,.8) if shadow_strength else None
    return light,trans


def _resize_shadow(shadow, size):
    """Resample zero-anchored attenuation so neutral transmittance stays1.

    OpenCV's bilinear coefficients can sum slightly above/below1 when resizing
    float64 transmittance. That can make neutral1 invalid or cast a tiny false
    shadow outside support. Zero attenuation stays exact through interpolation.
    """
    transmittance=np.asarray(shadow,dtype=np.float64)
    if transmittance.ndim!=2 or not np.isfinite(transmittance).all() or np.any((transmittance<0)|(transmittance>1)):
        raise native_colour.NativeColourError('Shadow source must be finite transmittance[0,1].')
    return 1-np.clip(cv2.resize(1-transmittance,size),0,1)


def _validate_optical_assets(pid, p, e):
    for matte in p.get('geometry',{}).get('mattes',[]):
        if matte['id'] in effect_config(p,e)['colliders'] and matte.get('holdout') and matte.get('holdoutPost') and matte.get('role')!='collide':
            from . import people_alpha
            people_alpha.validated_asset(pid,matte['holdout']['take_id'])


def element_frame_layers(p, e, row, take, profile, optical, f):
    """Native-sized linear709 element and independent receiver auxiliaries.

    RGB remains premultiplied/emissive data, including nonzero radiance where
    alpha is zero. Alpha is linear coverage. Native post holdouts apply once.
    This boundary is shared by compositing and layered export.
    """
    from . import projects
    root=projects.pdir(p['id'])
    k=model_index(row['plan'],profile['timestamps'][f])
    if k is None:return None
    spec=row['plan'];assets=take['assets'];x,y,w,h=take['geometry']['content_rect']
    rgb=cv2.imread(str(Path(assets['rgb']['frames_dir'])/f'{k:05d}.png'))[y:y+h,x:x+w]
    a=cv2.imread(str(Path(assets['alpha']['frames_dir'])/f'{k:05d}.png'),cv2.IMREAD_GRAYSCALE)[y:y+h,x:x+w].astype(np.float32)/255
    vis=cv2.imread(str(root/row['inputs']/'visibility'/f'{k:05d}.png'),cv2.IMREAD_GRAYSCALE).astype(np.float32)/255
    opts=effect_config(p,e);vis=cv2.resize(vis,(w,h))
    rgb=cv2.cvtColor(rgb,cv2.COLOR_BGR2RGB).astype(np.float32)/255
    vis=element_finishing.soften_visibility(vis,opts['holdout_blur'],w/profile['width'])
    if opts['body_matte']:
        if not spec.get('alpha_core'):
            raise ValueError('Original body needs captured simulation alpha. Prepare a new element guide or turn Original body off.')
        if assets['rgb']['colour_association']!='black_premultiplied':
            raise ValueError('Original body blending requires a black-premultiplied LTX element.')
        guide=cv2.imread(str(root/row['inputs']/'guide_alpha'/f'{k:05d}.png'),cv2.IMREAD_GRAYSCALE)
        if guide is None or guide.shape!=(h,w):raise ValueError('Original body alpha is missing or has the wrong canvas.')
        holder=optical[e['id']]
        if not hasattr(holder,'_body_masks'):holder._body_masks=element_finishing.BodyMasks(p,e,take,w,h)
        protected=holder._body_masks.pixels(f)
        rgb,a=element_finishing.blend_layers(native_colour.srgb_to_linear(rgb),a,guide.astype(np.float32)/255,vis,opts,protected)
        rgb*=opts['gain']*opts['opacity'];a*=opts['opacity']
    else:
        a=matte_treatment.apply(a,e.get('element',{}))
        factor=vis*opts['opacity']
        if assets['rgb']['colour_association']=='straight':factor=factor*a
        rgb=native_colour.srgb_to_linear(rgb)*opts['gain']*factor[...,None]
        a*=vis*opts['opacity']
    light,shadow=_light(rgb,vis,opts['light'],opts['shadow'])
    size=(profile['width'],profile['height']);rgb=cv2.resize(rgb,size);a=cv2.resize(a,size);vis=cv2.resize(vis,size)
    post_visibility=optical[e['id']].visibility(f)
    if post_visibility is not None:
        post_visibility=element_finishing.soften_visibility(post_visibility,opts['holdout_blur'])
        rgb*=post_visibility[...,None];a*=post_visibility
    if light is not None:light=cv2.resize(light,size)
    if shadow is not None:shadow=_resize_shadow(shadow,size)
    bloom=element_finishing.glow(rgb,opts['ltx_glow'])
    if bloom is not None:light=bloom if light is None else light+bloom
    return {'rgb':rgb,'alpha':a,'light':light,'shadow':shadow,'visibility':vis}


def _composite_frame(p, selected, profile, optical, f):
    """One native photographic frame, shared by review and final composition."""
    from . import projects
    root=projects.pdir(p['id'])
    if p.get('native_profile'):base=native_media.read_native_frame(root/'native',f)
    else:base=cv2.cvtColor(cv2.imread(str(media.frame_path(projects.plate_dir(p),f))),cv2.COLOR_BGR2RGB)
    from . import lighting_integration
    delta=lighting_integration.signed_layer(p,f,profile)
    if delta is not None:base=native_colour.composite_native(base,profile,light_rgb=delta)
    for e,row,take in selected:
        layer=element_frame_layers(p,e,row,take,profile,optical,f)
        if layer is None:continue
        rgb=native_colour.encode(layer['rgb'],{'colour':{'transfer':'srgb','primaries':'bt709'}})
        base=native_colour.composite_native(base,profile,rgb,layer['alpha'],premultiplied=True,light_rgb=layer['light'],shadow=layer['shadow'])
    return base


def _review_state(p, take_id):
    """Validate one current completed take without selecting/approving others."""
    from . import projects
    row=next((r for r in p.get('element_takes',[]) if r['id']==take_id),None)
    e=next((e for e in p['effects'] if row and e['id']==row['effect_id']),None)
    if not enabled(p) or not row or row.get('status')!='done':
        raise ValueError('Complete this element RGB and alpha before reviewing it over the plate.')
    if not e or row['generation_signature']!=generation_signature(p,e):
        raise ValueError('This element guide is outdated; review a current completed version.')
    _validate_optical_assets(p['id'],p,e)
    return e,row,_validated_take(projects.pdir(p['id']),row)


def _review_plan(p, take_id):
    from . import projects
    e,row,take=_review_state(p,take_id);profile=source_profile(p);times=profile['timestamps']
    duration=float(profile['duration'])
    if not math.isfinite(duration) or not 0<duration<=60.000001:
        raise ValueError('Element reviews support source shots up to60seconds.')
    if len(times)!=p['frames'] or not times or any(not math.isfinite(t) for t in times) or any(b<=a for a,b in zip(times,times[1:])):
        raise ValueError('The source timeline is incomplete.')
    count=max(1,math.ceil(duration*24-1e-8));indices=[]
    for i in range(count):
        t=i/24;k=min(bisect.bisect_left(times,t),len(times)-1)
        if k and t-times[k-1]<=times[k]-t:k-=1
        indices.append(k)
    factor=min(1,960/max(profile['width'],profile['height']))
    width,height=[max(2,round(profile[k]*factor/2)*2) for k in ('width','height')]
    root=projects.pdir(p['id']);sources={}
    directory=native_media._frames_dir(root/'native') if p.get('native_profile') else projects.plate_dir(p)
    for f in sorted(set(indices)):
        path=_asset_path(root,media.frame_path(directory,f));sources[str(path.relative_to(root.resolve()))]=elements._sha(path)
    if p.get('native_profile'):
        original=root/'native'/profile.get('source_file','source.mp4')
        if original.is_file():sources[str(original.relative_to(root))]=elements._sha(original)
    frozen={'revision':'selected-element-native-review-v2','effect':e,'matte_treatment':matte_treatment.policy(e.get('element',{})),'take_id':take_id,
            'generation_signature':row['generation_signature'],'request_sha256':row['request_sha256'],
            'post_manifest_sha256':row['post_manifest_sha256'],'geometry':p.get('geometry'),
            'profile':profile,'sources':sources,'assets':{stage:take['assets'][stage]['sha256'] for stage in ('rgb','alpha')}}
    signature=digest(frozen)
    spec={'signature':signature,'effect_id':e['id'],'take_id':take_id,'frames':count,'fps':24,'duration':count/24,'source_duration':duration,
          'width':width,'height':height,'source_frames':indices,'selected_only':True,'photographic_master':False,
          'description':'SDR review: original plate plus this completed element only. Does not approve or build a final take.'}
    return spec,frozen,profile,(e,row,take)


def review_over_plate(pid, take_id):
    """Free selected-take review using the same native composite as final export.

    Independent cache only: no project/take/approval writes or provider calls.
    Source/asset fingerprints are rechecked before publishing the viewing copy.
    """
    from . import projects
    p=projects.load(pid);spec,frozen,profile,selected=_review_plan(p,take_id);root=projects.pdir(pid)
    cache=root/'element_reviews'/take_id;cache.mkdir(parents=True,exist_ok=True);dest=cache/spec['signature']
    url=f'/api/projects/{pid}/element-takes/{take_id}/reviews/{spec["signature"]}/review.mp4'
    manifest=dest/'review.json';video=dest/'review.mp4'
    if manifest.is_file() and video.is_file():
        try:saved=json.loads(manifest.read_text())
        except (OSError,ValueError):saved={}
        if saved.get('signature')==spec['signature'] and saved.get('video_sha256')==elements._sha(video):
            return {**spec,'url':url,'cached':True}
    with tempfile.TemporaryDirectory(prefix='.review-',dir=cache) as td:
        temporary=Path(td);frames=temporary/'frames';frames.mkdir()
        e,row,take=selected
        optical={e['id']:post_holdouts.NativePostHoldouts(p,e,profile,source_depth=row['plan'].get('source_depth'))}
        for i,f in enumerate(spec['source_frames']):
            native=_composite_frame(p,[selected],profile,optical,f)
            display=native_colour.display_frame(native,profile,max_size=(spec['width'],spec['height']))
            if display.shape[:2]!=(spec['height'],spec['width']):display=cv2.resize(display,(spec['width'],spec['height']),interpolation=cv2.INTER_AREA)
            if not cv2.imwrite(str(frames/f'{i:05d}.png'),cv2.cvtColor(display,cv2.COLOR_RGB2BGR)):
                raise ValueError('Could not preserve local review frames.')
        audio=root/'native'/profile.get('source_file','source.mp4') if p.get('native_profile') else None
        args=['ffmpeg','-v','error','-y','-framerate','24','-i',str(frames/'%05d.png')]
        if audio is not None and audio.is_file():args+=['-seek_timestamp','1','-ss',str(profile.get('trim_actual_start_s',p.get('start_s',0))),'-t',str(spec['source_duration']),'-i',str(audio)]
        args+=['-map','0:v:0']
        args+=['-map','1:a:0?','-c:a','aac','-b:a','192k'] if audio is not None and audio.is_file() else ['-an']
        args+=['-frames:v',str(spec['frames']),'-vf','scale=in_range=pc:out_range=tv:out_color_matrix=bt709:flags=accurate_rnd+full_chroma_int,setsar=1',
               '-c:v','libx264','-crf','16','-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','iec61966-2-1',
               '-colorspace','bt709','-color_range','tv','-t',str(spec['duration']),'-movflags','+faststart',str(temporary/'review.mp4')]
        result=subprocess.run(args,capture_output=True,text=True,timeout=180)
        if result.returncode:raise ValueError('Could not encode the local element review: '+result.stderr[-1000:])
        actual=media.probe(temporary/'review.mp4')
        if (actual['frames'],actual['width'],actual['height'])!=(spec['frames'],spec['width'],spec['height']):
            raise ValueError('The local review failed frame-count/canvas verification.')
        current,_,_,_=_review_plan(projects.load(pid),take_id)
        if current['signature']!=spec['signature']:raise ValueError('The element or source changed during review. Build a new local review.')
        record={**spec,'inputs':frozen,'video_sha256':elements._sha(temporary/'review.mp4')}
        (temporary/'review.json').write_text(json.dumps(record,indent=2)+'\n')
        dest.mkdir(parents=True,exist_ok=True)
        os.replace(temporary/'review.mp4',video);os.replace(temporary/'review.json',manifest)
    return {**spec,'url':url,'cached':False}


def recover_downloaded(pid,take_id,stage,expected_raw_sha256):
    """Explicit local validation repair; never submits or approves a stage."""
    from . import projects
    with projects._lock(pid):
        p=projects.load(pid)
        if projects.generation_active(p):raise ValueError('Wait for the current generation before recovering a downloaded output.')
        row=next((r for r in p.get('element_takes',[]) if r['id']==take_id),None)
        if not row:raise ValueError('The saved element take was not found.')
        _verify_inputs(projects.pdir(pid),row)
        previous=row.get('local_recovery',{})
        if row.get('status')=='done' and previous.get('stage')==stage and previous.get('raw_sha256')==expected_raw_sha256:
            _validated_take(projects.pdir(pid),row)
            return next(r for r in records(p) if r['id']==take_id)
        result=elements.recover_downloaded_stage(Path(row['take_dir']),stage,expected_raw_sha256=expected_raw_sha256)
        assets=_partial_assets(projects.pdir(pid),row)
        complete=result.get('status')=='done' and {'rgb','alpha'}<=set(assets)
        updated={**row,'status':'done' if complete else 'prepared','assets':assets}
        if complete:_validated_take(projects.pdir(pid),updated)
        row.update(updated);row.pop('error',None)
        row['local_recovery']={'stage':stage,'raw_sha256':expected_raw_sha256,'new_inference_submissions':0,'recovered_at':time.time()}
        render_id=row.get('render_id')
        matching=[r for r in p['renders'] if r['id']==render_id] if render_id else [r for r in p['renders'] if r.get('kind')=='element' and r.get('element_id')==row['effect_id'] and r.get('status')=='error' and r.get('created','')>=row.get('created','')]
        if complete and len(matching)==1:
            render=matching[0];render.update(status='done',element_take=take_id,recovered_locally=True);render.pop('error',None)
            row['render_id']=render['id']
        projects.save(p)
    return next(r for r in records(p) if r['id']==take_id)


def _review_elements(p, take_ids, *, validate=True):
    """Choose explicit review candidates while keeping current approved pins."""
    from . import projects
    if not enabled(p) or not p.get('effects'):
        raise ValueError('Add an LTX element before building a review composite.')
    if (not isinstance(take_ids,list) or len(take_ids)>len(p['effects'])
            or any(not isinstance(tid,str) for tid in take_ids) or len(set(take_ids))!=len(take_ids)):
        raise ValueError('Choose each completed element take once.')
    requested={}
    for tid in take_ids:
        row=next((r for r in p.get('element_takes',[]) if r['id']==tid),None)
        if not row or row['effect_id'] in requested:
            raise ValueError('Choose exactly one completed take for each element.')
        requested[row['effect_id']]=row
    effect_ids={e['id'] for e in p['effects']}
    if set(requested)-effect_ids:
        raise ValueError('A selected review take belongs to an element no longer in this shot.')
    selected=[]
    for e in p['effects']:
        signature=generation_signature(p,e)
        pin=p.get('element_approvals',{}).get(e['id'])
        approved=next((r for r in p.get('element_takes',[]) if r['id']==pin and r['effect_id']==e['id']
                       and r.get('status')=='done' and r.get('generation_signature')==signature),None)
        row=requested.get(e['id'])
        if approved:
            if row and row['id']!=approved['id']:
                raise ValueError('The review composite must reuse the current approved element version.')
            row=approved
        if not row or row.get('status')!='done' or row.get('generation_signature')!=signature:
            raise ValueError('Choose current completed RGB and alpha for every unapproved element before reviewing the composite.')
        if validate:
            _validate_optical_assets(p['id'],p,e)
            take=_validated_take(projects.pdir(p['id']),row)
        else:take=None
        selected.append((e,copy.deepcopy(row),take))
    return selected


def _review_selection(selected):
    return [{'effect_id':e['id'],'take_id':row['id'],'generation_signature':row['generation_signature'],
             'request_sha256':row['request_sha256'],'post_manifest_sha256':row['post_manifest_sha256'],
             'assets_sha256':digest(row.get('assets',{}))} for e,row,_ in selected]


def _review_signature(p, selection):
    from . import projects
    return digest(['batch-element-review-v1',projects.effect_signature(p),selection])


def review_render_current(p, render):
    """Review pixels never become approved merely by being the current result."""
    try:
        selected=_review_elements(p,render['review_take_ids'],validate=False)
        selection=_review_selection(selected)
        return selection==render['review_selection'] and _review_signature(p,selection)==render['review_signature']
    except (ValueError,TypeError,KeyError,OSError):
        return False


def review_composite(pid, take_ids):
    """Build a viewing result without changing any per-element approval."""
    if not isinstance(take_ids,list):
        raise ValueError('Choose the completed element take IDs for this review composite.')
    return _composite(pid,review_take_ids=take_ids)


def composite(pid):
    return _composite(pid)


def _composite(pid, *, review_take_ids=None):
    from . import projects
    with projects._lock(pid):
        p=projects.load(pid)
        if projects.generation_active(p): raise ValueError('Wait for the current generation before compositing.')
        review=review_take_ids is not None
        if review:selected=_review_elements(p,review_take_ids)
        else:
            selected=[]
            for e in p['effects']:
                row=selected_take(p,e)
                if not row: raise ValueError('Generate current RGB and alpha for each element before compositing.')
                _validate_optical_assets(pid,p,e)
                selected.append((e,copy.deepcopy(row),_validated_take(projects.pdir(pid),row)))
        rid=uuid.uuid4().hex[:12];rd=projects.pdir(pid)/'renders'/rid;rd.mkdir(parents=True)
        r={'id':rid,'kind':'effect','status':'compositing','files':{},'seed':0,'strength':'','base':'plate',
           'render_profile':'native-ltx-elements-v1','effect_signature':projects.effect_signature(p),'created':time.strftime('%Y-%m-%d %H:%M:%S')}
        if review:
            selection=_review_selection(selected)
            r.update(review_only=True,review_take_ids=[row['id'] for _,row,_ in selected],
                     review_selection=selection,review_signature=_review_signature(p,selection))
        request={'project':p,'elements':[row['id'] for e,row,take in selected]}
        if review:request.update(review_only=True,review_selection=selection,review_signature=r['review_signature'])
        (rd/'request.json').write_text(json.dumps(request,indent=2))
        r['composite_request_sha256']=elements._sha(rd/'request.json')
        r['composite_selection']=_review_selection(selected)
        p['renders'].insert(0,r);projects.save(p)
    def run():
        try:
            profile=source_profile(p);root=projects.pdir(pid);fd=rd/'master_frames';fd.mkdir()
            optical={e['id']:post_holdouts.NativePostHoldouts(p,e,profile,source_depth=row['plan'].get('source_depth')) for e,row,take in selected}
            for f in range(len(profile['timestamps'])):
                native_media.write_native_frame(fd,f,_composite_frame(p,selected,profile,optical,f))
            # Export accepts a separate immutable profile, including source PTS.
            files=native_media.export_native(fd,profile,rd,source_audio=root/'native'/profile.get('source_file','source.mp4') if p.get('native_profile') else None)
            if review:
                for _,row,_ in selected:_validated_take(root,row)
            projects._set(pid,rid,status='done',files={**files,'result':'result.mp4','master':'master.mov','poster':'poster.jpg'},native_profile=profile)
        except Exception as exc: projects._set(pid,rid,status='error',error=f'{type(exc).__name__}: {exc}'[:500])
    threading.Thread(target=run,daemon=True).start()
    return r
