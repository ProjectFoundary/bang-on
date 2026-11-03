"""Projects on disk and the render runner (background threads, status persisted in project.json)."""
from __future__ import annotations

import json
import copy
import hashlib
import os
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

import cv2

import numpy as np

from . import comfy, flashes, graphs, keys, location, media, polish, presets, prompts, setdressing, track, image_models, people, analysis, room_refs, quality
from . import element_jobs, element_graphs, geometry, native_media, native_colour
from .config import (CH, CW, DATA, FPS, MAX_FRAMES, RENDER_CH, RENDER_CW, RENDER_H, RENDER_W, VIDEO_PROFILE,
                     SCENE_PROFILE, SCENE_DRAFT_W, SCENE_DRAFT_H, SCENE_LMS_STRENGTH, SCENE_UPSCALE_PROMPT, render_length)

_locks: dict[str, threading.Lock] = {}


def _lock(pid: str) -> threading.Lock:
    return _locks.setdefault(pid, threading.Lock())


def pdir(pid: str) -> Path:
    return DATA / 'projects' / pid


def load(pid: str) -> dict:
    return json.loads((pdir(pid) / 'project.json').read_text())


def save(p: dict) -> dict:
    path = pdir(p['id']) / 'project.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(p, indent=1))
    tmp.replace(path)
    return p


def update(pid: str, fn) -> dict:
    with _lock(pid):
        p = load(pid)
        fn(p)
        return save(p)


def list_all() -> list[dict]:
    root = DATA / 'projects'
    if not root.exists():
        return []
    out = []
    for f in sorted(root.glob('*/project.json'), key=lambda f: f.stat().st_mtime, reverse=True):
        p = json.loads(f.read_text())
        out.append({k: p[k] for k in ('id', 'name', 'mode', 'frames', 'created')})
    return out


def create(src: Path, name: str, mode: str = 'plate', start_s: float = 0.0, length_s: float | None = None,
           *, preserve_native: bool = False, colour_overrides: dict | None = None, rgb_range: str = 'legalize') -> dict:
    info = media.probe(src)
    if not np.isfinite(start_s) or start_s < 0 or start_s >= info['duration']:
        raise ValueError('Choose a start time inside the uploaded clip.')
    remaining = info['duration'] - start_s
    if length_s is not None and (not np.isfinite(length_s) or length_s <= 0):
        raise ValueError('Choose a positive clip length.')
    duration = min(remaining, length_s) if length_s is not None else remaining
    if not preserve_native and duration > MAX_FRAMES / FPS + 1 / FPS:
        raise ValueError(f'This model handles up to {MAX_FRAMES / FPS:.2f} seconds per shot. Choose a shorter range before importing.')
    pid = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
    d = pdir(pid); d.mkdir(parents=True)
    plate = d / ('plate' + src.suffix.lower())
    shutil.copyfile(src, plate)
    profile = None
    if preserve_native:
        if mode != 'plate': raise ValueError('Native source preservation is the original-plate workflow. Scene generation uses its existing working profile.')
        if duration > 60: raise ValueError('Select a shot of60seconds or less; generated elements are limited to5seconds each.')
        try:
            profile = native_media.ingest_native(plate, d / 'native', start_s, length_s, colour_overrides=colour_overrides,
                                                 rgb_range=rgb_range, display_dir=d / 'frames' / 'plate')
            n = profile['frames']
        except Exception:
            shutil.rmtree(d)
            raise
    else:
        max_frames = min(MAX_FRAMES, round(length_s * FPS)) if length_s else MAX_FRAMES
        n = media.ingest(plate, d / 'frames' / 'plate', start_s, max_frames)
    if not n: raise ValueError('The selected clip contains no video frames.')
    p = {'id': pid, 'name': name or src.stem, 'created': time.strftime('%Y-%m-%d %H:%M'), 'mode': mode,
         'source': {'file': plate.name, **info}, 'start_s': start_s, 'frames': n,
         'shot': '', 'location': '', 'background': None, 'environment': None, 'approved_environment': None,
         'opening_reference': None, 'reference_mode': 'opening_only', 'location_role': 'opening',
         'location_seed': 11, 'environment_seed': 42,
         'room_references': [],
         'people': copy.deepcopy(people.DEFAULTS),
         'effects': [], 'settings': {'guide_mode': 'sparse', 'strength': 'exact', 'seed': 42, 'polish': True}, 'renders': []}
    if profile:
        p.update(native_profile=profile, geometry=geometry.defaults(), element_takes=[])
        p['settings'].update(pipeline='ltx_elements', polish=False)
    track.ensure(d, d / 'frames' / 'plate', 'plate', n)           # quick auto-track for blocking
    return save(p)


def base_name(p: dict) -> str:
    return 'environment' if p['mode'] == 'greenscreen' and p.get('environment') else 'plate'


def track_file(p: dict) -> Path:
    b = environment_stage(p) if base_name(p) == 'environment' else 'plate'
    return track.ensure(pdir(p['id']), stage_dir(p, b), b.replace(':', '_'), p['frames'])


def environment_stage(p: dict) -> str | None:
    return f"environment:{p['environment']}" if p.get('environment') else None


def approved(p: dict) -> bool:
    if p.get('mode') != 'greenscreen': return True
    if not p.get('environment') or p.get('approved_environment') != p['environment']: return False
    r=next((r for r in p.get('renders',[]) if r['id']==p['environment']),None)
    return r is None or r.get('people_signature') == people.signature(p)


def require_approved(p: dict) -> None:
    if not approved(p):
        raise ValueError('Review and approve an environment before generating effects, flashes or a final shot.')


def source_stage(p: dict) -> str:
    return environment_stage(p) if p.get('mode') == 'greenscreen' and p.get('environment') else 'plate'


def effect_settings(p: dict) -> dict:
    """Future generations adopt sparse all-step guides; saved render recipes stay frozen."""
    settings = dict(p.get('settings', {}))
    # The old schema recommended early-only guidance. An unversioned setting is
    # upgraded for future submissions without rewriting old project/render files.
    if 'guide_mode' not in settings:
        settings.update(guide_mode='sparse', strength='exact')
    settings.setdefault('strength', 'exact')
    if settings['guide_mode'] not in ('sparse', 'full'):
        raise ValueError('Choose natural motion or all-phase guides.')
    if settings['strength'] not in ('guide', 'firm', 'exact'):
        raise ValueError('Choose a supported guide duration.')
    if 'collider' in settings and not isinstance(settings['collider'], bool):
        raise ValueError('Collider mode must be on or off.')
    if 'legacy_h3' in settings and not isinstance(settings['legacy_h3'], bool):
        raise ValueError('The legacy H3 workflow choice must be on or off.')
    if settings.get('pipeline', 'h3') not in ('h3', 'ltx_elements'):
        raise ValueError('Choose the LTX element or legacy H3 workflow.')
    if p.get('native_profile') and settings.get('pipeline') != 'ltx_elements':
        raise ValueError('Native source projects use the plate-preserving element workflow.')
    if settings.get('pipeline')=='ltx_elements':
        element_graphs.normalize_recipe({'models':settings.get('element_models',{}),
                                                     'model_revisions':settings.get('element_model_revisions',{})})
    return settings


def collider_recipe(p: dict) -> dict | None:
    if not effect_settings(p).get('collider', False):
        return None
    cfg = people.validate(p.get('people', {}), p['frames'])
    matte = people.current(p)
    return {'version': 'squib-moving-wall-v2', 'matte': matte['id'] if matte else None,
            'mask_signature': people.mask_signature(p), 'feather': cfg['feather'], 'trim': cfg['trim']}


def _require_collider_layers(p: dict, layers, collider_input: str | None = None) -> None:
    recipe = collider_recipe(p)
    if recipe is None:
        if collider_input:
            raise ValueError('Collider mode changed. Reload and prepare the keys again.')
        return
    if not recipe['matte']:
        raise ValueError('Build a current people matte before using collider mode.')
    frames = keys.guide_frames(p['effects'], p['frames'], effect_settings(p)['guide_mode'])
    if any(not all((layers or {}).get(f, {}).get(name) for name in ('fx', 'glow')) for f, _ in frames):
        raise ValueError('Collider mode needs all browser-rendered guide layers. Reload and prepare the keys again.')
    cfg = people.validate(p.get('people', {}), p['frames'])
    expected = [recipe['version'], p['id'], p['frames'], recipe['matte'], True, cfg['selection'], cfg['feather'], cfg['trim']]
    try:
        submitted = json.loads(collider_input or 'null')
    except (ValueError, TypeError):
        submitted = None
    if submitted != expected:
        raise ValueError('People matte changed or its guide version is missing. Reload and prepare the keys again.')


def effect_signature(p: dict) -> str:
    if element_jobs.enabled(p):
        from . import matte_treatment
        data=['ltx_elements_v2',p.get('native_profile'),p.get('effects',[]),p.get('geometry',{}),p.get('settings',{}),p.get('element_approvals',{}),
              [matte_treatment.policy(e.get('element',{})) for e in p.get('effects',[])]]
        from . import element_finishing
        finishing=[element_finishing.policy(e.get('element',{})) for e in p.get('effects',[])]
        if any(cfg!=element_finishing.policy() for cfg in finishing):data.append(finishing)
        from . import lighting_integration
        light=lighting_integration.selected_signature(p)
        if light is not None:data.append(['signed_lighting',light])
        return element_jobs.digest(data)
    data = [p.get('shot'), p.get('effects', []), effect_settings(p), image_models.selected(p, 'polish'), VIDEO_PROFILE,
            'sparse-explosion-guides-v1']
    recipe = collider_recipe(p)
    if recipe is not None:
        data.append(recipe)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def pipeline_active(p: dict) -> bool:
    return any(r['kind'] == 'pipeline' and r['status'] not in ('done', 'error') for r in p['renders'])


def generation_active(p: dict) -> bool:
    """Action drafting is advisory background work and never locks shot setup."""
    return any(r['kind'] != 'analysis' and r['status'] not in ('done', 'error') for r in p['renders'])


def render_current(p: dict, r: dict) -> bool:
    if r['kind']=='lighting':
        from . import lighting_integration
        try:return r.get('input_signature')==lighting_integration.input_signature(p)
        except ValueError:return False
    if r['kind'] in ('effect', 'keys'):
        if r.get('review_only'):
            return element_jobs.review_render_current(p,r)
        # An unversioned historical H3 take cannot prove which isolated LTX
        # elements/integration settings it contains. Keep it as history only.
        if element_jobs.enabled(p) and not r.get('effect_signature'):
            return False
        return (r.get('base', 'plate' if p.get('mode') == 'plate' else None) == source_stage(p) and
                (not r.get('effect_signature') or r['effect_signature'] == effect_signature(p)))
    if r['kind'] == 'final':
        if element_jobs.enabled(p) and p.get('effects') and not str(r.get('base','')).startswith('render:'):
            # A clean-plate finish predates the newly added, unrendered FX.
            return False
        return r.get('base') == flash_base(p) and r.get('flash_signature') == flash_signature(p)
    return True


def public(p: dict) -> dict:
    """Computed status is never trusted from the browser or used as persisted provenance."""
    q = copy.deepcopy(p)
    q['settings'] = effect_settings(p)
    q.setdefault('location_role', 'opening')
    q.setdefault('reference_mode', 'opening_only')
    q.setdefault('location_seed', 11)
    q.setdefault('environment_seed', p.get('settings', {}).get('seed', 42))
    q.setdefault('set_dressing', {'source': None, 'objects': [], 'protected': [], 'people': True, 'seed': 281040, 'margin': .04})
    q['image_models'] = image_models.validate(p.get('image_models', {}))
    q.setdefault('room_references',[])
    q['room_reference_limits']=dict(room_refs.LIMITS)
    q['room_reference_max']=room_refs.MAX_IMAGES
    q['people'] = people.validate(p.get('people', {}), p['frames'])
    matte = people.current(p)
    q['people_matte'] = {'id': matte['id'], 'current': True} if matte else None
    for version in q.get('locations',[]):
        sig=version.get('people_opening_signature')
        version['people_current']=not sig or sig==people.opening_signature(p)
    q['element_takes'] = element_jobs.records(p)
    from . import lighting_integration
    q['lighting_takes']=lighting_integration.records(p)
    from . import people_alpha
    q['people_alpha_takes'] = people_alpha.records(p)
    for take in q['people_alpha_takes']:
        take['approved_mattes'] = [m['id'] for m in p.get('geometry',{}).get('mattes',[]) if m.get('holdout',{}).get('take_id') == take['id']]
    q['element_model_defaults'] = dict(element_graphs.DEFAULT_MODELS)
    q.setdefault('geometry', geometry.defaults())
    q['render_timing'] = {'source_frames': p['frames'], 'generated_frames': p['frames'] if element_jobs.enabled(p) else render_length(p['frames']),
                          'padding_frames': 0 if element_jobs.enabled(p) else render_length(p['frames']) - p['frames'], 'fps': p.get('native_profile', {}).get('fps', FPS)}
    q['render_format'] = {'width': RENDER_CW, 'height': RENDER_CH, 'canvas_width': RENDER_W,
                          'canvas_height': RENDER_H, 'fps': FPS, 'profile': VIDEO_PROFILE}
    if p.get('native_profile'):
        pf = p['native_profile']
        q['render_format'].update(width=pf['width'], height=pf['height'], fps=pf['fps'], profile='native-source-elements-v1')
    q['environment_ready'] = approved(p)
    for r in q['renders']:
        r['source_current'] = render_current(p, r)
    for f in q.get('flashes', []):
        f['result_current'] = flash_current(p, f)
        if f['result_current']:
            f['result_base'] = flash_base(p)
    q['export_state'] = {'ready': sum(f['result_current'] for f in q.get('flashes', [])),
                         'unready': sum(not f['result_current'] for f in q.get('flashes', []))}
    return q


def approve_environment(pid: str, rid: str) -> dict:
    p = load(pid)
    r = next((r for r in p['renders'] if r['id'] == rid and r['kind'] == 'environment' and r['status'] == 'done'), None)
    if r is None:
        raise ValueError('Only a completed environment can be approved.')
    stage_dir(p, f'environment:{rid}')   # materialise/validate this immutable version before selecting it
    def fn(q):
        q.update(environment=rid, approved_environment=rid, flash_base=f'environment:{rid}')
        next(r for r in q['renders'] if r['id'] == rid)['approved_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    return update(pid, fn)


def location_entry(p: dict, file: str) -> tuple[dict, Path]:
    entry = next((l for l in p.get('locations', []) if l['file'] == file), None)
    path = (pdir(p['id']) / file).resolve()
    if not entry or pdir(p['id']).resolve() not in path.parents or not path.is_file():
        raise ValueError('Choose a saved location version from this project.')
    return entry, path


def approve_dressing_source(pid: str, file: str) -> dict:
    """Explicit still approval binds the draft and its coordinates to one version."""
    def fn(p):
        entry, path = location_entry(p, file)
        if cv2.imread(str(path)) is None: raise ValueError('This location image cannot be read.')
        entry.update(dressing_approved_at=time.strftime('%Y-%m-%d %H:%M:%S'), approved_hash=setdressing.digest(path))
        if p.get('set_dressing', {}).get('source') != file:
            p['set_dressing'] = {'source': file, 'objects': [],
                                 'protected': copy.deepcopy(entry.get('protected_areas', [])),
                                 'people': not bool(entry.get('empty_set')), 'seed': 281040, 'margin': .04}
    return update(pid, fn)


def start_dressing(pid: str) -> dict:
    with _lock(pid):
        p = load(pid)
        if p['mode'] != 'greenscreen': raise ValueError('Object placement belongs to the green-screen location workflow.')
        if any(r['kind'] == 'dressing' and r['status'] not in ('done', 'error') for r in p['renders']):
            raise ValueError('Wait for the current object edit to finish.')
        draft = setdressing.validate(p.get('set_dressing', {}))
        if not draft['source']: raise ValueError('Approve a location for object placement first.')
        entry, source = location_entry(p, draft['source'])
        if not entry.get('dressing_approved_at') or entry.get('approved_hash') != setdressing.digest(source):
            raise ValueError('Review and approve this location before adding objects.')
        if not draft['objects'] or any(not o['text'] for o in draft['objects']):
            raise ValueError('Draw and describe at least one object.')
        if draft['people'] and not draft['protected']:
            raise ValueError('Draw protected areas over the people and everything they hold first.')
        image = cv2.imread(str(source))
        setdressing.masks((image.shape[1], image.shape[0]), draft)  # fail before any Cloud work
        rid = time.strftime('%H%M%S-') + uuid.uuid4().hex[:4]
        rd = pdir(pid) / 'renders' / rid; (rd / 'inputs').mkdir(parents=True)
        shutil.copyfile(source, rd / 'inputs/source_approved.png')
        p['set_dressing'] = draft
        r = {'id': rid, 'kind': 'dressing', 'status': 'queued', 'created': time.strftime('%Y-%m-%d %H:%M:%S'),
             'seed': draft['seed'], 'strength': '', 'pid': os.getpid(), 'files': {},
             'parent_source': draft['source'], 'parent_source_hash': setdressing.digest(source)}
        (rd / 'request.json').write_text(json.dumps({'project': p, 'render': r}, indent=1))
        p['renders'].insert(0, r); save(p)
    threading.Thread(target=_run, args=(pid, rid), daemon=True).start()
    return r


def _alive(pid: int | None) -> bool:
    if not pid or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def recover() -> int:
    """Mark renders left unfinished by a stopped process as interrupted (their worker threads are gone). Returns how many."""
    n = 0
    for item in list_all():
        dirty = False
        def fn(p):
            nonlocal n, dirty
            for r in p['renders']:
                if r['status'] not in ('done', 'error') and not _alive(r.get('pid')):
                    r.update(status='error', error='interrupted (the Bang On process stopped before this render finished)'); n += 1; dirty = True
            for take in p.get('element_takes', []):
                if take.get('status') not in ('prepared','done','error') and not any(
                    r.get('kind')=='element' and r.get('element_id')==take.get('effect_id') and r.get('status') not in ('done','error')
                    for r in p['renders']):
                    take.update(status='error',error='Interrupted. Provider receipts are retained; this version will not resubmit automatically.')
                    dirty=True
            for take in p.get('people_alpha_takes', []):
                if take.get('status') not in ('prepared','done','failed','uncertain') and not any(
                    r.get('kind')=='people_alpha' and r.get('people_alpha_take')==take.get('id') and r.get('status') not in ('done','error')
                    for r in p['renders']):
                    take.update(status='uncertain',error='Interrupted. Inspect the saved AlphaGen receipt; this pass will not resubmit automatically.')
                    dirty=True
            for take in p.get('lighting_takes',[]):
                if take.get('status') not in ('done','error') and not any(r.get('id')==take['id'] and r.get('status') not in ('done','error') for r in p['renders']):
                    take.update(status='error',error='Interrupted. Lighting receipts are retained; inspect them before explicitly starting a new version.')
                    dirty=True
            for f in p.get('flashes', []):
                if f.get('status') in ('queued', 'generating'):
                    f.update(status='error', error='interrupted (Bang On stopped before this flash finished)'); n += 1; dirty = True
        with _lock(item['id']):
            p = load(item['id']); fn(p)
            if dirty:
                save(p)
    return n


def plate_dir(p: dict) -> Path:
    """Recover native frames from the uploaded original for older 960px projects.

    Keep old frames and render inputs intact for provenance. Never relabel an
    upscaled 960px cache as a native source.
    """
    d = pdir(p['id'])
    legacy = d / 'frames' / 'plate'
    if p.get('native_profile'):
        return legacy
    first = media.read_frame(legacy, 0)
    if first.shape[:2] == (RENDER_CH, RENDER_CW): return legacy
    source = d / p.get('source', {}).get('file', '__missing_source__')
    if not source.is_file():
        if p.get('source', {}).get('file'):
            raise ValueError('The uploaded original is missing; native source frames cannot be recovered.')
        return legacy  # legacy frame-only projects have no recoverable source
    target = d / 'frames' / 'plate_native1080'
    with _frame_lock:
        if not (target / '.complete').exists():
            n = media.ingest(source, target, p.get('start_s', 0.), p['frames'])
            if n != p['frames']:
                raise ValueError('Could not recover the complete native source from the uploaded original.')
            (target / '.complete').write_text(VIDEO_PROFILE)
    return target


def base_dir(p: dict) -> Path:
    """Frames that keys are built on and the model sees: the plate, or the environment pass for green screen."""
    d = pdir(p['id'])
    if p['mode'] == 'greenscreen':
        if not p.get('environment'):
            raise ValueError('build the environment first (green-screen projects)')
        return stage_dir(p, environment_stage(p))
    return plate_dir(p)


def add_effect(pid: str, preset_id: str, frame: int, x: float, y: float) -> dict:
    def fn(p):
        onset = int(presets.get(preset_id).get('default_start_frame', frame))
        e = presets.new_effect(preset_id, onset, x, y, uuid.uuid4().hex[:6], p['frames'])
        if element_jobs.enabled(p):
            fps = element_jobs.source_profile(p)['fps']
            for k in e['keys']: k['frame'] = min(p['frames']-1, round(onset+(k['frame']-onset)*fps/24))
            e['element'] = {**element_jobs.DEFAULT, 'body_matte': preset_id in element_jobs.KINDS, 'duration_s': 5. if preset_id=='smoke_plume_hq' else 3. if 'smoke' in preset_id or preset_id=='fire' else 2.}
            e['tracking'] = 'camera'
        p['effects'].append(e)
    return update(pid, fn)


def prompt_preview(p: dict) -> str:
    if not p['effects']:
        return ''
    return prompts.effect_prompt(p.get('shot', ''), p['effects'], guide_mode=effect_settings(p)['guide_mode'])


def key_preview(p: dict, frame: int) -> bytes:
    img = keys.preview(base_dir(p), p['effects'], frame)
    return cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes()


# ---------------------------------------------------------------- renders

def start_render(pid: str, kind: str = 'effect', layers: dict[int, dict[str, bytes]] | None = None, *, _background=True,
                 prompt_override: str | None = None, collider_input: str | None = None) -> dict:
    """layers: optional {frame: {'fx': png, 'glow': png}} from the browser mock-up (what the user saw); keys are built from them."""
    if prompt_override is not None:
        if kind!='environment':raise ValueError('A prompt override is supported only for a scene video test.')
        if not isinstance(prompt_override,str) or not prompt_override.strip() or len(prompt_override)>12000:
            raise ValueError('The scene test prompt must contain 1–12000 characters.')
        prompt_override=prompt_override.strip()
    current_project = load(pid)
    if current_project.get('native_profile') and kind in ('environment','location','dressing','analysis'):
        raise ValueError('Native plate projects keep original photography. Create a separate Scene project for scene generation.')
    if element_jobs.enabled(current_project) and kind in ('effect','keys','finish'):
        if kind == 'keys': raise ValueError('Use Preview guide on each LTX element.')
        if kind == 'effect': return element_jobs.composite(pid)
        return start_pipeline(pid)
    if kind == 'finish': return start_pipeline(pid, layers, collider_input=collider_input)
    if kind == 'dressing': return start_dressing(pid)
    p = load(pid)
    if kind in ('effect', 'keys'):
        require_approved(p)
        _require_collider_layers(p, layers, collider_input)
        if any(r['kind']=='analysis' for r in p['renders']) and not p.get('shot','').strip():
            raise ValueError('Review the action draft or enter an action description before generating.')
        if not p['effects']:
            raise ValueError('add at least one effect first')
        bd = base_dir(p)
        if media.read_frame(plate_dir(p),0).shape[:2] == (RENDER_CH,RENDER_CW) and media.read_frame(bd,0).shape[:2] != (RENDER_CH,RENDER_CW):
            raise ValueError('The selected scene is an older low-resolution take. Generate and approve a native 1080p scene before building effects.')
    elif kind == 'environment':
        if p['mode'] != 'greenscreen' or not p.get('opening_reference'):
            raise ValueError('Choose an opening guide showing the frame-0 pose before building an environment.')
        if p.get('reference_mode', 'opening_only') == 'scene_and_opening' and not p.get('background'):
            raise ValueError('Choose a separate scene reference or use the opening reference only.')
        if people.signature(p) and not people.current(p):
            raise ValueError('Build the current people matte before generating the scene.')
        cfg=people.validate(p.get('people',{}),p['frames'])
        if cfg['mode']=='cutout' and cfg['selection']!='manual':
            raise ValueError('Original cutout needs a tracked matte. Choose Manual selection in People and build it first.')
        if cfg['mode']=='guide' and cfg['extra_reference'] and not people.current(p):
            raise ValueError('Select people and build their matte before adding the extra people reference.')
        ref=next((l for l in p.get('locations',[]) if l['file']==p['opening_reference']),{})
        if ref.get('people_opening_signature') and ref['people_opening_signature']!=people.opening_signature(p):
            raise ValueError('The opening still uses an earlier people selection. Rebuild it with the current matte.')
        if people.signature(p) and p.get('people',{}).get('mode')=='cutout' and not ref.get('empty_set'):
            raise ValueError('Original cutout mode needs an empty first-frame location still. Generate one in Location first.')
        if ref.get('empty_set') and p.get('people',{}).get('mode')!='cutout':
            raise ValueError('Relight mode needs a first-frame reference with the selected people included.')
        opening = cv2.imread(str(pdir(pid)/p['opening_reference']))
        if media.read_frame(plate_dir(p),0).shape[:2] == (RENDER_CH,RENDER_CW) and (opening is None or opening.shape[:2] != (RENDER_CH,RENDER_CW)):
            raise ValueError('The opening still is below native 1080p. Generate and select a new location still before generating the scene.')
    elif kind == 'matte':
        cfg=people.validate(p.get('people',{}),p['frames'])
        if cfg['selection']!='manual':raise ValueError('Choose Manual selection to build a tracked people matte.')
        people.graph(cfg,'keyfx/people_matte')  # validate selection before any spend
    elif kind == 'analysis':
        pass
    elif kind == 'location':
        if p['mode'] != 'greenscreen':
            raise ValueError('locations are built for green-screen shots')
        references=p.get('room_references',[])
        room_refs.check_limit(image_models.selected(p,'location'),len(references))
        room_refs.paths(pdir(pid),references)
        f = 0 if p.get('location_role', 'opening') == 'opening' else int(p.get('location_frame', 0))
        if p.get('location_role', 'opening') == 'opening' and p.get('empty_set'):
            raise ValueError('An opening guide must include the people. Use scene-only mode for an empty set.')
        cfg=people.validate(p.get('people',{}),p['frames'])
        if cfg['mode']=='cutout' and cfg['selection']!='manual':
            raise ValueError('Original cutout needs a tracked matte. Choose Manual selection in People and build it first.')
        if (p.get('location_role','opening')=='opening' or not p.get('empty_set')) and cfg['selection']=='manual':
            if not cfg['points'] or not people.current(p):
                raise ValueError('Select the people to keep and build their matte before generating the opening still.')
    else:
        raise ValueError(f'unknown render kind {kind}')
    rid = time.strftime('%H%M%S-') + uuid.uuid4().hex[:4]
    r = {'id': rid, 'kind': kind, 'status': 'queued', 'created': time.strftime('%Y-%m-%d %H:%M:%S'),
         'seed': p.get('location_seed', 11) if kind == 'location' else p.get('environment_seed', p['settings'].get('seed', 42)) if kind == 'environment' else p['settings'].get('seed', 42), 'strength': p['settings'].get('strength', 'guide'),
         'polish': bool(p['settings'].get('polish', True)), 'pid': os.getpid(), 'files': {}}
    if kind in ('environment', 'effect', 'keys'):
        r['render_profile'] = SCENE_PROFILE if kind == 'environment' else VIDEO_PROFILE
    if kind in ('effect', 'keys'):
        cfg = effect_settings(p)
        r.update(strength=cfg['strength'], guide_mode=cfg['guide_mode'])
        r['base'] = source_stage(p)
        r['effect_signature'] = effect_signature(p)
        if cfg.get('collider'):
            r.update(collider=True, collider_recipe=collider_recipe(p), collider_input=collider_input)
    if kind == 'environment':
        r.update(scene_phase='draft', lms_strength=SCENE_LMS_STRENGTH,
                 opening_reference=p['opening_reference'], reference_mode=p.get('reference_mode', 'opening_only'),
                 location_reference=p.get('background') if p.get('reference_mode') == 'scene_and_opening' else p['opening_reference'],
                 people_signature=people.signature(p),extra_people_reference=people.validate(p.get('people',{}),p['frames'])['extra_reference'])
        if prompt_override is not None:r['prompt_override']=prompt_override
    if kind == 'matte': r['mask_signature']=people.mask_signature(p)
    rd = pdir(pid) / 'renders' / rid
    rd.mkdir(parents=True, exist_ok=True)
    if kind=='location':
        r['room_references']=room_refs.snapshot(pdir(pid),p.get('room_references',[]),rd/'inputs/room_references')
    (rd / 'request.json').write_text(json.dumps({'project': p, 'render': r}, indent=1))
    if layers:
        ld = pdir(pid) / 'renders' / rid / 'layers'; ld.mkdir(parents=True, exist_ok=True)
        for f, files in layers.items():
            for name, data in files.items():
                (ld / f'{name}_{int(f):04d}.png').write_bytes(data)
        r['mockup'] = True
    def register(q):
        if kind == 'analysis' and any(x['kind']=='analysis' and x['status'] not in ('done','error') for x in q['renders']):
            raise ValueError('Action analysis is already in progress. You can continue setting up the shot.')
        if _background and kind != 'analysis' and generation_active(q):
            raise ValueError('Wait for the current processing step to finish before starting another.')
        q['renders'].insert(0,r)
    update(pid,register)
    if _background: threading.Thread(target=_run, args=(pid, rid), daemon=True).start()
    return r


def _set(pid: str, rid: str, **kw) -> None:
    if kw.get('status') in ('done','error'):
        kw.setdefault('finished',time.strftime('%Y-%m-%d %H:%M:%S'))
        kw.setdefault('finished_at',time.time())
    def fn(p):
        for r in p['renders']:
            if r['id'] == rid:
                if 'status' in kw and kw['status']!=r['status']:r['progress']={}
                r.update(kw)
    update(pid, fn)


_progress_time: dict[tuple,float] = {}
def _progress(pid: str,rid: str,value: dict, *, phase: str | None = None) -> None:
    key=(pid,rid,phase);now=time.monotonic()
    if now-_progress_time.get(key,0)<.6 and 'cloud_status' not in value:return
    _progress_time[key]=now
    def fn(p):
        r=next((r for r in p['renders'] if r['id']==rid),None)
        if r and r['status'] not in ('done','error') and (phase is None or r.get('scene_phase') == phase):
            progress={**r.get('progress',{}),**{k:v for k,v in value.items() if v is not None}}
            cloud=value.get('cloud_status')
            if ('queue_position' in value and value['queue_position'] is None) or (cloud and not cloud.startswith('queued')):
                progress.pop('queue_position',None)
            r['progress']=progress
    update(pid,fn)


def _scene_status(pid: str, rid: str, phase: str, job: str, status: str) -> None:
    """A late event from the draft must never reset progress for the HD job."""
    def fn(p):
        r = next(x for x in p['renders'] if x['id'] == rid)
        if r.get('scene_phase') != phase or r['status'] in ('done', 'error'):
            return
        next_status = 'rendering' if status == 'running' else 'downloading'
        if next_status != r['status']:
            r['progress'] = {}
        r.update(status=next_status, job=job, jobs={**r.get('jobs', {}), phase: job})
    update(pid, fn)


def _scene_video_check(raw: Path, width: int, height: int, frames: int) -> None:
    info = media.probe(raw)
    if (info['width'], info['height']) != (width, height):
        raise RuntimeError(f'The scene returned outside the required {width}x{height} canvas; it was not accepted.')
    if abs(info['fps'] - FPS) > .01 or info['frames'] != render_length(frames):
        raise RuntimeError('The scene returned with the wrong frame count or frame rate; it was not accepted.')


def _layer_comp(bd: Path, ld: Path, f: int) -> tuple[np.ndarray, np.ndarray] | None:
    """Key from the browser mock-up: plate + glow (added) + effects layer (over). Returns (image, effects alpha) or None."""
    fx_f, glow_f = ld / f'fx_{f:04d}.png', ld / f'glow_{f:04d}.png'
    if not fx_f.exists():
        return None
    img = media.read_frame(bd, f).astype(np.float32)
    if glow_f.exists():
        g = cv2.imread(str(glow_f), cv2.IMREAD_UNCHANGED).astype(np.float32)
        if g.shape[:2] != img.shape[:2]:
            raise ValueError('FX glow resolution differs from the source. Reload Bang On and prepare the keys again.')
        img += g[..., :3] * (g[..., 3:] / 255)
    fx = cv2.imread(str(fx_f), cv2.IMREAD_UNCHANGED).astype(np.float32)
    if fx.shape[:2] != img.shape[:2]:
        raise ValueError('FX key resolution differs from the source. Reload Bang On and prepare the keys again.')
    a = fx[..., 3:] / 255
    return img * (1 - a) + fx[..., :3] * a, a[..., 0]


def _keys_from_layers(p: dict, r: dict, rd: Path) -> list[tuple[int, Path, bool]]:
    bd, ld, out = base_dir(p), rd / 'layers', rd / 'keys'
    out.mkdir(parents=True, exist_ok=True)
    frames = keys.guide_frames(p['effects'], p['frames'], r.get('guide_mode', 'full'))
    if r.get('collider') and any(not (ld / f'{name}_{f:04d}.png').is_file() for f, _ in frames for name in ('fx', 'glow')):
        raise ValueError('Collider guide layers are incomplete. Prepare the keys again.')
    if r.get('polish', True):
        _set(p['id'], r['id'], status='polishing')

    def one(item):
        f, hard = item
        got = _layer_comp(bd, ld, f)
        if got is None:                                   # not uploaded: fall back to the stamp key
            img = keys.compose(media.read_frame(bd, f).astype(np.float32), p['effects'], f)
        else:
            img, mask = got
            prompt = polish.prompt_for(p['effects'], f) if r.get('polish', True) else None
            if prompt and mask.max() > 0.05:
                img = polish.polish_frame(img, mask, prompt, r['seed'] + f, pdir(p['id']) / 'polish_cache', model=image_models.selected(p, 'polish'))
        path = out / f'key_{f:04d}.png'
        cv2.imwrite(str(path), media.pad(img))
        return f, path, hard

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as pool:
        return sorted(pool.map(one, frames))


def _build_keys(p: dict, r: dict, rd: Path) -> list[tuple[int, Path, bool]]:
    if (rd / 'layers').exists():
        return _keys_from_layers(p, r, rd)
    if r.get('collider'):
        raise ValueError('Collider guide layers are missing. Prepare the keys again.')
    bd = base_dir(p)
    if r.get('polish', True):
        _set(p['id'], r['id'], status='polishing')
        return polish.polish_keys(bd, p['effects'], rd / 'keys', r['seed'], pdir(p['id']) / 'polish_cache', p['frames'], model=image_models.selected(p, 'polish'), guide_mode=r.get('guide_mode', 'full'))
    return keys.build(bd, p['effects'], rd / 'keys', p['frames'], guide_mode=r.get('guide_mode', 'full'))


def _key_sheet(key_list: list[tuple[int, Path, bool]], out: Path, cols: int = 3) -> None:
    tiles = []
    for f, path, _hard in key_list:
        im = cv2.resize(cv2.imread(str(path)), (480, 272))
        cv2.putText(im, str(f), (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
        cv2.putText(im, str(f), (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        tiles.append(im)
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    cv2.imwrite(str(out), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88])


def _run(pid: str, rid: str) -> None:
    try:
        d = pdir(pid); rd = d / 'renders' / rid; rd.mkdir(parents=True, exist_ok=True)
        request = json.loads((rd / 'request.json').read_text())
        p, r = request['project'], request['render']
        _set(pid, rid, status='preparing')
        if r['kind'] in ('matte','analysis'):
            status=lambda job,s:_set(pid,rid,job=job,status='rendering' if s=='running' else 'finishing')
            progress=lambda v:_progress(pid,rid,v)
            if r['kind']=='analysis':
                draft=analysis.draft(d/'frames/plate',p['frames'],rd,on_status=status,on_progress=progress)
                _set(pid,rid,status='done',draft=draft,files={'action':'action.txt'},finished=time.strftime('%Y-%m-%d %H:%M:%S'))
            else:
                people.build(plate_dir(p),p['frames'],people.validate(p.get('people',{}),p['frames']),rd,on_status=status,on_progress=progress)
                _set(pid,rid,status='done',files={'cutout':'cutout.mov'},finished=time.strftime('%Y-%m-%d %H:%M:%S'))
            return
        if r['kind'] == 'dressing':
            draft = p['set_dressing']
            parent, _ = location_entry(p, draft['source'])
            source = rd / 'inputs/source_approved.png'
            if setdressing.digest(source) != r['parent_source_hash']:
                raise ValueError('The approved source snapshot has changed.')
            img = setdressing.build(source, draft, rd,
                    on_status=lambda job, status: _set(pid, rid, job=job, status='rendering' if status == 'running' else 'finishing'),
                    model=image_models.selected(p, 'dressing'))
            rel = str(img.relative_to(d))
            text = (rd / 'prompt.txt').read_text()
            scene_objects = copy.deepcopy(parent.get('scene_objects', parent.get('dressing', []))) + copy.deepcopy(draft['objects'])
            def keep_dressing(q):
                q.setdefault('locations', []).insert(0, {'id': rid, 'file': rel, 'seed': draft['seed'],
                    'frame': parent.get('frame'), 'role': parent.get('role', 'scene'),
                    'empty_set': bool(parent.get('empty_set')), 'kind': 'dressing', 'model': image_models.label(image_models.selected(p, 'dressing')),
                    'parent_source': draft['source'], 'parent_source_hash': r['parent_source_hash'],
                    'people_opening_signature': parent.get('people_opening_signature'),
                    'opening_method': parent.get('opening_method'),
                    'room_references': copy.deepcopy(parent.get('room_references',[])),
                    'objects': draft['objects'], 'protected_areas': draft['protected'], 'scene_objects': scene_objects,
                    'prompt': text, 'created': time.strftime('%Y-%m-%d %H:%M')})
            update(pid, keep_dressing)
            _set(pid, rid, status='done', finished=time.strftime('%Y-%m-%d %H:%M:%S'),
                 files={'location': 'location.png', 'source': 'inputs/source_approved.png', 'verification': 'verification.json'})
            return
        if r['kind'] == 'location':                  # step 1: edit a plate frame into the location (perspective matches the shot)
            role = p.get('location_role', 'opening')
            f = 0 if role == 'opening' else int(p.get('location_frame') or 0)
            f=min(max(f,0),p['frames']-1)
            frame = cv2.imread(str(media.frame_path(plate_dir(p), f)))
            cfg=people.validate(p.get('people',{}),p['frames'])
            matte=people.current(p)
            cutout=cfg['selection']=='manual' and bool(cfg['points']) and cfg['mode']=='cutout'
            references=[d/ref['file'] for ref in r.get('room_references',[])]
            text = location.edit_prompt(p.get('location', ''), [], p.get('lighting', ''), not p.get('empty_set') and not cutout,room_references=len(references))
            model = image_models.selected(p, 'location')
            matte_opening=bool(matte) and not cutout and not p.get('empty_set')
            if matte_opening:
                mask=d/'renders'/matte['id']/'masks'/f'{f:05d}.png'
                img=location.build_opening(frame,mask,cfg,
                    p.get('location',''),p.get('lighting',''),r['seed'],rd,model=model,
                    on_stage=lambda stage:_set(pid,rid,status=stage),**({'references':references} if references else {}))
                text=(rd/'prompt.txt').read_text()
            else:
                img = location.build(frame, text, r['seed'], rd, **({'model': model} if model != 'qwen' else {}),**({'references':references} if references else {}))
                text=(rd/'prompt.txt').read_text() if (rd/'prompt.txt').exists() else text
            rel = str(img.relative_to(d))
            def keep(q):
                q.setdefault('locations', []).insert(0, {'id': rid, 'file': rel, 'seed': r['seed'], 'frame': f, 'prompt': text,
                                                         'role': role, 'empty_set': bool(p.get('empty_set') or cutout),
                                                         'dressing': [], 'lighting': p.get('lighting', ''), 'model': image_models.label(model),
                                                         'opening_method': 'matte' if matte_opening else 'empty' if cutout or p.get('empty_set') else 'plate',
                                                         'people_opening_signature': people.opening_signature(p) if not p.get('empty_set') and not cutout else None,
                                                         'room_references': copy.deepcopy(r.get('room_references',[])),
                                                         'created': time.strftime('%Y-%m-%d %H:%M')})
                # A new still is a candidate. Selecting it for the opening/scene is a separate action.
            update(pid, keep)
            _set(pid, rid, status='done', finished=time.strftime('%Y-%m-%d %H:%M:%S'), files={'location': 'location.png'})
            return
        if r['kind'] == 'keys':                      # preview only: build (and polish) the keys, no video render
            key_list = _build_keys(p, r, rd)
            _key_sheet(key_list, rd / 'keys.jpg')
            (rd / 'effects.json').write_text(json.dumps(p['effects'], indent=1))
            _set(pid, rid, status='done', finished=time.strftime('%Y-%m-%d %H:%M:%S'),
                 files={'sheet': 'keys.jpg', 'keys': [f'keys/{path.name}' for _f, path, _h in key_list]})
            return
        plate_structure = d / f"structure_plate_native1080_{render_length(p['frames'])}.mp4"
        if not plate_structure.exists():
            media.structure_video(plate_dir(p), p['frames'], plate_structure)
        if r['kind'] == 'environment':
            inp = rd / 'inputs'; inp.mkdir(exist_ok=True)
            scene = inp / 'scene.png'
            shutil.copyfile(d / r['location_reference'], scene)
            opening = inp / 'opening.png'
            image = cv2.imread(str(d / r['opening_reference']))
            cv2.imwrite(str(opening), media.pad(image))
            (inp / 'hashes.json').write_text(json.dumps({str(scene.name): hashlib.sha256(scene.read_bytes()).hexdigest(),
                                                       str(opening.name): hashlib.sha256(opening.read_bytes()).hexdigest()}, indent=1))
            selected_scene = next((l for l in p.get('locations', []) if l['file'] == r['location_reference']), {})
            cfg=people.validate(p.get('people',{}),p['frames'])
            matte=people.current(p)
            extra_people=bool(matte) and cfg['mode']=='guide' and cfg['extra_reference']
            separate_scene=r.get('reference_mode')=='scene_and_opening'
            prompt = r.get('prompt_override')
            if prompt is None: prompt = prompts.environment_prompt(p.get('shot', ''), p.get('location', ''),
                                                location.describe_dressing(selected_scene.get('scene_objects', selected_scene.get('dressing', []))), location.LIGHTING.get(p.get('lighting', ''), p.get('lighting', '')),
                                                opening_guided=True,opening_picture=2 if separate_scene else 1,
                                                extra_people_reference=extra_people)
            if r.get('prompt_override') is None and matte and cfg['mode']=='cutout':
                prompt = ('subject_definitions:\n<Subject 1> is the empty location in <Picture 1>.\n\n'
                    'summary: [video editing] Rebuild the location as a clean empty environment.\n\n'
                    'retention_analysis:\n<Picture 1>: fully_preserved. Empty location layout, perspective and lighting.\n'
                    '<Video 1>: attribute_transfer. Camera motion and timing only. Its people and studio equipment are removed.\n\n'
                    'detailed_description:\nLive-action footage. [Shot 1] The empty location from <Picture 1> follows the exact camera '
                    'movement of <Video 1>. Retain the floor and architecture with physically consistent perspective. '
                    'The scene is unoccupied throughout. The foreground people will be composited separately. '
                    'No new action or visible effects.\n\noverall_soundscape: N/A\n\nnon_diegetic_music: N/A')
            graph, uploads = graphs.environment_graph(prompt, scene, plate_structure, r['seed'], f'keyfx/{pid}/env_{rid}', opening=opening, n_frames=p['frames'])
            if separate_scene:
                graph['cond']['inputs']['ref_images.ref_image_1']=['img_opening',0]
            if extra_people:
                guide=people.guide_video(plate_dir(p),p['frames'],d/'renders'/matte['id']/'masks',cfg,rd/'people_reference.mp4')
                graph['people_video']={'class_type':'LoadVideo','inputs':{'file':'people.mp4'}}
                graph['people_frames']={'class_type':'GetVideoComponents','inputs':{'video':['people_video',0]}}
                graph['cond']['inputs']['ref_videos.ref_video_1']=['people_frames',0]
                uploads['people_video']=('file',guide)
        else:
            bd = base_dir(p)
            structure = plate_structure
            if p['mode'] == 'greenscreen':
                structure = rd / 'structure.mp4'
                media.structure_video(bd, p['frames'], structure)
            anchor = rd / 'anchor.png'
            cv2.imwrite(str(anchor), media.pad(media.read_frame(bd, 0, native=True)))
            key_list = _build_keys(p, r, rd)
            k0 = next((path for f, path, _h in key_list if f == 0), None)
            if k0 is not None:                       # an effect already going at frame 0: the first frame shows it
                shutil.copyfile(k0, anchor)
            look_files = [Path(presets.get(pid_)['dir']) / f for pid_, f, _ in prompts.looks(p['effects'])]
            prompt = prompts.effect_prompt(p.get('shot', ''), p['effects'], guide_mode=r.get('guide_mode', 'full'))
            graph, uploads = graphs.effect_graph(prompt, look_files, structure, anchor, key_list, r['strength'], r['seed'],
                                                 f'keyfx/{pid}/fx_{rid}', n_frames=p['frames'])
            (rd / 'effects.json').write_text(json.dumps(p['effects'], indent=1))
        (rd / 'prompt.txt').write_text(prompt)
        (rd / 'graph.json').write_text(json.dumps(graph, indent=1))
        if r['kind'] == 'environment':
            _set(pid, rid, status='uploading', scene_phase='draft', progress={}, job=None)
            draft_files = comfy.run_graph(graph, uploads, 'save', rd / 'draft' / 'raw',
                                         on_status=lambda job, s: _scene_status(pid, rid, 'draft', job, s),
                                         on_progress=lambda v: _progress(pid, rid, v, phase='draft'))
            draft = draft_files[0]
            _scene_video_check(draft, SCENE_DRAFT_W, SCENE_DRAFT_H, p['frames'])
            # Only the clean draft reaches the finish. Keep every grid frame so
            # the VAE latent and full-sequence guide stay aligned from frame zero.
            strength = r.get('lms_strength', SCENE_LMS_STRENGTH)
            hd_graph, hd_uploads = graphs.scene_upscale_graph(draft, r['seed'], f'keyfx/{pid}/env_hd_{rid}', p['frames'], strength)
            (rd / 'upscale_graph.json').write_text(json.dumps(hd_graph, indent=1))
            (rd / 'upscale_prompt.txt').write_text(SCENE_UPSCALE_PROMPT)
            (rd / 'scene_recipe.json').write_text(json.dumps({
                'profile': r.get('render_profile', SCENE_PROFILE), 'lms_strength': strength,
                'draft_canvas': [SCENE_DRAFT_W, SCENE_DRAFT_H], 'final_canvas': [RENDER_W, RENDER_H],
                'output_size': [RENDER_CW, RENDER_CH], 'frames': p['frames'], 'model_frames': render_length(p['frames']),
                'fps': FPS, 'seed': r['seed'], 'draft_file': str(draft.relative_to(rd)),
                'draft_sha256': hashlib.sha256(draft.read_bytes()).hexdigest(),
                'finishing_inputs': 'Clean raw draft only; full sequence aligned from frame zero',
            }, indent=1))
            _set(pid, rid, status='uploading', scene_phase='upscale', progress={}, job=None,
                 draft_file=str(draft.relative_to(rd)))
            files = comfy.run_graph(hd_graph, hd_uploads, 'save', rd / 'raw',
                                   on_status=lambda job, s: _scene_status(pid, rid, 'upscale', job, s),
                                   on_progress=lambda v: _progress(pid, rid, v, phase='upscale'))
            _scene_video_check(files[0], RENDER_W, RENDER_H, p['frames'])
        else:
            _set(pid, rid, status='uploading')
            files = comfy.run_graph(graph, uploads, 'save', rd / 'raw',
                                   on_status=lambda job_id, s: _set(pid, rid, status='rendering' if s == 'running' else 'downloading', job=job_id),
                                   on_progress=lambda v:_progress(pid,rid,v))
        raw = files[0]
        if media.read_frame(plate_dir(p),0).shape[:2] == (RENDER_CH,RENDER_CW):
            size=media.probe(raw)
            if (size['width'],size['height']) != (RENDER_W,RENDER_H):
                raise RuntimeError('The video returned below or outside the native 1920x1088 canvas; it was not accepted.')
        _set(pid, rid, status='finishing')
        if r['kind'] in ('environment','effect') and people.signature(p) and p.get('people',{}).get('mode')=='cutout':
            matte=people.current(p)
            raw=people.composite(raw,plate_dir(p),p['frames'],d/'renders'/matte['id']/'masks',people.validate(p['people'],p['frames']),rd/'composited.mp4')
        res = media.finish_render(raw, p['frames'], plate_dir(p), rd, crf=10 if r['kind'] == 'environment' else 16)
        review={}
        if r['kind'] == 'environment':
            media.video_to_frames(raw, p['frames'], rd / 'frames')
            try:
                review=quality.review_scene(rd/'result.mp4',d/r['opening_reference'],rd/'scene_review.json')
                res['review']='scene_review.json'
            except (OSError,ValueError,cv2.error):
                review={'warnings':['The automatic scene review was unavailable. Check the result before approving.']}
            # Completion never overwrites the active environment. Explicit approval selects it.
        _set(pid, rid, status='done', files=res, quality_warnings=review.get('warnings',[]),finished=time.strftime('%Y-%m-%d %H:%M:%S'))
    except Exception as exc:  # report, keep the render entry
        traceback.print_exc()
        current = next(x for x in load(pid)['renders'] if x['id'] == rid)
        prefix = ('HD finish failed; the smaller scene is saved. ' if current.get('scene_phase') == 'upscale' else '')
        _set(pid, rid, status='error', error=f'{prefix}{type(exc).__name__}: {exc}'[:500])


# ---------------------------------------------------------------- steps: plate -> big effects -> muzzle flashes -> export

def stages(p: dict) -> list[dict]:
    """One scene context, with completed effects takes available above it."""
    base = source_stage(p)
    out = [{'id': base, 'label': 'Source plate' if base == 'plate' else 'Scene' + ('' if approved(p) else ' - review needed')}]
    for r in reversed(p['renders']):
        if r['kind'] == 'effect' and r['status'] == 'done' and not r.get('review_only') and render_current(p, r):
            out.append({'id': f"render:{r['id']}", 'label': f"Effects take - {r['created'][11:16]}"})
    return out


def flash_base(p: dict) -> str:
    ids = [s['id'] for s in stages(p)]
    # A scene/plate selection must never bypass effects when an effects take exists.
    if p.get('effects'):
        rendered = [i for i in ids if i.startswith('render:')]
        if rendered: return p['flash_base'] if p.get('flash_base') in rendered else rendered[-1]
        return source_stage(p)
    return source_stage(p)


def stage_dir(p: dict, stage: str) -> Path:
    d = pdir(p['id'])
    if stage == 'plate':
        return plate_dir(p)
    if stage == 'environment':
        stage = environment_stage(p)
    if stage and (stage.startswith('render:') or stage.startswith('environment:')):
        kind = 'effect' if stage.startswith('render:') else 'environment'
        rid = stage.split(':', 1)[1]
        r = next((r for r in p['renders'] if r['id'] == rid and r['kind'] == kind and r['status'] == 'done'), None)
        if r is None:
            raise ValueError(f'unknown stage {stage}')
        rd = d / 'renders' / rid
        if r.get('render_profile') == 'native-ltx-elements-v1':
            fd = rd / 'display_frames'
            if len(list(fd.glob('*.png'))) != p['frames']:
                raise ValueError('The native composite display sequence is incomplete.')
            return fd
        fd = rd / 'frames_native_v1'
        legacy = rd / 'frames'
        # Frame-only archived projects still work; video-backed stages always
        # rebuild once from their master so old downsampled caches are ignored.
        has_master = (rd/'composited.mp4').is_file() or (rd/'result.mp4').is_file() or any((rd/'raw').glob('*.mp4'))
        if not has_master and len(list(legacy.glob('*.png'))) == p['frames']:
            return legacy
        with _frame_lock:
            if not fd.exists() or len(list(fd.glob('*.png'))) != p['frames']:
                raw = rd / 'composited.mp4'
                if not raw.is_file():
                    raw = next((rd / 'raw').glob('*.mp4'), rd / 'result.mp4')
                if not raw.is_file():
                    raise ValueError(f'Stage {stage} has no video file.')
                if media.video_to_frames(raw, p['frames'], fd) != p['frames']:
                    raise ValueError(f'Stage {stage} has an incomplete frame sequence.')
        return fd
    raise ValueError(f'unknown stage {stage}')


_spent = 0                      # credits reserved by flash jobs in this server run
_spent_lock = threading.Lock()
_frame_lock = threading.Lock()


def frame_hash(p: dict, base: str, f: int) -> str:
    if p.get('native_profile'):
        return hashlib.sha256((native_frame_dir(p, base)/f'{f:05d}.png').read_bytes()+
                              json.dumps(p['native_profile'],sort_keys=True).encode()).hexdigest()
    return hashlib.sha256(media.frame_path(stage_dir(p, base), f).read_bytes()).hexdigest()


def native_frame_dir(p: dict, base: str) -> Path:
    if base == 'plate': return pdir(p['id'])/'native/frames'
    if base.startswith('render:'):
        render=next((r for r in p.get('renders',[]) if r['id']==base.split(':',1)[1]),None)
        if render and render.get('review_only'):
            raise ValueError('This is an unapproved review composite. Approve the elements and rebuild the composite before finishing.')
        return pdir(p['id'])/'renders'/base.split(':',1)[1]/'master_frames'
    raise ValueError('Native plate workflow cannot use a generated environment as its photographic master.')


def flash_current(p: dict, f: dict, base: str | None = None) -> bool:
    base = base or flash_base(p)
    if not isinstance(f.get('frame'), int) or not 0 <= f['frame'] < p['frames']:
        return False
    if not f.get('result') or not (pdir(p['id']) / f['result']).is_file():
        return False
    if p.get('native_profile') and (not f.get('native_result') or not (pdir(p['id'])/f['native_result']).is_file()):
        return False
    try:
        if f.get('result_model', 'nano') != image_models.selected(p, 'flash'):
            return False
        if f.get('result_base') == base and f.get('result_frame_hash'):
            return f['result_frame_hash'] == frame_hash(p, base, f['frame'])
        # Legacy unversioned results are unverified unless their actual saved
        # model input proves which immutable frame was used. Never guess by date.
        if ((f.get('result_base') == 'environment' and base == environment_stage(p)) or
                (f.get('result_base') == 'plate' and base == 'plate')):
            clean = (pdir(p['id']) / f['result']).parent / 'clean.png'
            old = cv2.imread(str(clean)) if clean.is_file() else None
            current = cv2.imread(str(media.frame_path(stage_dir(p, base), f['frame'])))
            return old is not None and np.array_equal(old, flashes.to_model_canvas(current))
    except (ValueError, OSError):
        pass
    return False


def flash_signature(p: dict) -> str:
    used = sorted((f['id'], f['frame'], f.get('origin'), f.get('tip'), f['result'] if flash_current(p, f) else None)
                  for f in p.get('flashes', []))
    return hashlib.sha256(json.dumps(used).encode()).hexdigest()


def add_flash(pid: str, frame: int, origin: dict, tip: dict) -> dict:
    def fn(p):
        p.setdefault('flashes', []).append({'id': uuid.uuid4().hex[:6], 'frame': int(frame), 'origin': origin, 'tip': tip,
                                            'seed': 184921 + len(p['flashes']), 'status': 'new'})
    return update(pid, fn)


def flash_cost(n: int) -> dict:
    budget = flashes.budget_credits()
    with _spent_lock:
        left = None if budget is None else max(0, budget - _spent)
    return {'per_flash': flashes.CREDITS_PER_FLASH, 'requested': n * flashes.CREDITS_PER_FLASH,
            'allowance_left': left, 'unlimited': budget is None}


def validate_flash_frames(p: dict) -> None:
    frames = [f['frame'] for f in p.get('flashes', [])]
    if len(set(frames)) != len(frames):
        raise ValueError('Use one muzzle flash per frame. Move or remove flashes that share a frame before generating.')


def start_pipeline(pid: str, layers=None, *, collider_input: str | None = None) -> dict:
    """One user action, persisted progress: effects video -> muzzle edits -> splice."""
    with _lock(pid):
        p = load(pid)
        require_approved(p)
        validate_flash_frames(p)
        if generation_active(p) or any(
                f['status'] in ('queued', 'generating') for f in p.get('flashes', [])):
            raise ValueError('Wait for the current generation to finish.')
        for f in p.get('flashes', []): flashes.geometry(f)
        reusable = next((r for r in p['renders'] if r['kind'] == 'effect' and r['status'] == 'done'
                         and r.get('effect_signature') == effect_signature(p) and render_current(p, r)), None)
        if element_jobs.enabled(p) and p.get('effects') and reusable is None:
            raise ValueError('Generate current element RGB/alpha and composite them before the muzzle/final step.')
        if p.get('effects') and reusable is None:
            _require_collider_layers(p, layers, collider_input)
        target = f"render:{reusable['id']}" if p.get('effects') and reusable else source_stage(p)
        count = sum(not flash_current(p, f, target) or bool(p.get('effects') and not reusable) for f in p.get('flashes', []))
        cost = flash_cost(count)
        if not cost['unlimited'] and cost['requested'] > cost['allowance_left']:
            raise ValueError('The muzzle-frame allowance is too low for this take. No video generation was started.')
        rid = time.strftime('%H%M%S-') + uuid.uuid4().hex[:4]
        rd = pdir(pid) / 'renders' / rid; rd.mkdir(parents=True)
        r = {'id': rid, 'kind': 'pipeline', 'status': 'queued', 'files': {}, 'seed': p['settings'].get('seed', 42),
             'strength': '', 'pid': os.getpid(), 'created': time.strftime('%Y-%m-%d %H:%M:%S')}
        if collider_input:
            r['collider_input'] = collider_input
        (rd / 'request.json').write_text(json.dumps({'project': p, 'render': r}, indent=1))
        if layers:
            ld = rd / 'layers'; ld.mkdir()
            for f, files in layers.items():
                for name, data in files.items(): (ld / f'{name}_{int(f):04d}.png').write_bytes(data)
        p['renders'].insert(0, r); save(p)
    threading.Thread(target=_run_pipeline, args=(pid, rid), daemon=True).start()
    return r


def _run_pipeline(pid: str, rid: str) -> None:
    try:
        rd = pdir(pid) / 'renders' / rid
        p = load(pid)
        if p.get('effects'):
            _set(pid, rid, status='effects')
            effect = next((r for r in p['renders'] if r['kind'] == 'effect' and r['status'] == 'done'
                           and r.get('effect_signature') == effect_signature(p) and render_current(p, r)), None)
            if effect is None:
                layers = {}
                for f in (rd / 'layers').glob('*.png'):
                    name, frame = f.stem.rsplit('_', 1)
                    layers.setdefault(int(frame), {})[name] = f.read_bytes()
                pipeline = next(r for r in p['renders'] if r['id'] == rid)
                effect = start_render(pid, 'effect', layers or None, _background=False, collider_input=pipeline.get('collider_input'))
                _set(pid, rid, effect_id=effect['id'])
                _run(pid, effect['id'])
                effect = next(r for r in load(pid)['renders'] if r['id'] == effect['id'])
                if effect['status'] != 'done': raise RuntimeError(effect.get('error', 'Effects video failed.'))
            update(pid, lambda q: q.update(flash_base=f"render:{effect['id']}"))
        else:
            update(pid, lambda q: q.update(flash_base=source_stage(q)))
        p = load(pid)
        if any(not flash_current(p, f) for f in p.get('flashes', [])):
            _set(pid, rid, status='flashes')
            start_flashes(pid, _background=False)
            p = load(pid)
            failures = [f for f in p.get('flashes', []) if not flash_current(p, f)]
            if failures: raise RuntimeError(failures[0].get('error') or 'Muzzle frames did not finish. Review and retry.')
        _set(pid, rid, status='splicing')
        final = export_final(pid)
        _set(pid, rid, status='done', final_id=final['id'], finished=time.strftime('%Y-%m-%d %H:%M:%S'))
    except Exception as exc:
        _set(pid, rid, status='error', error=f'{type(exc).__name__}: {exc}'[:500])


def start_flashes(pid: str, ids: list[str] | None = None, *, _background=True) -> dict:
    """Queue paid flash edits for flashes with no current result (or the given ids). Refuses beyond the allowance."""
    global _spent
    with _lock(pid):
        p = load(pid)
        require_approved(p)
        validate_flash_frames(p)
        base = flash_base(p)
        if any(r['kind'] == 'effect' and r['status'] not in ('done','error') for r in p['renders']):
            raise ValueError('Wait for the effects video before generating muzzle frames.')
        if p.get('effects') and not base.startswith('render:'):
            raise ValueError('Generate the effects video before muzzle frames.')
        todo = [f for f in p.get('flashes', []) if f['status'] not in ('queued', 'generating') and
                ((ids and f['id'] in ids) or (not ids and not flash_current(p, f, base)))]
        if not todo:
            raise ValueError('no flashes need generating')
        for f in todo:
            flashes.geometry(f)                               # validate before spending
        need = len(todo) * flashes.CREDITS_PER_FLASH
        with _spent_lock:
            budget = flashes.budget_credits()
            if budget is not None and _spent + need > budget:
                raise ValueError(f'flash allowance exceeded: {len(todo)} flashes ≈ {need} credits, '
                                 f'{max(0, budget - _spent)} left (start the server with KEYFX_FLASH_CREDITS to allow paid flashes)')
            _spent += need
        want = {f['id'] for f in todo}
        for flash in p['flashes']:
            if flash['id'] in want:
                flash.update(status='queued', error=None)
        model = image_models.selected(p, 'flash')
        for f in todo: f['_model'] = model
        save(p)
    if _background:
        threading.Thread(target=_run_flashes, args=(pid, copy.deepcopy(todo), base), daemon=True).start()
    else:
        _run_flashes(pid, copy.deepcopy(todo), base)
    return {'queued': len(todo), 'credits': need}


def _run_flashes(pid: str, todo: list[dict], base: str) -> None:
    for f in todo:                                       # geometry and exact base frozen at submission
        fid = f['id']
        def setf(**kw):
            update(pid, lambda q: [f.update(**kw) for f in q['flashes'] if f['id'] == fid])
        try:
            p = load(pid)
            setf(status='generating')
            frame = cv2.imread(str(media.frame_path(stage_dir(p, base), f['frame'])))
            out = pdir(pid) / 'flashes' / f"{fid}_{time.strftime('%H%M%S')}"
            model = f.get('_model', 'nano')
            native_result = None
            if p.get('native_profile'):
                native = native_media.read_native_frame(native_frame_dir(p,base),f['frame'])
                display = native_colour.display_frame(native,p['native_profile'])
                edited = flashes.generate_native_display(display,f,f['seed'],out,model=model)
                changed = np.any(edited != display,axis=2).astype(np.float32)
                final_native = native_media.lift_display_edit(native,display,edited,p['native_profile'],changed)
                native_media.write_native_frame(out,0,final_native)
                (out/'00000.png').rename(out/'native.png')
                native_result = str((out/'native.png').relative_to(pdir(pid)))
                img = cv2.cvtColor(native_colour.display_frame(final_native,p['native_profile']),cv2.COLOR_RGB2BGR)
            else:
                img = flashes.generate(frame, f, f['seed'], out, **({'model': model} if model != 'nano' else {}))
            cv2.imwrite(str(out / 'frame.png'), img)
            setf(status='done', result_model=model, result=str(out.relative_to(pdir(pid)) / 'frame.png'), result_base=base,
                 native_result=native_result,
                 result_frame_hash=frame_hash(p, base, f['frame']),
                 prompt=(out / 'prompt.txt').read_text())
        except Exception as exc:
            traceback.print_exc()
            setf(status='error', error=f'{type(exc).__name__}: {exc}'[:300])


def export_final(pid: str, omit_unready: bool = False) -> dict:
    """Step 4: the chosen version with its flash frames swapped in, plus a side-by-side against the original plate."""
    p = load(pid)
    require_approved(p)
    validate_flash_frames(p)
    base = flash_base(p)
    if p.get('effects') and not base.startswith('render:'):
        raise ValueError('Generate the effects video before finishing the shot.')
    unready = [f['id'] for f in p.get('flashes', []) if not flash_current(p, f, base)]
    if unready and not omit_unready:
        raise ValueError(f'{len(unready)} flash(es) are outdated, unverified or not generated. Explicitly omit them or regenerate before export.')
    bd = stage_dir(p, base)
    if p.get('native_profile'):
        rid = time.strftime('%H%M%S-')+uuid.uuid4().hex[:4]
        rd = pdir(pid)/'renders'/rid; fd = rd/'master_frames'; fd.mkdir(parents=True)
        native_swaps = {f['frame']:pdir(pid)/f['native_result'] for f in p.get('flashes',[]) if flash_current(p,f,base)}
        for i in range(p['frames']):
            src = native_swaps.get(i,native_frame_dir(p,base)/f'{i:05d}.png')
            shutil.copy2(src,fd/f'{i:05d}.png')
        res = native_media.export_native(fd,p['native_profile'],rd,
                                        source_audio=pdir(pid)/'native'/p['native_profile']['source_file'])
        r={'id':rid,'kind':'final','status':'done','created':time.strftime('%Y-%m-%d %H:%M:%S'),'seed':0,'strength':'',
           'base':base,'files':{**res,'result':'result.mp4','master':'master.mov','poster':'poster.jpg'},
           'flashes':sorted(native_swaps),'flash_signature':flash_signature(p),'omitted_flashes':unready}
        update(pid,lambda q:q['renders'].insert(0,r))
        return r
    root = (pdir(pid) / 'flashes').resolve()
    swaps = {}
    for f in p.get('flashes', []):
        if flash_current(p, f, base):
            path = (pdir(pid) / f['result']).resolve()
            if root in path.parents and path.is_file():
                swaps[int(f['frame'])] = path
    rid = time.strftime('%H%M%S-') + uuid.uuid4().hex[:4]
    rd = pdir(pid) / 'renders' / rid; rd.mkdir(parents=True)
    raw = rd / 'final_raw.mp4'
    first = media.read_frame(bd, 0, native=True)
    height, width = first.shape[:2]
    w = media._writer(raw, width, height, lossless=True)
    for i in range(p['frames']):
        f = cv2.imread(str(swaps[i] if i in swaps else media.frame_path(bd, i)))
        if f is None or f.shape[:2] != (height, width):
            w.stdin.close(); w.wait()
            raise ValueError('A final or muzzle frame has the wrong resolution. Regenerate it for the selected video.')
        w.stdin.write(f.tobytes())
    w.stdin.close(); w.wait()
    res = media.finish_render(raw, p['frames'], plate_dir(p), rd)
    raw.unlink(missing_ok=True)
    r = {'id': rid, 'kind': 'final', 'status': 'done', 'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'seed': 0, 'strength': '',
         'base': base, 'flashes': sorted(swaps), 'files': res, 'finished': time.strftime('%Y-%m-%d %H:%M:%S')}
    r.update(flash_signature=flash_signature(p), omitted_flashes=unready)
    update(pid, lambda q: q['renders'].insert(0, r))
    return r


_SERVER_FLASH_KEYS = ('id', 'seed', 'status', 'result', 'native_result', 'result_base', 'result_frame_hash', 'result_model', 'error', 'prompt')


def merge_flash_edits(p: dict, incoming: list) -> None:
    """PATCH from the browser: it may move, resize, retime or delete existing flashes, never set ids, results or status."""
    import re
    old = {f['id']: f for f in p.get('flashes', [])}
    out = []
    for f in incoming:
        fid = str(f.get('id', ''))
        if not re.fullmatch(r'[0-9a-f]{6}', fid) or fid not in old:
            continue
        cur = old[fid]
        edit = {}
        if isinstance(f.get('frame'), int) and 0 <= f['frame'] < p['frames']:
            edit['frame'] = f['frame']
        for k in ('origin', 'tip'):
            v = f.get(k)
            if isinstance(v, dict) and all(isinstance(v.get(a), (int, float)) and 0 <= v[a] <= 1 for a in ('x', 'y')):
                edit[k] = {'x': float(v['x']), 'y': float(v['y'])}
        if any(edit.get(k, cur.get(k)) != cur.get(k) for k in ('frame', 'origin', 'tip')):
            if cur.get('status') in ('queued', 'generating'):
                raise ValueError('Wait for this flash to finish before changing its geometry.')
            cur = {**cur, **edit, 'status': 'new', 'result': None, 'result_base': None, 'result_frame_hash': None}
        out.append(cur)
    p['flashes'] = out
