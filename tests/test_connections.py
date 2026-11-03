"""Connection setup and routing, using in-process HTTP transports; never submits real inference."""
import concurrent.futures
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from comfy_low import ComfyLow

from keyfx import comfy, connections, image_models, projects
from keyfx.server import app


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(connections, 'DATA', tmp_path)
    monkeypatch.setattr(projects, 'DATA', tmp_path)
    for name in ('COMFY_API_KEY', 'COMFY_BASE_URL', 'COMFY_LOCAL_TOKEN', 'COMFYUI_URL'):
        monkeypatch.delenv(name, raising=False)
    connections._tests.clear()
    monkeypatch.setattr(connections, '_cloud_owned_models', lambda key: [])


def catalog():
    result = {}
    for _, _, graph, _ in connections.requirements():
        for node in graph.values():
            spec = result.setdefault(node['class_type'], {'input': {'required': {}}})['input']['required']
            for name, value in node['inputs'].items():
                if name in connections._MODEL_FIELDS and isinstance(value, str):
                    options = spec.setdefault(name, [[], {}])[0]
                    if value not in options:
                        options.append(value)
    return result


def cloud_body(**kw):
    return {'mode': 'cloud', 'cloud_api_key': 'test-cloud-private-key', **kw}


def local_body(**kw):
    return {'mode': 'local', 'local_url': connections.LOCAL_URL, 'comfyui_url': connections.COMFYUI_URL, **kw}


@pytest.fixture
def read_server(monkeypatch):
    calls = []
    nodes = catalog()
    original = httpx.Client

    def handle(request):
        calls.append(request)
        assert request.method == 'GET', 'Connection test attempted a mutation'
        if request.url.path.endswith('/object_info'):
            return httpx.Response(200, json=nodes)
        if request.url.host == 'cloud.comfy.org':
            assert request.url.path == '/api/user', 'Cloud authentication must not depend on unsupported v2 job listing'
            return httpx.Response(200, json={'id': 'private-account-id', 'status': 'active'})
        assert request.url.path == '/api/v2/jobs'
        return httpx.Response(200, json={'jobs': []})

    monkeypatch.setattr(connections.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    return calls, nodes


def test_cloud_connection_is_read_only_and_secret_free(read_server):
    calls, _ = read_server
    report = connections.test(cloud_body())
    assert report['connected'] and report['ready']
    assert all(c['status'] == 'ready' for c in report['checks'])
    assert [str(r.url) for r in calls] == [connections.CLOUD_URL + '/api/user', connections.CLOUD_URL + '/api/object_info']
    assert all(r.headers['authorization'] == 'Bearer test-cloud-private-key' for r in calls)
    assert 'test-cloud-private-key' not in json.dumps(report)
    assert 'private-account-id' not in json.dumps(report)
    assert report['cloud_account_status'] == 'active'
    assert not connections._path().exists(), 'Testing persisted credentials'


def test_local_token_never_sent_to_catalogue_or_cloud(read_server, monkeypatch):
    monkeypatch.setenv('COMFY_API_KEY', 'environment-cloud-key')
    calls, _ = read_server
    report = connections.test(local_body(local_token='private-local-token'))
    assert report['connected']
    assert report['checks'][-1]['status'] == 'off'
    assert calls[0].headers['authorization'] == 'Bearer private-local-token'
    assert 'authorization' not in calls[1].headers
    assert all(r.url.host != 'cloud.comfy.org' for r in calls)


def test_mixed_connection_checks_cloud_with_its_own_key(read_server):
    calls, _ = read_server
    report = connections.test(local_body(local_token='local-only-token', allow_cloud_partner=True, cloud_api_key='cloud-only-key'))
    assert report['checks'][-1]['status'] == 'ready'
    assert calls[0].headers['authorization'] == 'Bearer local-only-token'
    assert all(r.headers['authorization'] == 'Bearer cloud-only-key' for r in calls if r.url.host == 'cloud.comfy.org')


def test_mixed_connection_refuses_invalid_cloud_auth(read_server, monkeypatch):
    original = connections._get_json
    def get(url, key=''):
        if url.startswith(connections.CLOUD_URL):
            request = httpx.Request('GET', url)
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError('secret-value', request=request, response=response)
        return original(url, key)
    monkeypatch.setattr(connections, '_get_json', get)
    body = local_body(cloud_api_key='secret-value', allow_cloud_partner=True)
    result = connections.test(body)
    assert not result['connected'] and 'Cloud partner connection failed' in result['message']
    assert 'secret-value' not in json.dumps(result)
    assert result['checks'][0]['status'] == 'ready'
    with pytest.raises(ValueError):
        connections.save({**body, 'test_id': result['test_id']})


def test_missing_legacy_models_are_named_without_blocking_primary_ltx_readiness(read_server):
    _, nodes = read_server
    del nodes['MinimaxH3LatentUpscaler3D']
    model = 'minimax_h3_ref2va_pruned_int8_convrot.safetensors'
    nodes['UNETLoader']['input']['required']['unet_name'][0].remove(model)
    report = connections.test(local_body())
    groups = {c['id']: c for c in report['checks']}
    assert groups['effects']['status'] == 'missing'
    assert 'Model: ' + model in groups['effects']['details']
    assert 'Node: MinimaxH3LatentUpscaler3D' in groups['scene']['details']
    assert groups['qwen']['status'] == 'ready'
    assert not groups['effects']['required'] and not groups['scene']['required']
    assert groups['rgb']['required'] and groups['alpha']['required']
    assert report['connected'] and report['ready']


def test_v3_combo_catalogues_verify_actual_model_options():
    graph = {'model': {'class_type': 'LoadDA3Model', 'inputs': {'model_name': 'depth.safetensors'}}}
    nodes = {'LoadDA3Model': {'input': {'required': {'model_name': ['COMBO', {'options': ['depth.safetensors'], 'multiselect': False}]}}}}
    assert connections.check_graph(nodes, graph) == ('ready', [])
    nodes['LoadDA3Model']['input']['required']['model_name'][1]['options'] = ['other.safetensors']
    assert connections.check_graph(nodes, graph) == ('missing', ['Model: depth.safetensors'])
    nodes['LoadDA3Model']['input']['required']['model_name'][1].pop('options')
    assert connections.check_graph(nodes, graph)[0] == 'unknown'


def test_cloud_catalogue_merges_only_verified_owned_models_into_matching_loaders(monkeypatch):
    nodes = {'LTXICLoRALoaderModelOnly': {'input': {'required': {'lora_name': ['COMBO', {'options': ['shared-lora']}]}}},
             'UNETLoader': {'input': {'required': {'unet_name': [['shared-unet']]}}}}
    monkeypatch.setattr(connections, '_get_json', lambda *a, **kw: nodes)
    monkeypatch.setattr(connections, '_cloud_owned_models', lambda key: [
        {'model_name': 'owned-fluid', 'type': 'lora', 'owned_by_user': True},
        {'model_name': 'owned-unet', 'type': 'diffusion_model', 'owned_by_user': True},
        {'model_name': 'not-owned', 'type': 'lora', 'owned_by_user': False}])
    merged = connections.node_catalog(connections.Connection(cloud_api_key='account-key'))
    assert merged['LTXICLoRALoaderModelOnly']['input']['required']['lora_name'][1]['options'] == ['shared-lora', 'owned-fluid']
    assert merged['UNETLoader']['input']['required']['unet_name'][0] == ['shared-unet', 'owned-unet']
    assert nodes['LTXICLoRALoaderModelOnly']['input']['required']['lora_name'][1]['options'] == ['shared-lora']
    graph = {'lora': {'class_type': 'LTXICLoRALoaderModelOnly', 'inputs': {'lora_name': 'owned-fluid'}}}
    assert connections.check_graph(merged, graph) == ('ready', [])


def test_unavailable_owned_catalogue_keeps_public_models_ready_and_private_unknown(monkeypatch):
    nodes = {'LTXICLoRALoaderModelOnly': {'input': {'required': {'lora_name': [['shared']]}}}}
    monkeypatch.setattr(connections, '_get_json', lambda *a, **kw: nodes)
    def unavailable(key): raise RuntimeError('Private server response must not be echoed')
    monkeypatch.setattr(connections, '_cloud_owned_models', unavailable)
    merged = connections.node_catalog(connections.Connection(cloud_api_key='private-key'))
    graph = {'lora': {'class_type': 'LTXICLoRALoaderModelOnly', 'inputs': {'lora_name': 'shared'}}}
    assert connections.check_graph(merged, graph) == ('ready', [])
    graph['lora']['inputs']['lora_name'] = 'owned'
    assert connections.check_graph(merged, graph) == ('unknown', ['Imported model catalogue not verified: owned'])


def test_unreadable_model_catalogue_is_not_reported_ready(read_server):
    calls, nodes = read_server
    nodes.clear()
    report = connections.test(local_body())
    assert report['connected'] and not report['ready']
    assert report['checks'][0]['status'] == 'unknown'
    assert 'unavailable' in report['message']


@pytest.mark.parametrize('status,message', [(401, 'Authentication'), (403, 'Authentication'), (404, 'HTTP 404'), (500, 'HTTP 500')])
def test_http_failures_do_not_echo_remote_content(monkeypatch, status, message):
    original = httpx.Client
    def handler(request):
        return httpx.Response(status, text='Remote response contains test-cloud-private-key')
    monkeypatch.setattr(connections.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    result = connections.test(cloud_body())
    assert not result['connected'] and message in result['message']
    assert 'test-cloud-private-key' not in json.dumps(result)


def test_redirect_is_not_followed_with_credentials(monkeypatch):
    calls = []
    original = httpx.Client
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={'location': 'https://unrelated.example/collect'})
    monkeypatch.setattr(connections.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    assert not connections.test(cloud_body())['connected']
    assert len(calls) == 1 and calls[0].url.host == 'cloud.comfy.org'


def test_cloud_auth_rejects_unrelated_success_response_without_job_listing(monkeypatch):
    calls = []
    def get(url, key=''):
        calls.append(url)
        return {'jobs': []}  # A proxy-shaped200 is not an authenticated Cloud account.
    monkeypatch.setattr(connections, '_get_json', get)
    result = connections.test(cloud_body())
    assert not result['connected'] and not result['test_id']
    assert calls == [connections.CLOUD_URL + '/api/user']


def test_waitlisted_cloud_account_is_authenticated_but_not_execution_ready(read_server, monkeypatch):
    original = connections._get_json
    def get(url, key=''):
        return {'status': 'waitlisted'} if url.endswith('/api/user') else original(url, key)
    monkeypatch.setattr(connections, '_get_json', get)
    result = connections.test(cloud_body())
    assert result['connected'] and result['ready'] is False
    assert result['cloud_account_status'] == 'waitlisted' and 'waitlisted' in result['message']


def test_local_proxy_404_keeps_local_setup_instructions(monkeypatch):
    def get(url, key=''):
        request = httpx.Request('GET', url)
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError('private-server-body', request=request, response=response)
    monkeypatch.setattr(connections, '_get_json', get)
    result = connections.test(local_body())
    assert not result['connected'] and 'comfy-api-proxy' in result['message']
    assert 'private-server-body' not in str(result)


@pytest.mark.parametrize('address', ['file:///tmp/comfy', 'http://u:password@localhost', 'http://localhost?key=secret',
    'http://localhost#key', 'http://localhost:bad', 'http://localhost:0', 'not a url'])
def test_invalid_addresses_rejected_without_echoing_them(address):
    with pytest.raises(ValueError) as error:
        connections.draft(local_body(local_url=address))
    assert address not in str(error.value)


def test_save_requires_matching_recent_success_and_private_file(read_server):
    body = cloud_body()
    with pytest.raises(ValueError):
        connections.save(body)
    report = connections.test(body)
    with pytest.raises(ValueError):
        connections.save({**body, 'cloud_api_key': 'different-key', 'test_id': report['test_id']})
    result = connections.save({**body, 'test_id': report['test_id']})
    assert result['configured'] and result['has_cloud_key'] and result['report']['connected']
    assert 'test-cloud-private-key' not in json.dumps(result)
    if os.name != 'nt':
        assert connections._path().stat().st_mode & 0o777 == 0o600
    assert json.loads(connections._path().read_text())['cloud_api_key'] == 'test-cloud-private-key'
    assert not (connections.DATA / 'projects').exists()


def test_existing_keys_preserved_but_local_token_does_not_follow_new_host(read_server):
    body = local_body(cloud_api_key='saved-cloud', local_token='saved-local', allow_cloud_partner=True)
    report = connections.test(body)
    connections.save({**body, 'test_id': report['test_id']})
    cfg = connections.draft({'mode': 'local', 'cloud_api_key': '', 'local_token': ''})
    assert cfg.cloud_api_key == 'saved-cloud' and cfg.local_token == 'saved-local'
    cfg = connections.draft({'local_url': 'http://other-machine:8189'})
    assert cfg.local_token == '' and cfg.cloud_api_key == 'saved-cloud'
    assert connections.draft({'clear_cloud_key': True, 'allow_cloud_partner': False}).cloud_api_key == ''


def test_disconnect_does_not_reactivate_environment_account(monkeypatch):
    monkeypatch.setenv('COMFY_API_KEY', 'legacy-environment-key')
    assert connections.current().configured
    result = connections.disconnect()
    assert not result['configured'] and not result['has_cloud_key']
    with pytest.raises(RuntimeError, match='Connect your Comfy'):
        comfy.client()


def test_canonical_cloud_environment_and_catalogue_url_validation(monkeypatch):
    monkeypatch.setenv('COMFY_API_KEY', 'cloud-key')
    monkeypatch.setenv('COMFY_BASE_URL', 'https://cloud.comfy.org:443/')
    assert connections.current().mode == 'cloud'
    monkeypatch.setenv('COMFYUI_URL', 'http://user:secret@localhost:8188')
    with pytest.raises(ValueError):
        connections.current()
    assert 'secret' not in json.dumps(connections.summary())


def test_local_defaults_use_local_image_models(monkeypatch):
    monkeypatch.setenv('COMFY_BASE_URL', connections.LOCAL_URL)
    assert image_models.validate({})['flash'] == 'qwen'
    assert image_models.validate({})['polish'] == 'qwen'
    assert image_models.validate({'flash': 'flux3'})['flash'] == 'flux3'


def test_client_routes_are_independent_and_do_not_mutate_environment(monkeypatch):
    monkeypatch.setenv('COMFY_API_KEY', 'never-send-cloud-to-local')
    monkeypatch.setenv('COMFY_BASE_URL', 'http://environment-target:8189')
    original = dict(os.environ)
    cfg = connections.Connection(mode='local', local_url='http://local-target:8189', local_token='local-token',
                                 cloud_api_key='cloud-key', allow_cloud_partner=True)
    def construct(cloud):
        c = comfy.client(config=cfg, cloud=cloud)
        try:
            assert c.workflows.from_json({'n': {'class_type': 'SaveImage', 'inputs': {}}}).json
            return c._low.base_url, c._low.authenticated
        finally:
            c.close()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(construct, [False, True] * 8))
    assert results[::2] == [('http://local-target:8189', True)] * 8
    assert results[1::2] == [(connections.CLOUD_URL, True)] * 8
    assert dict(os.environ) == original
    c = comfy.client(config=connections.Connection(mode='local'))
    assert not c._low.authenticated, 'Cloud environment key leaked into keyless local client'
    c.close()


def job_json():
    return {'id': 'cloud-job', 'status': 'succeeded', 'created_at': '2026-11-02T00:00:00Z', 'started_at': None,
            'completed_at': None, 'expires_at': '2026-11-03T00:00:00Z', 'queue_position': None, 'progress': None, 'outputs': [],
            'error': None, 'urls': {'self': connections.CLOUD_URL + '/api/v2/jobs/cloud-job',
            'events': connections.CLOUD_URL + '/api/v2/jobs/cloud-job/events',
            'cancel': connections.CLOUD_URL + '/api/v2/jobs/cloud-job/cancel'}}


def test_partner_job_is_collected_from_cloud_with_frozen_account(tmp_path, monkeypatch):
    cfg = connections.Connection(mode='local', cloud_api_key='frozen-cloud-key', local_token='local-token', allow_cloud_partner=True)
    monkeypatch.setattr(connections, 'current', lambda: cfg)
    sent, requests = [], []
    def mcp(key):
        assert key == 'frozen-cloud-key'
        def call(name, args):
            sent.append(name)
            if name == 'submit_workflow':
                return {'result': {'structuredContent': {'status': 'validated'}}}
            # Simulate a connection change after submission; collection must use the submitted account.
            monkeypatch.setattr(connections, 'current', lambda: connections.Connection(mode='local', cloud_api_key='changed-key'))
            return {'result': {'structuredContent': {'job_ids': ['cloud-job']}}}
        return call
    def handle(request):
        requests.append(request)
        assert request.url.host == 'cloud.comfy.org'
        assert request.headers['authorization'] == 'Bearer frozen-cloud-key'
        return httpx.Response(200, json=job_json())
    def transport(base, key, **kw):
        return ComfyLow(base, key, client=httpx.Client(transport=httpx.MockTransport(handle)), **kw)
    monkeypatch.setattr(comfy, 'ComfyLow', transport)
    monkeypatch.setattr(comfy, '_mcp', mcp)
    monkeypatch.setattr(comfy, '_upload', lambda *args: 'input/clean.png')
    assert comfy.run_partner_graph({'input': {'class_type': 'LoadImage', 'inputs': {}}}, 'input', tmp_path / 'input.png', 'out', tmp_path / 'out') == []
    assert sent == ['submit_workflow', 'submit_batch']
    assert requests and all(r.method == 'GET' for r in requests)


def test_local_only_nano_never_submits_to_cloud(monkeypatch, tmp_path):
    monkeypatch.setattr(connections, 'current', lambda: connections.Connection(mode='local', cloud_api_key='present-but-not-enabled'))
    monkeypatch.setattr(comfy, '_mcp', lambda *args: pytest.fail('Unexpected Cloud access'))
    with pytest.raises(RuntimeError, match='Cloud partner access'):
        comfy.run_partner_graph({}, 'input', Path('input.png'), 'out', tmp_path)


def test_connection_api_masks_secrets_and_protects_active_jobs(read_server):
    client = TestClient(app, base_url='http://127.0.0.1:8766')
    assert not client.get('/api/connection').json()['configured']
    body = cloud_body()
    report = client.post('/api/connection/test', json=body).json()
    body['test_id'] = report['test_id']
    saved = client.put('/api/connection', json=body)
    assert saved.status_code == 200
    assert 'test-cloud-private-key' not in saved.text
    assert 'test-cloud-private-key' not in client.get('/api/connection').text
    p = projects.pdir('active'); p.mkdir(parents=True)
    (p / 'project.json').write_text(json.dumps({'renders': [{'status': 'running', 'pid': os.getpid()}]}))
    assert client.delete('/api/connection').status_code == 409
    assert client.put('/api/connection', json=body).status_code == 409
    assert client.post('/api/connection/test', json=body).status_code == 200
    assert client.delete('/api/connection', headers={'Origin': 'https://untrusted.example'}).status_code == 403
    (p / 'project.json').write_text(json.dumps({'renders': []}))
    assert not client.delete('/api/connection').json()['configured']
