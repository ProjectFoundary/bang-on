"""Bang On web API + static UI."""
from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
import os
import re
import uuid
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import presets, projects, setdressing, image_models, people, room_refs, connections, native_media, element_jobs
from .config import CH, CW, WEB_DIST

@asynccontextmanager
async def lifespan(app: FastAPI):
    _recover()
    yield


app = FastAPI(title='Bang On', lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=2000)
from .element_api import router as element_router
app.include_router(element_router)
from .layer_export_api import router as layer_export_router
app.include_router(layer_export_router)
from .people_alpha_api import router as people_alpha_router
app.include_router(people_alpha_router)
from .simulation_preview import router as simulation_preview_router
app.include_router(simulation_preview_router)
from .finishing_api import router as finishing_router
app.include_router(finishing_router)

# The server runs on the user's machine and can spend their Comfy credits, so state-changing requests must come from the
# KeyFX page itself: the Host must be one this server answers to (blocks DNS rebinding), and a browser's Origin must match it
# (blocks cross-site form posts). Extra host names can be allowed with KEYFX_ALLOWED_HOSTS=host1,host2.
_LOCAL = {'127.0.0.1', 'localhost', '[::1]'} | {h.strip() for h in os.environ.get('KEYFX_ALLOWED_HOSTS', '').split(',') if h.strip()}


@app.middleware('http')
async def same_origin_only(request: Request, call_next):
    host = request.headers.get('host', '')
    name = host.rsplit(':', 1)[0] if not host.startswith('[') else host.split(']')[0] + ']'
    if name not in _LOCAL:
        return PlainTextResponse('host not allowed', status_code=403)
    match = re.match(r'^/api/projects/([^/]+)(?:/|$)', request.url.path)
    if match:
        try:
            projects.pdir(match.group(1))
            render = re.match(r'^/api/projects/[^/]+/renders/([^/]+)(?:/|$)', request.url.path)
            if render:
                projects.validate_id(render.group(1), 'render')
        except ValueError as exc:
            return JSONResponse({'detail': str(exc)}, status_code=400)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and urlparse(origin).netloc != host:
            return PlainTextResponse('cross-origin request refused', status_code=403)
        match = re.match(r'^/api/projects/([^/]+)(?:/|$)', request.url.path)
        if match:
            try:
                if projects.pipeline_active(projects.load(match.group(1))):
                    return JSONResponse({'detail': 'This take is generating. Wait before changing its inputs.'}, status_code=409)
            except FileNotFoundError:
                pass
    response = await call_next(request)
    if request.url.path in ('/', '/index.html'):
        response.headers['Cache-Control'] = 'no-store'
    return response


def _recover() -> None:
    from .bundled_examples import install_defaults
    install_defaults()
    projects.recover()


def _project(pid: str) -> dict:
    try:
        return projects.public(projects.load(pid))
    except FileNotFoundError:
        raise HTTPException(404, 'no such project')


def _connection_idle() -> None:
    # A connection belongs to the installation. Do not switch its account between take stages.
    for path in (projects.DATA / 'projects').glob('*/project.json'):
        try:
            p = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if any(r.get('status') not in ('done', 'error') and (not r.get('pid') or r['pid'] == os.getpid() or projects._alive(r['pid'])) for r in p.get('renders', [])) or any(
            f.get('status') in ('queued', 'generating') for f in p.get('flashes', [])
        ):
            raise HTTPException(409, 'Wait for the current generation to finish before changing its Comfy connection.')


@app.get('/api/connection')
def get_connection():
    return connections.summary()


@app.post('/api/connection/test')
def test_connection(body: dict):
    try:
        return connections.test(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.put('/api/connection')
def save_connection(body: dict):
    _connection_idle()
    try:
        return connections.save(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete('/api/connection')
def disconnect_connection():
    _connection_idle()
    return connections.disconnect()


@app.get('/api/presets')
def list_presets():
    return [presets.public(p) for p in presets.load_all().values()]


@app.get('/api/presets/{pid}/{name}')
def preset_file(pid: str, name: str):
    p = presets.get(pid)
    f = (Path(p['dir']) / name).resolve()
    if f.parent != Path(p['dir']).resolve() or not f.exists():
        raise HTTPException(404)
    return FileResponse(f)


@app.get('/api/projects')
def list_projects():
    return projects.list_all()


@app.get('/api/projects/{pid}/example-media/{reference_id}')
def example_media(pid: str, reference_id: str):
    p = _project(pid)
    reference = next((r for r in p.get('bundled_example', {}).get('references', []) if r['id'] == reference_id), None)
    if not reference: raise HTTPException(404, 'Example reference not found.')
    root = projects.pdir(pid).resolve()
    path = (root / reference['file']).resolve()
    if not path.is_relative_to(root) or not path.is_file(): raise HTTPException(404, 'Example reference not found.')
    return FileResponse(path, media_type='video/mp4')


@app.post('/api/projects')
def create_project(file: UploadFile = File(...), name: str = Form(''), mode: str = Form('plate'), start_s: float = Form(0.0),
                   length_s: float = Form(0.0), analyse_action: bool = Form(True), preserve_native: bool = Form(False),
                   colour_profile: str = Form('auto'), rgb_range: Literal['preserve','legalize'] = Form('legalize')):
    if mode not in ('plate', 'greenscreen'):
        raise HTTPException(400, 'mode must be plate or greenscreen')
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / Path(file.filename or 'plate.mp4').name
        with src.open('wb') as f:
            shutil.copyfileobj(file.file, f)
        try:
            options = {}
            if preserve_native and mode == 'plate':
                overrides = (native_media.colour_profile_overrides(colour_profile,native_media.inspect_source(src))
                             if colour_profile != 'auto' else None)
                options = {'preserve_native':True,'colour_overrides':overrides,'rgb_range':rgb_range}
            p=projects.create(src, name or Path(file.filename or 'plate').stem, mode, start_s, length_s or None, **options)
            if analyse_action and mode == 'greenscreen' and not preserve_native: projects.start_render(p['id'],'analysis')
            return projects.public(projects.load(p['id']))
        except ValueError as exc: raise HTTPException(400, str(exc))


@app.get('/api/projects/{pid}')
def get_project(pid: str):
    return _project(pid)


class Patch(BaseModel):
    action_review: str | None = None
    mode: Literal['plate','greenscreen'] | None = None
    camera_mode: Literal['auto','locked'] | None = None
    people: dict | None = None
    image_models: dict | None = None
    name: str | None = None
    shot: str | None = None
    location: str | None = None
    effects: list | None = None
    settings: dict | None = None
    flashes: list | None = None
    flash_base: str | None = None
    dressing: list | None = None
    lighting: str | None = None
    location_frame: int | None = None
    background: str | None = None
    empty_set: bool | None = None
    opening_reference: str | None = None
    reference_mode: Literal['opening_only', 'scene_and_opening'] | None = None
    location_role: Literal['opening', 'scene'] | None = None
    location_seed: int | None = None
    environment_seed: int | None = None
    set_dressing: dict | None = None


@app.patch('/api/projects/{pid}')
def patch_project(pid: str, body: Patch):
    existing = _project(pid)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if existing.get('native_profile') and changes.get('mode', 'plate') != 'plate':
        raise HTTPException(400, 'Native plate projects keep the original photography. Create a separate Scene project for environment generation.')
    if projects.pipeline_active(existing):
        raise HTTPException(409, 'This take is generating. Wait before changing its inputs.')
    if 'image_models' in changes:
        try: changes['image_models'] = image_models.validate(changes['image_models'])
        except ValueError as exc: raise HTTPException(400, str(exc))
    if 'settings' in changes:
        try: changes['settings'] = projects.effect_settings({**existing,'settings': changes['settings']})
        except (ValueError, TypeError) as exc: raise HTTPException(400, str(exc))
    candidate = {**existing, **changes}
    if changes.get('camera_mode') == 'locked' and candidate['mode'] != 'plate':
        raise HTTPException(400, 'Locked camera is available for original footage shots.')
    if element_jobs.enabled(candidate):
        try:
            ids=set()
            for effect in candidate.get('effects', []):
                if not isinstance(effect,dict) or not isinstance(effect.get('id'),str) or effect['id'] in ids:
                    raise ValueError('Each element needs a unique effect ID.')
                ids.add(effect['id'])
                element_jobs.effect_config(candidate,effect)
            # Validate before persistence: invalid control edits must never make
            # a saved shot impossible to reopen through public()/signatures.
            projects.effect_signature(candidate)
        except (ValueError,TypeError,KeyError) as exc:
            raise HTTPException(400,str(exc)) from None
    if 'people' in changes:
        try: changes['people']=people.validate(changes['people'],existing['frames'])
        except (ValueError,TypeError,KeyError) as exc: raise HTTPException(400,str(exc))
    if 'action_review' in changes and not any(r['id']==changes['action_review'] and r['kind']=='analysis' and r['status']=='done' for r in existing['renders']):
        raise HTTPException(400,'Choose a completed action draft to review.')
    flash_edits = changes.pop('flashes', None)
    if 'set_dressing' in changes:
        try:
            draft = setdressing.validate(changes['set_dressing'])
            current = projects.load(pid)
            if draft['source'] != current.get('set_dressing', {}).get('source'):
                raise ValueError('Choose a location with Approve for object placement before editing it.')
            if draft['source']: projects.location_entry(current, draft['source'])
            changes['set_dressing'] = draft
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(400, str(exc))
    if 'opening_reference' in changes:
        cur = projects.load(pid)
        cutout=cur.get('people',{}).get('mode')=='cutout' and people.current(cur)
        eligible = {l['file'] for l in cur.get('locations', []) if l.get('frame') == 0 and (not l.get('empty_set') or cutout)}
        if changes['opening_reference'] not in eligible:
            raise HTTPException(400, 'Choose a first-frame reference. An empty reference requires a current Original cutout matte.')
    if 'background' in changes:                        # only a location this project already has
        cur = projects.load(pid)
        if changes['background'] not in {l['file'] for l in cur.get('locations', [])} | {'background.png'}:
            changes.pop('background')
    if 'dressing' in changes:
        changes['dressing'] = [{'id': str(d.get('id', ''))[:12], 'text': str(d.get('text', ''))[:200],
                                'frame': max(0, min(existing['frames'] - 1, int(d.get('frame', 0)))),
                                'box': {k: min(1.0, max(0.0, float(d['box'][k]))) for k in ('x0', 'y0', 'x1', 'y1')} if isinstance(d.get('box'), dict) else None}
                               for d in changes['dressing'] if isinstance(d, dict)]
    if 'flash_base' in changes and not isinstance(changes['flash_base'], str):
        changes.pop('flash_base')

    def fn(p):
        if 'camera_mode' in changes and projects.generation_active(p):
            raise HTTPException(409, 'Wait for the current render before changing camera motion.')
        p.update(changes)
        if 'mode' in changes and changes['mode']!=existing['mode']:
            p['approved_environment']=None
        if flash_edits is not None:
            projects.merge_flash_edits(p, flash_edits)
    try:
        return projects.public(projects.update(pid, fn))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get('/api/projects/{pid}/people/frame/{frame}')
def people_frame(pid: str,frame: int,view: str='overlay',matte: str | None=None,feather: float | None=None,trim: float | None=None):
    p=_project(pid);r=people.current(p)
    if not r or not 0<=frame<p['frames']:raise HTTPException(404,'Build a current people matte first.')
    cfg=people.validate(p.get('people',{}),p['frames'])
    if ((matte is not None and matte!=r['id']) or (feather is not None and feather!=cfg['feather'])
            or (trim is not None and trim!=cfg['trim'])):
        raise HTTPException(409,'People matte changed. Reload before preparing collider guides.')
    rd=projects.pdir(pid)/'renders'/r['id'];a=people.alpha(rd/'masks'/f'{frame:05d}.png',cfg)
    if view!='mask':src=cv2.imread(str(projects.plate_dir(p)/f'{frame:05d}.png'))
    if view=='mask':im=(a[:,:,0]*255).astype('uint8')
    elif view=='cutout':
        import numpy as np
        yy,xx=np.indices(src.shape[:2]);checker=((xx//18+yy//18)%2*25+40)[...,None]
        im=np.clip(src*a+checker*(1-a),0,255).astype('uint8')
    else:
        import numpy as np
        tint=np.zeros_like(src);tint[:]=(150,230,120)
        im=np.clip(src*(1-a*.45)+tint*a*.45,0,255).astype('uint8')
    ok,b=cv2.imencode('.png',im)
    if not ok:raise HTTPException(500,'Could not preview matte.')
    return Response(b.tobytes(),media_type='image/png',headers={'Cache-Control':'no-store'})


@app.get('/api/projects/{pid}/people/cutout')
def people_download(pid: str):
    p=_project(pid);r=people.current(p)
    if not r:raise HTTPException(404,'Build a current people matte first.')
    rd=projects.pdir(pid)/'renders'/r['id'];cfg=p['people'];key=people.signature(p)[:16]
    target=rd/f'cutout_{key}.mov'
    if not target.exists():people.export_cutout(projects.plate_dir(p),p['frames'],rd/'masks',cfg,target)
    return FileResponse(target,media_type='video/quicktime',filename=f'{p["name"]}_people.mov')


class NewEffect(BaseModel):
    preset: str
    frame: int
    x: float
    y: float


@app.post('/api/projects/{pid}/effects')
def add_effect(pid: str, body: NewEffect):
    if projects.pipeline_active(_project(pid)): raise HTTPException(409, 'Wait for this take to finish.')
    return projects.public(projects.add_effect(pid, body.preset, body.frame, body.x, body.y))


@app.post('/api/projects/{pid}/background')
def upload_background(pid: str, file: UploadFile = File(...), role: Literal['opening', 'scene'] = Form('scene')):
    _project(pid)
    data = file.file.read()
    import numpy as np
    im = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        raise HTTPException(400, 'not an image')
    rid = 'upload-' + uuid.uuid4().hex[:10]
    rel = f'locations/{rid}.png'
    path = projects.pdir(pid) / rel; path.parent.mkdir(exist_ok=True)
    cv2.imwrite(str(path), im)
    def keep(p):
        p.setdefault('locations', []).insert(0, {'id': rid, 'file': rel, 'seed': 0, 'frame': 0 if role == 'opening' else None,
                                                'role': role, 'prompt': 'uploaded image; check pose and perspective', 'created': ''})
        p['opening_reference' if role == 'opening' else 'background'] = rel
    return projects.public(projects.update(pid, keep))


@app.post('/api/projects/{pid}/room-references')
def upload_room_references(pid: str, files: list[UploadFile] = File(...)):
    _project(pid)
    if not files or len(files)>room_refs.MAX_IMAGES:
        raise HTTPException(400,f'Upload up to {room_refs.MAX_IMAGES} room photos.')
    try:
        prepared=[room_refs.prepare(f.file.read(room_refs.MAX_BYTES+1),f.filename or '') for f in files]
        def keep(p):
            if projects.generation_active(p):
                raise HTTPException(409,'Wait for this generation to finish before changing room references.')
            refs=p.setdefault('room_references',[])
            if len(refs)+len(prepared)>room_refs.MAX_IMAGES:
                raise ValueError(f'Keep up to {room_refs.MAX_IMAGES} room photos. Remove some before adding more.')
            for ref,data in prepared:
                path=projects.pdir(pid)/ref['file'];path.parent.mkdir(exist_ok=True)
                path.write_bytes(data)
            refs.extend(ref for ref,_ in prepared)
        return projects.public(projects.update(pid,keep))
    except ValueError as exc:
        raise HTTPException(400,str(exc))


@app.get('/api/projects/{pid}/room-references/{rid}')
def room_reference_image(pid: str,rid: str):
    p=_project(pid)
    ref=next((r for r in p.get('room_references',[]) if r['id']==rid),None)
    if not ref:raise HTTPException(404,'Room reference not found.')
    try:path=room_refs.paths(projects.pdir(pid),[ref])[0]
    except ValueError as exc:raise HTTPException(404,str(exc))
    return FileResponse(path,media_type='image/png')


@app.delete('/api/projects/{pid}/room-references/{rid}')
def remove_room_reference(pid: str,rid: str):
    _project(pid)
    def remove(p):
        if projects.generation_active(p):
            raise HTTPException(409,'Wait for this generation to finish before changing room references.')
        if not any(r['id']==rid for r in p.get('room_references',[])):
            raise HTTPException(404,'Room reference not found.')
        p['room_references']=[r for r in p['room_references'] if r['id']!=rid]
        # Keep immutable files for earlier render requests and history.
    return projects.public(projects.update(pid,remove))


@app.post('/api/projects/{pid}/environments/{rid}/approve')
def approve_environment(pid: str, rid: str):
    _project(pid)
    try:
        return projects.public(projects.approve_environment(pid, rid))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class DressingSource(BaseModel):
    file: str


@app.post('/api/projects/{pid}/dressing/source')
def approve_dressing_source(pid: str, body: DressingSource):
    _project(pid)
    try:
        return projects.public(projects.approve_dressing_source(pid, body.file))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get('/api/projects/{pid}/background')
def get_background(pid: str, file: str | None = None):
    p = _project(pid)
    name = file or p.get('background')
    if not name or name not in {l['file'] for l in p.get('locations', [])} | {'background.png'}:
        raise HTTPException(404)
    f = (projects.pdir(pid) / name).resolve()
    if projects.pdir(pid).resolve() not in f.parents or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)


@app.get('/api/projects/{pid}/frame/{i}')
def frame(pid: str, i: int, base: str = 'auto'):
    p = _project(pid)
    try:
        fd = projects.stage_dir(p, projects.source_stage(p) if base == 'auto' else base)
    except (ValueError, OSError):
        raise HTTPException(404)
    f = fd / f'{min(max(i, 0), p["frames"] - 1):05d}.png'
    if not f.exists():
        raise HTTPException(404)
    im = cv2.imread(str(f))
    if im.shape[:2] != (CH, CW):
        im = cv2.resize(im, (CW, CH), interpolation=cv2.INTER_AREA)
    return Response(cv2.imencode('.jpg', im, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes(), media_type='image/jpeg')


@app.get('/api/projects/{pid}/track')
def get_track(pid: str):
    """Auto-track of the frames effects are placed on (computed on first request if missing)."""
    p = _project(pid)
    if projects.camera_locked(p):
        data = json.loads(projects.track_file(p).read_text())
        data['camera'] = [[1, 0, 0, 0] for _ in range(p['frames'])]
        data.pop('H', None)
        return JSONResponse(data, headers={'Cache-Control':'no-store'})
    return FileResponse(projects.track_file(p), media_type='application/json')


@app.get('/api/projects/{pid}/key/{i}')
def key_preview(pid: str, i: int):
    p = _project(pid)
    try:
        return Response(projects.key_preview(p, i), media_type='image/jpeg')
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get('/api/projects/{pid}/prompt')
def prompt(pid: str):
    return PlainTextResponse(projects.prompt_preview(_project(pid)))


@app.post('/api/projects/{pid}/render')
async def render(pid: str, request: Request):
    """JSON {kind} or multipart: kind + fx_NNNN.png / glow_NNNN.png layers rendered by the browser mock-up at each key frame."""
    _project(pid)
    layers: dict[int, dict[str, bytes]] = {}
    prompt_override = None
    collider_input = None
    if request.headers.get('content-type', '').startswith('multipart/'):
        form = await request.form()
        kind = str(form.get('kind', 'effect'))
        collider_input = str(form.get('collider', ''))
        for name, val in form.multi_items():
            m = re.fullmatch(r'(fx|glow)_(\d+)\.png', name)
            if m and hasattr(val, 'read'):
                layers.setdefault(int(m.group(2)), {})[m.group(1)] = await val.read()
    else:
        body = await request.json()
        kind = body.get('kind', 'effect')
        prompt_override = body.get('prompt_override')
    try:
        return projects.start_render(pid, kind, layers or None, prompt_override=prompt_override, collider_input=collider_input)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get('/api/projects/{pid}/renders/{rid}/{name:path}')
def render_file(pid: str, rid: str, name: str):
    try:
        projects.validate_id(rid, 'render')
        root = projects.pdir(pid)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    rd = (root / 'renders' / rid).resolve()
    f = (rd / name).resolve()
    if not rd.is_relative_to(root) or rd not in f.parents or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)


@app.post('/api/projects/{pid}/snap/{name}')
async def snap(pid: str, name: str, request: Request):
    """Save a frame of the live mock-up as PNG (review/debug aid): data/projects/<id>/snaps/<name>.png."""
    _project(pid)
    if not re.fullmatch(r'[\w.-]{1,64}', name):
        raise HTTPException(400, 'bad name')
    d = projects.pdir(pid) / 'snaps'; d.mkdir(exist_ok=True)
    (d / f'{name}.png').write_bytes(await request.body())
    return {'ok': True}


@app.get('/api/projects/{pid}/stages')
def get_stages(pid: str):
    p = _project(pid)
    return {'stages': projects.stages(p), 'flash_base': projects.flash_base(p)}


class NewFlash(BaseModel):
    frame: int
    origin: dict
    tip: dict


@app.post('/api/projects/{pid}/flashes')
def add_flash(pid: str, body: NewFlash):
    if projects.pipeline_active(_project(pid)): raise HTTPException(409, 'Wait for this take to finish.')
    return projects.public(projects.add_flash(pid, body.frame, body.origin, body.tip))


@app.get('/api/projects/{pid}/flashes/cost')
def flash_cost(pid: str, n: int = 1):
    _project(pid)
    return projects.flash_cost(n)


class FlashRun(BaseModel):
    ids: list[str] | None = None


@app.post('/api/projects/{pid}/flashes/generate')
def generate_flashes(pid: str, body: FlashRun):
    _project(pid)
    try:
        return projects.start_flashes(pid, body.ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get('/api/projects/{pid}/flashfile/{name:path}')
def flash_file(pid: str, name: str):
    root = (projects.pdir(pid) / 'flashes').resolve()
    f = (projects.pdir(pid) / name).resolve()
    if root not in f.parents or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)


class ExportOptions(BaseModel):
    omit_unready: bool = False


@app.post('/api/projects/{pid}/export')
def export(pid: str, body: ExportOptions | None = None):
    _project(pid)
    try:
        return projects.export_final(pid, bool(body and body.omit_unready))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get('/api/health')
def health():
    return JSONResponse({'ok': True})


if WEB_DIST.exists():
    app.mount('/', StaticFiles(directory=WEB_DIST, html=True), name='web')
