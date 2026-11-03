"""Comfy access through the official comfy-sdk: Comfy Cloud (COMFY_API_KEY) or a local ComfyUI (COMFY_BASE_URL)."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Callable

from comfy_sdk import Comfy
from comfy_low import ComfyLow
from comfy_sdk.assets import AssetFactory
from comfy_sdk.jobs import JobFactory
from comfy_sdk.models import Models
from comfy_sdk.workflows import WorkflowFactory
from comfy_sdk.retry import DEFAULT_RETRY

from . import connections

CLOUD_URL = 'https://cloud.comfy.org'


def _api_key() -> str | None:
    return connections.current().cloud_api_key or None


class _BoundComfy(Comfy):
    """Bind the SDK's factories to an explicit transport, without changing process environment.

    comfy-sdk 0.5's high-level constructor only accepts its target via environment.
    Its public low-level transport accepts a URL and token directly. All high-level
    methods still use the official SDK; only constructor wiring is supplied here.
    """
    def __init__(self, base: str, key: str | None):
        self._low = ComfyLow(base, key, timeout=120, client_info='bang-on')
        self.assets = AssetFactory(self._low)
        self.workflows = WorkflowFactory()
        self.jobs = JobFactory(self._low)
        self.models = Models(self._low, DEFAULT_RETRY)


def client(*, cloud: bool = False, config: connections.Connection | None = None) -> Comfy:
    cfg = config or connections.current()
    if not cfg.configured:
        raise RuntimeError('Connect your Comfy account or local installation in Connect Comfy first.')
    if cloud or cfg.mode == 'cloud':
        if not cfg.cloud_api_key:
            raise RuntimeError('Add your Comfy Cloud API key in Connect Comfy.')
        return _BoundComfy(CLOUD_URL, cfg.cloud_api_key)
    return _BoundComfy(cfg.local_url, cfg.local_token or None)


def _watch_progress(job, callback: Callable[[dict], None]) -> None:
    """Execution events prove the worker has started, even if its status event was missed."""
    from dataclasses import asdict
    from comfy_sdk.events import Progress, StatusChange
    started = False
    try:
        for event in job.events():
            if isinstance(event, Progress):
                value = asdict(event)
                if not started:
                    value.update(cloud_status='running', queue_position=None)
                    started = True
                callback(value)
            elif isinstance(event, StatusChange):
                if event.status.startswith('queued'):
                    started = False
                callback({'cloud_status': event.status, 'queue_position': event.queue_position})
    except Exception:
        pass  # optional telemetry; job.result() remains authoritative


def run_graph(graph: dict, uploads: dict[str, tuple[str, Path]], out_node: str, out_dir: Path,
              on_status: Callable[[str, str], None] | None = None, metadata: dict | None = None,
              on_progress: Callable[[dict], None] | None = None) -> list[Path]:
    """Upload local files into the given node inputs, submit, wait, and download every output of `out_node`.

    uploads: {node_id: (input_field, local_path)}; returns the downloaded files."""
    c = client()
    try:
        wf = c.workflows.from_json(graph)
        for node, (field, path) in uploads.items():
            wf.set_input(node, field, c.assets.from_file(Path(path)))
        # job metadata is only accepted for deployment jobs, so plain cloud/local submissions send none
        job = c.submit(wf, idempotency_key=str(uuid.uuid4()), metadata=metadata) if metadata else c.submit(wf, idempotency_key=str(uuid.uuid4()))
        if on_status:
            on_status(job.id, 'running')
        if on_progress:
            import threading
            threading.Thread(target=_watch_progress, args=(job, on_progress), daemon=True).start()
        job.result()
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for i, out in enumerate(job.get_outputs(out_node)):
            suffix = Path(out.name or '').suffix or ('.mp4' if 'video' in (out.content_type or '') else '.png')
            dest = out_dir / f'{out_node}_{i}{suffix}'
            out.to_file(dest)
            files.append(dest)
        if on_status:
            on_status(job.id, 'done')
        return files
    finally:
        c.close()


# ---------------------------------------------------------------- partner nodes (Nano Banana Pro / Gemini)
# Partner API nodes need the job submitted with the Comfy account's authorisation. The plain SDK submit fails them with
# "Unauthorized: Please login first to use this node", so (as in muzzle_hub) they go through Comfy Cloud's MCP endpoint:
# upload the image, submit_batch, then collect the known job through the SDK. Cloud only.

def _mcp(key: str | None = None):
    import json as _json
    import urllib.request
    key = key or _api_key()
    if not key:
        raise RuntimeError('Add your Comfy Cloud API key in Connect Comfy.')
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'Authorization': 'Bearer ' + key}

    def request(method, params):
        body = _json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
        req = urllib.request.Request(CLOUD_URL + '/mcp', data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            if r.headers.get('Mcp-Session-Id'):
                headers['Mcp-Session-Id'] = r.headers['Mcp-Session-Id']
            data = r.read().decode()
        return _json.loads(data if data.lstrip().startswith('{') else next(l[6:] for l in data.splitlines() if l.startswith('data: ')))

    request('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'keyfx', 'version': '0.1'}})
    return lambda name, args: request('tools/call', {'name': name, 'arguments': args})


def _payload(response: dict) -> dict:
    import json as _json
    r = response.get('result', {})
    if r.get('isError') or 'error' in response:
        raise RuntimeError(f'Comfy Cloud MCP call failed: {str(response)[:300]}')
    data = r.get('structuredContent')
    if data:
        return data if 'job_ids' in data else data.get('result', data)
    for item in r.get('content', []):
        if item.get('type') == 'text':
            try:
                return _json.loads(item['text'])
            except ValueError:
                pass
    return {}


def _upload(call, source: Path) -> str:
    import json as _json
    import shlex
    import urllib.request
    from urllib.parse import urlsplit
    resp = call('upload_file', {'file_path': str(source), 'client_os': 'darwin'})
    lines = '\n'.join(c.get('text', '') for c in resp.get('result', {}).get('content', [])).splitlines()
    cmd = next((l for l in lines if l.startswith('curl ')), None)
    if not cmd:
        raise RuntimeError('Comfy Cloud gave no upload command')
    argv = shlex.split(cmd)
    url = argv[-1]; parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname != 'cloud.comfy.org' or parts.username or parts.password:
        raise RuntimeError('unexpected upload destination')
    ctype = 'image/png'
    for i, a in enumerate(argv[:-1]):
        if a in ('-H', '--header'):
            name, sep, value = argv[i + 1].partition(':')
            if sep and name.lower() == 'content-type':
                ctype = value.strip()
    req = urllib.request.Request(url, data=source.read_bytes(), headers={'Content-Type': ctype}, method='PUT')
    with urllib.request.urlopen(req, timeout=60) as r:
        ref = _json.loads(r.read())
    if ref.get('type') != 'input' or not ref.get('name'):
        raise RuntimeError('invalid upload response')
    return '/'.join(s for s in (ref.get('subfolder'), ref['name']) if s)


def run_partner_graph(graph: dict, image_node: str, image: Path, out_node: str, out_dir: Path, description: str = 'Bang On',
                      additional_images: dict[str, Path] | None = None) -> list[Path]:
    """Submit a graph with a partner API node via Comfy Cloud MCP and download `out_node`'s outputs. One submission, never retried."""
    import copy as _copy
    cfg = connections.current()
    if not cfg.nano_available:
        raise RuntimeError('Nano Banana needs a Cloud connection. Enable Cloud partner access in Connect Comfy, or choose Qwen / FLUX.')
    call = _mcp(cfg.cloud_api_key)
    g = _copy.deepcopy(graph)
    g[image_node]['inputs']['image'] = _upload(call, Path(image))
    for node,path in (additional_images or {}).items():
        g[node]['inputs']['image'] = _upload(call, Path(path))
    pre = _payload(call('submit_workflow', {'workflow': g, 'dry_run': True}))
    if pre.get('status') != 'validated':
        raise RuntimeError(f'Comfy Cloud refused the workflow: {str(pre)[:300]}')
    res = _payload(call('submit_batch', {'items': [{'tool': 'submit_workflow', 'workflow': g, 'description': description}],
                                         'client_os': 'darwin', 'confirm': True}))
    if len(res.get('job_ids', [])) != 1 or res.get('failed'):
        raise RuntimeError(f'ambiguous Comfy Cloud submission (not retried): {str(res)[:300]}')
    # The MCP submission above is always a Cloud job, regardless of the native-workflow target.
    # Freeze the same account and target for lookup and downloads, even if settings change.
    c = client(cloud=True, config=cfg)
    try:
        job = c.jobs.get(res['job_ids'][0])
        job.result()
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for i, out in enumerate(job.get_outputs(out_node)):
            dest = out_dir / f'{out_node}_{i}.png'
            out.to_file(dest); files.append(dest)
        return files
    finally:
        c.close()
