"""Project and render URLs must never expose installation credentials."""
import pytest
from fastapi.testclient import TestClient

from keyfx import projects, server


@pytest.mark.parametrize('value', ['..', '.', '../outside', '/tmp/outside', 'a/b', 'a\\b', '%2e%2e', 'a\x00b', 'a b', 'é', 'a'*129])
def test_project_paths_reject_unsafe_ids(tmp_path, monkeypatch, value):
    monkeypatch.setattr(projects, 'DATA', tmp_path)
    with pytest.raises(ValueError, match='ID'):
        projects.pdir(value)


def test_project_symlink_cannot_escape_store(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, 'DATA', tmp_path/'data')
    root=tmp_path/'data'/'projects';root.mkdir(parents=True)
    outside=tmp_path/'outside';outside.mkdir()
    (root/'alias').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='outside'):
        projects.pdir('alias')


@pytest.mark.parametrize('path', [
    '/api/projects/%2e%2e/renders/%2e%2e/connection.json',
    '/api/projects/%252e%252e/renders/test/connection.json',
    '/api/projects/safe/renders/%2e%2e/%2e%2e/%2e%2e/connection.json',
    '/api/projects/safe/renders/%252e%252e/connection.json',
])
@pytest.mark.parametrize('method', ['GET', 'HEAD', 'POST'])
def test_encoded_traversal_is_rejected_even_for_allowed_network_host(tmp_path, monkeypatch, path, method):
    monkeypatch.setattr(projects, 'DATA', tmp_path)
    monkeypatch.setattr(server, '_LOCAL', server._LOCAL | {'qa-host'})
    secret='private-connection-sentinel'
    (tmp_path/'connection.json').write_text(secret)
    (tmp_path/'projects'/'safe'/'renders').mkdir(parents=True)
    client=TestClient(server.app,base_url='http://qa-host')
    response=client.request(method,path)
    assert response.status_code in (400,404)
    assert secret not in response.text


def test_safe_render_download_still_works_and_blocks_file_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, 'DATA', tmp_path)
    root=projects.pdir('test-project_1');rd=root/'renders'/'take_1';rd.mkdir(parents=True)
    (rd/'result.mp4').write_bytes(b'public-media')
    (root/'private.txt').write_text('private-sentinel')
    client=TestClient(server.app,base_url='http://127.0.0.1')
    assert client.get('/api/projects/test-project_1/renders/take_1/result.mp4').content==b'public-media'
    response=client.get('/api/projects/test-project_1/renders/take_1/%2e%2e/%2e%2e/private.txt')
    assert response.status_code==404
    assert 'private-sentinel' not in response.text
