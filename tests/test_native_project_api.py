"""Native control edits must be atomic and keep the photographic route intact."""
import copy

from fastapi.testclient import TestClient
import pytest

from keyfx import projects
from keyfx.server import app
from test_element_pipeline import pipeline
from test_native_plate_media import rgb10_source, yuv10_source


@pytest.mark.parametrize('mode,expected', [('plate',False),('greenscreen',True)])
def test_action_analysis_is_only_scheduled_for_scene_imports(tmp_path,monkeypatch,mode,expected):
    # A local upload must not schedule a paid action model for plate-only FX,
    # including older API clients that explicitly request analyse_action=true.
    p={'id':'local-upload','mode':mode};scheduled=[]
    monkeypatch.setattr(projects,'create',lambda *args,**kw:p)
    monkeypatch.setattr(projects,'load',lambda pid:p)
    monkeypatch.setattr(projects,'public',lambda value:value)
    monkeypatch.setattr(projects,'start_render',lambda pid,kind:scheduled.append((pid,kind)))
    client=TestClient(app,base_url='http://127.0.0.1')
    response=client.post('/api/projects',data={'mode':mode,'analyse_action':'true','preserve_native':'false'},
                         files={'file':('local.mp4',b'local import fixture','video/mp4')})
    assert response.status_code==200,response.text
    assert scheduled==([('local-upload','analysis')] if expected else [])


def test_native_upload_form_preserves_source_and_requires_colour_assignment(tmp_path,monkeypatch):
    monkeypatch.setattr(projects,'DATA',tmp_path/'data')
    source=rgb10_source(tmp_path);original=source.read_bytes()
    client=TestClient(app,base_url='http://127.0.0.1')
    def upload(colour):
        return client.post('/api/projects',data={'name':'Native upload','mode':'plate','preserve_native':'true','colour_profile':colour},
                           files={'file':(source.name,original,'application/octet-stream')})
    assert upload('auto').status_code==400
    response=upload('rec709');assert response.status_code==200,response.text
    p=response.json();profile=p['native_profile']
    assert p['settings']['pipeline']=='ltx_elements'
    assert (profile['width'],profile['height'],profile['fps_rational'])==(259,35,'24000/1001')
    assert profile['bit_depth']==10 and profile['storage_bit_depth']==16
    assert not p['renders'],'Native import must not start the legacy action model'
    assert (projects.pdir(p['id'])/'native'/profile['source_file']).read_bytes()==original


def test_headroom_upload_defaults_to_legal_copy_with_opt_in_strict_preservation(tmp_path,monkeypatch):
    monkeypatch.setattr(projects,'DATA',tmp_path/'data')
    monkeypatch.setattr(projects,'start_render',lambda *a,**kw:pytest.fail('Import must not submit a model job'))
    source=yuv10_source(tmp_path,y_values=(0,512,1023));original=source.read_bytes()
    client=TestClient(app,base_url='http://127.0.0.1')
    def upload(policy=None):
        data={'mode':'plate','preserve_native':'true','analyse_action':'true'}
        if policy is not None:data['rgb_range']=policy
        return client.post('/api/projects',data=data,files={'file':(source.name,original,'application/octet-stream')})
    strict=upload('preserve');assert strict.status_code==400 and 'Legal-range RGB working copy' in strict.json()['detail']
    assert not list((tmp_path/'data/projects').glob('*/project.json'))
    assert upload('guess').status_code==422
    response=upload();assert response.status_code==200,response.text
    p=response.json();profile=p['native_profile']
    assert profile['rgb_range_policy']=='legalize' and profile['rgb_headroom_validation']['clipped_components']>0
    assert not p['renders'] and source.read_bytes()==original
    assert (projects.pdir(p['id'])/'native'/profile['source_file']).read_bytes()==original


@pytest.mark.parametrize('field,value', [('duration_s',0),('duration_s',6),('depth_offset',10),('seed',1.5),
                                        ('density',-1),('density',4.1),('brightness',True),('brightness',5),
                                        ('smoke_dispersion',-.1),('smoke_dispersion',2.1),('smoke_dispersion',True)])
def test_invalid_element_patch_never_changes_saved_project(pipeline,field,value):
    create,_,_=pipeline
    p,root,_=create()
    path=root/'project.json';before=path.read_bytes()
    effects=copy.deepcopy(p['effects']);effects[0]['element'][field]=value
    client=TestClient(app,base_url='http://127.0.0.1')
    response=client.patch('/api/projects/shot',json={'effects':effects})
    assert response.status_code==400,response.text
    assert path.read_bytes()==before
    assert client.get('/api/projects/shot').status_code==200


def test_native_plate_cannot_switch_into_whole_scene_generation(pipeline):
    create,_,_=pipeline;p,root,_=create()
    before=(root/'project.json').read_bytes()
    client=TestClient(app,base_url='http://127.0.0.1')
    assert client.patch('/api/projects/shot',json={'mode':'greenscreen'}).status_code==400
    assert (root/'project.json').read_bytes()==before
    with pytest.raises(ValueError,match='original photography'):
        projects.start_render('shot','environment')
