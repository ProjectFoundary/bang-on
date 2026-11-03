"""Native EXR packages preserve model radiance, data alpha and signed lighting."""
import copy
import hashlib
import io
import json
from pathlib import Path
import zipfile

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keyfx import element_jobs, layer_export_api, layer_exports, media, native_colour, native_media, post_holdouts, projects
from test_element_pipeline import generate, pipeline
from test_batch_review_composite import review_shot

REAL_EXPORT=native_media.export_native


@pytest.fixture
def ready(pipeline,monkeypatch):
    create,exports,_=pipeline;p,root,originals=create(fps='60/1',transfer='smpte2084',primaries='bt2020')
    row,calls=generate(p,monkeypatch);element_jobs.approve('shot',row['id']);p=projects.load('shot')
    monkeypatch.setattr(layer_exports,'_light_signature',lambda p:None)
    monkeypatch.setattr(layer_exports,'_light_frame',lambda *a:None)
    return p,root,row,originals


def files(pid,job):
    state=layer_exports.status(pid,job['id']);assert state['status']=='done',state
    with zipfile.ZipFile(layer_exports.archive(pid,job['id'])) as z:return {name:z.read(name) for name in z.namelist()}


def exr(data,*,rgba=False):
    result=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_UNCHANGED)
    assert result.dtype==np.float32
    return result[...,[2,1,0,3]] if rgba else result[...,::-1]


def test_native_rgba_layers_match_the_compositor_data_and_exact_source_stays_intact(ready):
    p,root,row,originals=ready;before=(root/'project.json').read_bytes()
    assets={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in (root/'elements').rglob('*') if path.is_file()}
    job=layer_exports.start('shot',{'include':['elements']});archive=files('shot',job);manifest=json.loads(archive['manifest.json'])
    assert (manifest['width'],manifest['height'],manifest['frames'])==(18,32,8)
    assert manifest['source_colour']['primaries']=='bt2020' and manifest['working_colour']['primaries']=='bt709'
    assert manifest['timing']['timestamps']==p['native_profile']['timestamps']
    assert manifest['timing']['source_pts']==p['native_profile']['source_pts']
    assert manifest['alpha']['data'] and manifest['alpha']['association']=='premultiplied/emissive RGB'
    take=element_jobs._validated_take(root,row)
    optical={'blast':post_holdouts.NativePostHoldouts(p,p['effects'][0],p['native_profile'])}
    assert not exr(archive['elements/blast/rgba/00000.exr'],rgba=True).any()
    for f in range(p['frames']):
        saved=exr(archive[f'elements/blast/rgba/{f:05d}.exr'],rgba=True)
        layer=element_jobs.element_frame_layers(p,p['effects'][0],row,take,p['native_profile'],optical,f)
        if layer is None:assert not saved.any();continue
        assert np.array_equal(saved[...,:3],layer['rgb'].astype(np.float32))
        assert np.array_equal(saved[...,3],layer['alpha'].astype(np.float32))
    assert (root/'project.json').read_bytes()==before
    assert {str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in (root/'elements').rglob('*') if path.is_file()}==assets
    assert all(np.array_equal(native_media.read_native_frame(root/'native',f),originals[f]) for f in range(p['frames']))
    assert layer_exports.start('shot',{'include':['elements']})['id']==job['id']


def test_signed_lighting_export_retains_negative_float_values_and_no_alpha(ready,monkeypatch):
    p,root,_,_=ready
    delta=np.zeros((32,18,3),np.float32);delta[4,7]=[-.25,.03,1.75]
    monkeypatch.setattr(layer_exports,'_light_signature',lambda p:'signed-light-pass')
    monkeypatch.setattr(layer_exports,'_light_frame',lambda p,f,profile:delta*(f+1) if f>0 else None)
    archive=files('shot',layer_exports.start('shot',{'include':['lighting']}))
    assert np.array_equal(exr(archive['lighting/signed/00003.exr']),delta*4)
    assert not exr(archive['lighting/signed/00000.exr']).any()
    manifest=json.loads(archive['manifest.json']);assert manifest['lighting']['status']=='generated'
    assert manifest['lighting']['association']=='signed additive RGB; no alpha'
    assert not any(name.startswith('elements/') for name in archive)


def test_no_lighting_is_not_exported_as_a_fake_generated_zero_pass(ready):
    with pytest.raises(ValueError,match='lighting pass'):layer_exports.start('shot',{'include':['lighting']})
    archive=files('shot',layer_exports.start('shot',{'include':['lighting','elements']}))
    assert not any(name.startswith('lighting/signed/') for name in archive)
    assert json.loads(archive['manifest.json'])['lighting']['status']=='not_generated'


def test_rgba_export_keeps_black_smoke_opacity_and_zero_alpha_fire_radiance(ready,monkeypatch):
    def layer(*args):
        rgb=np.zeros((32,18,3),np.float64);a=np.zeros((32,18),np.float32)
        a[7,8]=.7;rgb[9,10]=[3.,.25,.01]  # Neither RGB-black nor alpha0 implies empty.
        return {'rgb':rgb,'alpha':a,'light':None,'shadow':None,'visibility':np.ones_like(a)}
    monkeypatch.setattr(element_jobs,'element_frame_layers',layer)
    archive=files('shot',layer_exports.start('shot',{'include':['elements']}))
    frame=exr(archive['elements/blast/rgba/00000.exr'],rgba=True)
    assert np.array_equal(frame[7,8],np.array([0,0,0,.7],np.float32))
    assert np.array_equal(frame[9,10],np.array([3,.25,.01,0],np.float32))


def test_receiver_auxiliaries_preserve_legacy_order_and_neutral_shadow(ready):
    p,root,_,_=ready;p['effects'][0]['element'].update(light=.4,shadow=.3);projects.save(p)
    archive=files('shot',layer_exports.start('shot',{'include':['lighting','elements']}))
    m=json.loads(archive['manifest.json']);assert 'multiply' in m['reconstruction'][2]
    shadow=exr(archive['lighting/analytic/blast/shadow/00000.exr'])
    assert np.array_equal(shadow,np.ones((32,18,3),np.float32))
    assert not exr(archive['lighting/analytic/blast/light/00000.exr']).any()
    assert np.any(exr(archive['lighting/analytic/blast/light/00003.exr'])>0)


def test_export_rejects_unapproved_default_but_explicit_review_stays_unapproved(review_shot,monkeypatch):
    p,root,blast,smoke,_,_=review_shot
    monkeypatch.setattr(layer_exports,'_light_signature',lambda p:None)
    monkeypatch.setattr(layer_exports,'_light_frame',lambda *a:None)
    with pytest.raises(ValueError,match='Approve each'):layer_exports.start('shot',{'include':['elements']})
    render=element_jobs.review_composite('shot',[blast['id'],smoke['id']]);p=projects.load('shot')
    # Layer-only review export does not depend on an unrelated viewing movie.
    before=copy.deepcopy(p['element_approvals'])
    archive=files('shot',layer_exports.start('shot',{'include':['elements'],'render_id':render['id']}))
    m=json.loads(archive['manifest.json']);assert m['review_only'] and not m['elements'][1]['approved']
    assert projects.load('shot')['element_approvals']==before
    p=projects.load('shot');p['effects'][0]['element']['gain']=1.4;projects.save(p)
    with pytest.raises(ValueError,match='outdated'):layer_exports.start('shot',{'include':['elements'],'render_id':render['id']})


def test_combined_export_builds_verified_native_movie_and_copies_current_master(ready,monkeypatch):
    p,root,row,originals=ready
    # The synthetic photograph fixture has PNGs but no uploaded audio container.
    def encode(frames,profile,out,source_audio=None):
        return REAL_EXPORT(frames,profile,out,source_audio=source_audio if source_audio and source_audio.is_file() else None)
    monkeypatch.setattr(native_media,'export_native',encode)
    archive=files('shot',layer_exports.start('shot',{'include':['combined']}))
    assert archive['combined/master.mov'] and archive['combined/preview.mp4']
    assert not any('/native_frames/' in name or '/display_frames/' in name for name in archive)
    extracted=root/'check.mov';extracted.write_bytes(archive['combined/master.mov'])
    probe=media.probe(extracted);assert probe['width']==18 and probe['height']==32 and probe['frames']==8
    render=element_jobs.composite('shot');p=projects.load('shot')
    assert p['renders'][0]['status']=='done',p['renders'][0].get('error')
    archive2=files('shot',layer_exports.start('shot',{'include':['combined'],'render_id':render['id']}))
    assert archive2['combined/master.mov']==(root/'renders'/render['id']/'master.mov').read_bytes()


def test_snapshot_changes_and_archive_tampering_fail_closed(ready,monkeypatch):
    p,root,_,_=ready;original=layer_exports._write_exr;changed=False
    def write(path,data):
        nonlocal changed
        original(path,data)
        if not changed:
            changed=True;q=projects.load('shot');q['effects'][0]['element']['gain']=1.3;projects.save(q)
    monkeypatch.setattr(layer_exports,'_write_exr',write)
    job=layer_exports.start('shot',{'include':['elements']});assert layer_exports.status('shot',job['id'])['status']=='error'
    assert not (root/'layer_exports'/job['id']/'bundle.zip').exists()
    monkeypatch.setattr(layer_exports,'_write_exr',original)
    job=layer_exports.start('shot',{'include':['elements']});path=layer_exports.archive('shot',job['id']);path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):layer_exports.archive('shot',job['id'])


@pytest.mark.parametrize('options',[{'include':[]},{'include':['unknown']},{'include':['elements','elements']},{'render_id':'../x'},{'directory':'/tmp'}])
def test_options_are_bounded_and_do_not_write_the_project(ready,options):
    _,root,_,_=ready;before=(root/'project.json').read_bytes()
    with pytest.raises(ValueError):layer_exports.start('shot',options)
    assert (root/'project.json').read_bytes()==before


def test_layer_export_http_status_download_and_corruption(ready):
    p,root,_,_=ready;app=FastAPI();app.include_router(layer_export_api.router);client=TestClient(app)
    response=client.post('/api/projects/shot/layer-exports',json={'include':['elements']})
    assert response.status_code==200,response.text
    job=response.json();assert job['status']=='done' and 'url' in job and str(root) not in response.text
    assert client.get(f'/api/projects/shot/layer-exports/{job["id"]}').json()['status']=='done'
    download=client.get(job['url']);assert download.status_code==200
    assert 'manifest.json' in zipfile.ZipFile(io.BytesIO(download.content)).namelist()
    layer_exports.archive('shot',job['id']).write_bytes(b'tampered')
    assert client.get(job['url']).status_code==404
    assert client.get('/api/projects/shot/layer-exports/../bad').status_code==404


def test_interrupted_export_is_reported_without_waiting_forever(ready):
    _,root,_,_=ready;job=layer_exports.start('shot',{'include':['elements']})
    path=root/'layer_exports'/job['id']/'state.json';state=json.loads(path.read_text());state.update(status='building',pid=-1)
    path.write_text(json.dumps(state))
    assert layer_exports.status('shot',job['id'])['status']=='error'
    assert layer_exports.start('shot',{'include':['elements']})['status']=='done'
