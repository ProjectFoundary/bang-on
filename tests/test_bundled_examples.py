"""Fresh installs receive editable samples once; user edits and deletions survive."""
import hashlib
import json
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient
import pytest

from keyfx import bundled_examples, projects
from keyfx.server import app


def test_fresh_start_installs_two_complete_offline_samples(tmp_path,monkeypatch):
    monkeypatch.setattr(projects,'DATA',tmp_path)
    monkeypatch.delenv('KEYFX_SKIP_EXAMPLES',raising=False)
    def no_provider(*a,**kw):pytest.fail('Opening a bundled example reached Comfy')
    monkeypatch.setattr('keyfx.connections._get_json',no_provider)
    with TestClient(app,base_url='http://127.0.0.1') as c:
        rows=c.get('/api/projects').json()
        assert {r['id'] for r in rows}=={'example-plane-smoke-v1','example-house-explosion-v1'}
        for row in rows:
            p=c.get('/api/projects/'+row['id']).json();root=projects.pdir(row['id'])
            assert p['frames']==96 and p['settings']['pipeline']=='ltx_elements'
            assert not p['renders'] and not p['element_takes'] and not p.get('element_approvals')
            assert len(list((root/'native/frames').glob('*.png')))==96
            assert len(list((root/'frames/plate').glob('*.png')))==96
            source=root/'native'/p['native_profile']['source_file']
            assert hashlib.sha256(source.read_bytes()).hexdigest()==p['native_profile']['source_sha256']
            assert c.get('/api/projects/'+row['id']+'/geometry/scene').status_code==200
            for ref in p['bundled_example']['references']:
                url='/api/projects/'+row['id']+'/example-media/'+ref['id']
                assert c.get(url,headers={'Range':'bytes=0-63'}).status_code==206
            assert c.get('/api/projects/'+row['id']+'/example-media/unknown').status_code==404
        plane=projects.load('example-plane-smoke-v1');plane['name']='My edited sample';projects.save(plane)
        assert bundled_examples.install_defaults()==[]
        assert projects.load(plane['id'])['name']=='My edited sample'
        # Deleting a sample does not make it reappear on subsequent starts.
        import shutil
        shutil.rmtree(projects.pdir('example-house-explosion-v1'))
        assert bundled_examples.install_defaults()==[]
        assert not projects.pdir('example-house-explosion-v1').exists()


def test_preexisting_project_is_never_overwritten(tmp_path,monkeypatch):
    monkeypatch.delenv('KEYFX_SKIP_EXAMPLES',raising=False)
    ids=[e['id'] for e in json.loads((bundled_examples.ASSETS/'catalog.json').read_text())['examples']]
    for pid in ids:
        d=tmp_path/'projects'/pid;d.mkdir(parents=True);(d/'project.json').write_text('{"user":"keep"}')
    bundled_examples.install_defaults(tmp_path)
    assert all((tmp_path/'projects'/pid/'project.json').read_text()=='{"user":"keep"}' for pid in ids)


def test_explicit_empty_install_opt_out(tmp_path,monkeypatch):
    monkeypatch.setenv('KEYFX_SKIP_EXAMPLES','1')
    assert bundled_examples.install_defaults(tmp_path)==[]
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('name',['../escape','/tmp/escape','folder\\escape'])
def test_bundle_paths_cannot_escape_installation(name):
    with pytest.raises(ValueError):bundled_examples._relative(name)
