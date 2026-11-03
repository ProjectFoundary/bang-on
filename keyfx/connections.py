"""Installation-scoped Comfy connections, private persistence and read-only readiness checks."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
import copy
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .config import DATA

CLOUD_URL = 'https://cloud.comfy.org'
LOCAL_URL = 'http://127.0.0.1:8189'
COMFYUI_URL = 'http://127.0.0.1:8188'
_lock = threading.RLock()
_tests: dict[str, tuple[float, str, dict]] = {}


@dataclass(frozen=True)
class Connection:
    mode: str = 'cloud'
    local_url: str = LOCAL_URL
    comfyui_url: str = COMFYUI_URL
    cloud_api_key: str = field(default='', repr=False)
    local_token: str = field(default='', repr=False)
    allow_cloud_partner: bool = False
    enabled: bool = True

    @property
    def configured(self) -> bool:
        return self.enabled and (self.mode == 'local' or bool(self.cloud_api_key))

    @property
    def nano_available(self) -> bool:
        return self.configured and bool(self.cloud_api_key) and (self.mode == 'cloud' or self.allow_cloud_partner)


def _path() -> Path:
    return DATA / 'connection.json'


def _url(value: str) -> str:
    try:
        p = urlsplit(value.strip())
        valid = (p.scheme in ('http', 'https') and p.hostname and not p.username and not p.password
                 and not p.query and not p.fragment and p.port != 0 and not any(c.isspace() for c in value))
        if not valid:
            raise ValueError
    except ValueError:
        raise ValueError('Enter an http:// or https:// address without a password, query or fragment.') from None
    return value.strip().rstrip('/')


def _secret(value: object) -> str:
    if not isinstance(value, str) or len(value) > 4096 or any(c in value for c in '\r\n\x00'):
        raise ValueError('Enter a valid key or token on a single line.')
    return value.strip()


def current() -> Connection:
    with _lock:
        path = _path()
        if path.exists():
            try:
                value = json.loads(path.read_text())
                return Connection(**{k: value[k] for k in Connection.__dataclass_fields__ if k in value})
            except (OSError, ValueError, TypeError):
                # A damaged explicit profile must never fall back to another account.
                raise ValueError('The saved Comfy connection could not be read. Open Connect Comfy to replace it.') from None
        base = os.environ.get('COMFY_BASE_URL', '').strip()
        target = urlsplit(_url(base)) if base else urlsplit(CLOUD_URL)
        cloud = target.scheme == 'https' and target.hostname == 'cloud.comfy.org' and target.port in (None, 443) and not target.path.strip('/')
        return Connection(mode='cloud' if cloud else 'local',
                          local_url=LOCAL_URL if cloud else _url(base),
                          comfyui_url=_url(os.environ.get('COMFYUI_URL', '').strip() or COMFYUI_URL),
                          cloud_api_key=_secret(os.environ.get('COMFY_API_KEY', '')),
                          local_token=_secret(os.environ.get('COMFY_LOCAL_TOKEN', '')))


def draft(body: dict) -> Connection:
    if not isinstance(body, dict) or set(body) - {
        'mode', 'local_url', 'comfyui_url', 'cloud_api_key', 'local_token', 'allow_cloud_partner',
        'clear_cloud_key', 'clear_local_token', 'test_id',
    }:
        raise ValueError('Enter a Cloud or Local connection.')
    try:
        old = current()
    except ValueError:
        old = Connection()
    mode = body.get('mode', old.mode)
    if mode not in ('cloud', 'local'):
        raise ValueError('Choose Comfy Cloud or Local ComfyUI.')
    for name in ('allow_cloud_partner', 'clear_cloud_key', 'clear_local_token'):
        if name in body and not isinstance(body[name], bool):
            raise ValueError('Choose a valid connection option.')
    urls = {}
    for name in ('local_url', 'comfyui_url'):
        value = body.get(name, getattr(old, name))
        if not isinstance(value, str):
            raise ValueError('Enter a valid connection address.')
        urls[name] = _url(value)
    cloud_key = _secret(body.get('cloud_api_key', '')) or old.cloud_api_key
    # Never reuse a proxy's bearer token at a newly entered address.
    local_token = _secret(body.get('local_token', '')) or (old.local_token if urls['local_url'] == old.local_url else '')
    if body.get('clear_cloud_key'):
        cloud_key = ''
    if body.get('clear_local_token'):
        local_token = ''
    cfg = Connection(mode=mode, **urls, cloud_api_key=cloud_key, local_token=local_token,
                     allow_cloud_partner=body.get('allow_cloud_partner', old.allow_cloud_partner))
    if mode == 'cloud' and not cloud_key:
        raise ValueError('Enter your Comfy Cloud API key.')
    if mode == 'local' and cfg.allow_cloud_partner and not cloud_key:
        raise ValueError('Enter a Cloud key for Nano Banana, or turn off Cloud partner access.')
    return cfg


def summary() -> dict:
    try:
        cfg = current()
        path = _path()
        saved = json.loads(path.read_text()) if path.exists() else {}
        error = None
    except (ValueError, OSError):
        cfg, saved = Connection(enabled=False), {}
        error = 'The saved connection needs to be replaced.'
    return {'mode': cfg.mode, 'configured': cfg.configured, 'local_url': cfg.local_url,
            'comfyui_url': cfg.comfyui_url, 'has_cloud_key': bool(cfg.cloud_api_key),
            'has_local_token': bool(cfg.local_token), 'allow_cloud_partner': cfg.allow_cloud_partner,
            'nano_available': cfg.nano_available, 'source': 'saved' if _path().exists() else 'environment',
            'report': saved.get('report'), 'error': error}


def _write(cfg: Connection, report: dict | None = None) -> dict:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.connection-{uuid.uuid4().hex}.tmp')
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump({**asdict(cfg), 'report': report}, f)
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        temp.unlink(missing_ok=True)
    return summary()


def _signature(cfg: Connection) -> str:
    return hashlib.sha256(json.dumps(asdict(cfg), sort_keys=True).encode()).hexdigest()


def save(body: dict) -> dict:
    with _lock:
        cfg = draft(body)
        token = body.get('test_id', '')
        tested = _tests.get(token) if isinstance(token, str) else None
        if not tested or time.monotonic() - tested[0] > 900 or tested[1] != _signature(cfg) or not tested[2]['connected']:
            raise ValueError('Test this connection successfully before saving. If you changed a field, test again.')
        report = {k: v for k, v in tested[2].items() if k != 'test_id'}
        return _write(cfg, report)


def disconnect() -> dict:
    with _lock:
        # Keep an explicit disconnected profile so environment credentials do not silently reappear.
        _tests.clear()
        return _write(Connection(enabled=False))


def _get_json(url: str, key: str = '') -> dict:
    headers = {'Accept': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        value = response.json()
    if not isinstance(value, dict):
        raise ValueError('Unexpected response format.')
    return value


def _cloud_account(key: str) -> str:
    # Cloud's authenticated user route is supported by its current public API.
    # /api/v2/jobs listing is not implemented on Cloud (HTTP501), although the
    # local API proxy and individual SDK job routes use the v2 surface.
    value = _get_json(CLOUD_URL + '/api/user', key)
    status = value.get('status')
    if status not in ('active', 'waitlisted'):
        raise ValueError('Unexpected authenticated Cloud user response.')
    return status


def _cloud_owned_models(key: str) -> list[dict]:
    """Read imported registrations; this never imports/downloads a model."""
    from .comfy import _mcp, _payload
    call = _mcp(key)
    rows, offset = [], 0
    for _ in range(10):
        result = _payload(call('search_models', {'owned': True, 'limit': 100, 'offset': offset}))
        data, metadata = result.get('data'), result.get('metadata', {})
        if not isinstance(data, list) or not isinstance(metadata, dict):
            raise ValueError('Imported model catalogue was unavailable.')
        total = metadata.get('total')
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise ValueError('Imported model catalogue pagination was unavailable.')
        rows.extend(row for row in data if isinstance(row, dict) and row.get('owned_by_user') is True
                    and isinstance(row.get('model_name'), str) and isinstance(row.get('type'), str))
        offset += len(data)
        if offset >= total:
            return rows
        if not data:
            break
    raise ValueError('Imported model catalogue was incomplete.')


def _model_types(kind: str, field: str) -> set[str]:
    groups = {'lora_name': {'lora', 'loras'}, 'unet_name': {'diffusion_model', 'diffusion_models', 'unet'},
              'clip_name': {'clip', 'text_encoder', 'text_encoders'}, 'vae_name': {'vae', 'vae_approx'},
              'ckpt_name': {'checkpoint', 'checkpoints'}}
    if field == 'model_name':
        if kind == 'LoadDA3Model': return {'geometry_estimation'}
        if kind in ('UpscaleModelLoader', 'LatentUpscaleModelLoader'): return {'upscaler', 'upscale_models'}
    if field == 'model' and kind == 'DownloadAndLoadSAM2Model': return {'sam2', 'sams'}
    return groups.get(field, set())


def node_catalog(cfg: Connection) -> dict:
    """Shared node schemas plus account-owned names, scoped to matching loaders.

    Cloud object_info lists shared models only. Its read-only MCP model search
    is authoritative for this account's imported filenames, not weight hashes.
    """
    if cfg.mode != 'cloud':
        return _get_json(cfg.comfyui_url + '/object_info')
    catalog = copy.deepcopy(_get_json(CLOUD_URL + '/api/object_info', cfg.cloud_api_key))
    try:
        owned = _cloud_owned_models(cfg.cloud_api_key)
    except Exception:
        # Public models remain usable. Unlisted imported names must stay
        # unverified if the account lookup failed, rather than being assumed.
        catalog['_bang_owned_models'] = {'status': 'unknown'}
        return catalog
    catalog['_bang_owned_models'] = {'status': 'verified'}
    for kind, info in catalog.items():
        inputs = info.get('input', {}) if isinstance(info, dict) else {}
        for field, definition in {**inputs.get('required', {}), **inputs.get('optional', {})}.items():
            types = _model_types(kind, field)
            if not types or not isinstance(definition, list) or not definition:
                continue
            choices = definition[0]
            if choices == 'COMBO' and len(definition) > 1 and isinstance(definition[1], dict):
                choices = definition[1].get('options')
            if not isinstance(choices, list):
                continue
            for row in owned:
                if row.get('owned_by_user') is True and row['type'] in types and row['model_name'] not in choices:
                    choices.append(row['model_name'])
    return catalog


def _failure(exc: Exception, mode: str) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return 'Authentication was refused. Check your Cloud key or local proxy token.'
        if code == 404 and mode == 'local':
            return 'Comfy API v2 was not found. For local ComfyUI, start comfy-api-proxy and use its address (usually port 8189).'
        return f'The Comfy server returned HTTP {code}. Try again after checking the server.'
    if isinstance(exc, httpx.TimeoutException):
        return 'The connection timed out. Check the address and that Comfy is running.'
    if isinstance(exc, httpx.RequestError):
        return 'Could not reach Comfy. Check the address, network and running server.'
    return 'The address did not return a compatible Comfy API response.'


def requirements() -> list[tuple[str, str, dict, bool]]:
    # Derive requirements from the actual graph builders, rather than a separate list that can drift.
    from . import analysis, flashes, graphs, people, polish, setdressing, element_graphs, geometry
    dummy = Path('connection-check')
    effect, _ = graphs.effect_graph('check', [dummy], dummy, dummy, [(1, dummy, True)], n_frames=5)
    scene, _ = graphs.environment_graph('check', dummy, dummy, opening=dummy, n_frames=5)
    upscale, _ = graphs.scene_upscale_graph(dummy, n_frames=5)
    return element_graphs.requirements() + [('depth', 'Reference depth · optional', geometry.depth_graph('check'), False),
            ('effects', 'Legacy effects video · MiniMax H3', effect, False),
            ('scene', 'Scene video + HD finish', {**scene, **{f'hd_{k}': v for k, v in upscale.items()}}, False),
            ('qwen', 'Qwen Image Edit', polish.graph('check', 1), False),
            ('flux3', 'FLUX 3 Image Edit', setdressing.graph('check', 1), False),
            ('analysis', 'Action draft · optional', analysis.graph(5, 'check'), False),
            ('people', 'People matte · optional', people.graph({**people.DEFAULTS, 'points': [
                {'frame': 0, 'person': 0, 'label': 1, 'x': .5, 'y': .5}]}, 'check'), False),
            ('nano', 'Nano Banana · Cloud', flashes.graph('check', 1), False)]


_MODEL_FIELDS = {'unet_name', 'clip_name', 'vae_name', 'lora_name', 'ckpt_name', 'model_name', 'model'}


def check_graph(catalog: dict | None, graph: dict) -> tuple[str, list[str]]:
    if catalog is None:
        return 'unknown', ['Could not read the node and model catalogue.']
    missing, unknown = set(), set()
    for node in graph.values():
        kind = node['class_type']
        info = catalog.get(kind)
        if not isinstance(info, dict):
            missing.add(f'Node: {kind}')
            continue
        inputs = info.get('input', {})
        spec = {**inputs.get('required', {}), **inputs.get('optional', {})}
        for name, value in node.get('inputs', {}).items():
            if name not in _MODEL_FIELDS or not isinstance(value, str):
                continue
            definition = spec.get(name)
            choices = definition[0] if isinstance(definition, list) and definition else None
            # V3 nodes expose COMBO options in metadata, while older nodes use
            # a list as their first type entry. Both advertise concrete models.
            if (choices == 'COMBO' and len(definition) > 1 and isinstance(definition[1], dict)):
                choices = definition[1].get('options')
            if isinstance(choices, list):
                if value not in choices:
                    if catalog.get('_bang_owned_models', {}).get('status') == 'unknown' and _model_types(kind, name):
                        unknown.add(f'Imported model catalogue not verified: {value}')
                    else:
                        missing.add(f'Model: {value}')
            else:
                unknown.add(f'Model catalogue not exposed: {value}')
    details = sorted(missing) + sorted(unknown)
    return ('missing' if missing else 'unknown' if unknown else 'ready'), details


def test(body: dict) -> dict:
    cfg = draft(body)
    base, key = (CLOUD_URL, cfg.cloud_api_key) if cfg.mode == 'cloud' else (cfg.local_url, cfg.local_token)
    report = {'connected': False, 'mode': cfg.mode, 'message': '', 'checks': [],
              'checked_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'test_id': ''}
    try:
        if cfg.mode == 'cloud':
            report['cloud_account_status'] = _cloud_account(key)
        else:
            jobs = _get_json(base + '/api/v2/jobs?limit=1', key)
            if not isinstance(jobs.get('jobs'), list):
                raise ValueError('Unexpected jobs response.')
        report.update(connected=True, message='Connection verified. No generation was started.')
    except (httpx.HTTPError, ValueError) as exc:
        report['message'] = _failure(exc, cfg.mode)
        return report
    catalog = None
    try:
        catalog = node_catalog(cfg)
        if not catalog or not any(isinstance(v, dict) and 'input' in v for v in catalog.values()):
            catalog = None
    except (httpx.HTTPError, ValueError):
        pass
    nano_catalog = catalog if cfg.mode == 'cloud' else None
    nano_error = None
    if cfg.mode == 'local' and cfg.nano_available:
        try:
            report['cloud_account_status'] = _cloud_account(cfg.cloud_api_key)
        except (httpx.HTTPError, ValueError) as exc:
            nano_error = _failure(exc, 'cloud')
            report.update(connected=False, message='Local connection verified. Cloud partner connection failed: ' + nano_error)
        else:
            try:
                nano_catalog = node_catalog(replace(cfg, mode='cloud'))
            except (httpx.HTTPError, ValueError):
                nano_error = 'Could not read the Cloud model catalogue.'
    for identity, label, graph, required in requirements():
        if identity == 'nano' and not cfg.nano_available:
            status, details = 'off', ['Connect Cloud for Nano Banana, or choose Qwen / FLUX for local image edits.']
        else:
            status, details = check_graph(nano_catalog if identity == 'nano' else catalog, graph)
            if identity == 'nano' and nano_error:
                status, details = 'unknown', [nano_error]
        report['checks'].append({'id': identity, 'label': label, 'status': status, 'details': details, 'required': required})
    report['ready'] = report['connected'] and all(c['status'] == 'ready' for c in report['checks'] if c['required'])
    if report.get('cloud_account_status') == 'waitlisted':
        report['ready'] = False
        report['message'] = 'Cloud account authenticated, but execution access is waitlisted. No generation was started.'
    report['catalogue_note'] = 'Catalogue checks verify advertised nodes and model names. They do not run inference or verify GPU memory, credit balance, model downloads or output quality.'
    if catalog is None and report['connected']:
        report['message'] = 'Connection verified. Model checks are unavailable; check the ComfyUI address and catalogue access.'
    with _lock:
        now = time.monotonic()
        for token, entry in list(_tests.items()):
            if now - entry[0] > 900:
                del _tests[token]
        while len(_tests) >= 32:
            del _tests[next(iter(_tests))]
        token = uuid.uuid4().hex
        report['test_id'] = token
        _tests[token] = (now, _signature(cfg), report)
    return report
