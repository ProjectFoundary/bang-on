"""Combined generation and model routing; all image/video inference is local or stubbed."""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from keyfx import image_models, media, projects, setdressing
from keyfx.server import app


@pytest.fixture
def shot(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, 'DATA', tmp_path)
    monkeypatch.setattr(projects, '_spent', 0)
    monkeypatch.setenv('KEYFX_FLASH_CREDITS', '350')
    class Deferred:
        def __init__(self, **kwargs): pass
        def start(self): pass
    monkeypatch.setattr(projects.threading, 'Thread', Deferred)
    def unexpected(*a, **kw): pytest.fail('Unexpected paid inference')
    monkeypatch.setattr(projects.comfy, 'run_graph', unexpected)
    monkeypatch.setattr(projects.comfy, 'run_partner_graph', unexpected)
    p = {'id':'combined','name':'Combined','created':'2026-10-29','mode':'plate','frames':3,
         'source':{},'shot':'A wall impact','location':'','environment':None,'effects':[{'id':'fx','preset':'explosion','keys':[]}],
         'settings':{'seed':7,'polish':False},'flash_base':'plate','renders':[],
         'flashes':[{'id':'abc123','frame':1,'origin':{'x':.5,'y':.5},'tip':{'x':.6,'y':.5},'seed':1,'status':'new'}]}
    d=projects.pdir(p['id']);d.mkdir(parents=True)
    fd=d/'frames/plate';fd.mkdir(parents=True)
    for i in range(3):cv2.imwrite(str(fd/f'{i:05d}.png'),np.full((540,960,3),40,np.uint8))
    projects.save(p)
    return p


def fake_video(monkeypatch, events, fail=False):
    def run(pid,rid):
        events.append('video')
        if fail:
            projects._set(pid,rid,status='error',error='Video failed');return
        fd=projects.pdir(pid)/'renders'/rid/'frames';fd.mkdir()
        for i in range(3):cv2.imwrite(str(fd/f'{i:05d}.png'),np.full((540,960,3),140,np.uint8))
        projects._set(pid,rid,status='done')
    monkeypatch.setattr(projects,'_run',run)


def fake_flash(monkeypatch, events, fail=False):
    def generate(frame,flash,seed,out,**kw):
        events.append('flash')
        assert frame.mean()==140  # effects video, never the original plate
        if fail:raise RuntimeError('Flash failed')
        out.mkdir(parents=True);(out/'prompt.txt').write_text('flash')
        return np.full_like(frame,220)
    monkeypatch.setattr(projects.flashes,'generate',generate)


def test_video_then_muzzle_then_real_splice_and_reuse(shot,monkeypatch):
    events=[];fake_video(monkeypatch,events);fake_flash(monkeypatch,events)
    export=projects.export_final
    def finish(*a,**kw):events.append('splice');return export(*a,**kw)
    monkeypatch.setattr(projects,'export_final',finish)
    run=projects.start_pipeline(shot['id']);projects._run_pipeline(shot['id'],run['id'])
    p=projects.load(shot['id']);pipeline=next(r for r in p['renders'] if r['id']==run['id'])
    assert pipeline['status']=='done',pipeline.get('error')
    assert events==['video','flash','splice']
    final=next(r for r in p['renders'] if r['kind']=='final')
    assert final['base'].startswith('render:') and final['flashes']==[1]
    video=media.read_video(projects.pdir(p['id'])/'renders'/final['id']/'result.mp4')
    assert abs(video[0].mean()-140)<5 and abs(video[1].mean()-220)<5 and abs(video[2].mean()-140)<5
    run=projects.start_pipeline(shot['id']);projects._run_pipeline(shot['id'],run['id'])
    assert events==['video','flash','splice','splice']  # completed inference is not bought again


@pytest.mark.parametrize('failed', ['video','flash'])
def test_failure_stops_later_stages(shot,monkeypatch,failed):
    events=[];fake_video(monkeypatch,events,failed=='video');fake_flash(monkeypatch,events,failed=='flash')
    monkeypatch.setattr(projects,'export_final',lambda *a:pytest.fail('Failure reached export'))
    run=projects.start_pipeline(shot['id']);projects._run_pipeline(shot['id'],run['id'])
    p=projects.load(shot['id']);r=next(r for r in p['renders'] if r['id']==run['id'])
    assert r['status']=='error'
    assert events==(['video'] if failed=='video' else ['video','flash'])


def test_allowance_preflight_does_not_start_video(shot,monkeypatch):
    monkeypatch.setenv('KEYFX_FLASH_CREDITS','0')
    with pytest.raises(ValueError,match='No video generation'):projects.start_pipeline(shot['id'])
    assert projects.load(shot['id'])['renders']==[]


def test_muzzle_frames_cannot_bypass_pending_video(shot):
    with pytest.raises(ValueError,match='effects video'):projects.start_flashes(shot['id'])
    with pytest.raises(ValueError,match='effects video'):projects.export_final(shot['id'],True)


def test_generation_freezes_inputs_and_rejects_duplicate_jobs(shot):
    c=TestClient(app,base_url='http://127.0.0.1:8766')
    base='/api/projects/combined'
    assert c.post(base+'/render',json={'kind':'finish'}).status_code==200
    assert c.patch(base,json={'shot':'changed'}).status_code==409
    assert c.post(base+'/render',json={'kind':'finish'}).status_code==409
    assert c.post(base+'/flashes/generate',json={}).status_code==409
    assert c.get(base).status_code==200
    assert projects.load(shot['id'])['shot']=='A wall impact'


def test_model_choices_are_validated_and_invalidate_only_matching_work(shot):
    c=TestClient(app,base_url='http://127.0.0.1:8766');base='/api/projects/combined'
    assert c.get(base).json()['image_models']==image_models.DEFAULTS
    assert c.patch(base,json={'image_models':{'flash':'made-up'}}).status_code==400
    assert c.patch(base,json={'image_models':{'flash':{}}}).status_code==400
    assert c.patch(base,json={'image_models':{'flash':'qwen'}}).status_code==200
    p=projects.load(shot['id']);assert image_models.selected(p,'flash')=='qwen'
    r={'kind':'effect','base':'plate','effect_signature':projects.effect_signature(p)}
    assert projects.render_current(p,r)
    p['image_models']['flash']='nano';assert projects.render_current(p,r)
    p['image_models']['polish']='qwen';assert not projects.render_current(p,r)


@pytest.mark.parametrize('model',['qwen','flux3','nano'])
def test_edit_dispatch_uses_actual_graph_and_source(tmp_path,monkeypatch,model):
    source=tmp_path/'source.png';cv2.imwrite(str(source),np.full((736,1280,3),100,np.uint8))
    seen={}
    def native(g,uploads,node,out):
        seen.update(graph=g,uploads=uploads,node=node)
        return [source]
    def partner(g,input_node,inp,node,out,name):
        seen.update(graph=g,partner=inp,node=node)
        return [source]
    monkeypatch.setattr(image_models.comfy,'run_graph',native)
    monkeypatch.setattr(image_models.comfy,'run_partner_graph',partner)
    assert image_models.edit(source,'place object',7,tmp_path/'out',model)==source
    if model=='nano':assert seen['partner']==source and seen['graph']['35']['inputs']['prompt']=='place object'
    else:
        assert seen['uploads']['img']==('image',source)
        if model=='flux3':assert cv2.imread(str(seen['uploads']['mask'][1])).min()==255
        else:assert seen['graph']['pos']['inputs']['prompt']=='place object'


@pytest.mark.parametrize('model',['qwen','nano'])
def test_alternative_object_models_keep_exact_pixel_protection(tmp_path,monkeypatch,model):
    src=np.random.default_rng(3).integers(0,255,(720,1280,3),np.uint8)
    source=tmp_path/'source.png';cv2.imwrite(str(source),src)
    draft={'source':'source.png','objects':[{'id':'obj','action':'add','text':'wooden crate','box':{'x0':.05,'y0':.6,'x1':.3,'y1':.95}}],
           'protected':[{'id':'person','box':{'x0':.25,'y0':.2,'x1':.8,'y1':1}}],'seed':7,'margin':.04,'people':True}
    def edit(inp,text,seed,out,chosen,mask):
        assert chosen==model and 'wooden crate' in text
        assert np.array_equal(cv2.imread(str(inp))[:720],src)
        out.mkdir();path=out/'edited.png';cv2.imwrite(str(path),np.full((736,1280,3),200,np.uint8));return path
    monkeypatch.setattr(image_models,'edit',edit)
    final=cv2.imread(str(setdressing.build(source,draft,tmp_path/'edit',model=model)))
    _,permission,protected,_=setdressing.masks((1280,720),draft)
    assert np.array_equal(final[permission==0],src[permission==0])
    assert np.array_equal(final[protected>0],src[protected>0])


def test_duplicate_muzzle_frames_fail_before_inference(shot):
    shot['flashes'].append({**shot['flashes'][0], 'id':'def456'})
    projects.save(shot)
    with pytest.raises(ValueError,match='one muzzle flash per frame'):projects.start_pipeline(shot['id'])
    assert projects.load(shot['id'])['renders']==[]


def test_placing_effect_or_flash_preserves_computed_scene_and_model_state(shot):
    c=TestClient(app,base_url='http://127.0.0.1:8766');base='/api/projects/combined'
    r=c.post(base+'/effects',json={'preset':'wall_hit','frame':0,'x':.2,'y':.3})
    assert r.status_code==200 and r.json()['environment_ready'] is True
    assert r.json()['image_models']==image_models.DEFAULTS
    r=c.post(base+'/flashes',json={'frame':2,'origin':{'x':.5,'y':.5},'tip':{'x':.6,'y':.5}})
    assert r.status_code==200 and r.json()['environment_ready'] is True
    assert r.json()['image_models']==image_models.DEFAULTS


def test_unlimited_allowance_runs_pipeline_after_large_prior_reservations(shot,monkeypatch):
    monkeypatch.setenv('KEYFX_FLASH_CREDITS','unlimited')
    monkeypatch.setattr(projects,'_spent',1_000_000)
    cost=projects.flash_cost(3)
    assert cost['unlimited'] is True and cost['allowance_left'] is None
    assert json.loads(json.dumps(cost,allow_nan=False))['requested']==105
    events=[];fake_video(monkeypatch,events);fake_flash(monkeypatch,events)
    run=projects.start_pipeline(shot['id']);projects._run_pipeline(shot['id'],run['id'])
    r=next(r for r in projects.load(shot['id'])['renders'] if r['id']==run['id'])
    assert r['status']=='done',r.get('error')
    assert events==['video','flash']
    assert projects._spent==1_000_035


def test_unlimited_allowance_is_json_safe_in_cost_endpoint(shot,monkeypatch):
    monkeypatch.setenv('KEYFX_FLASH_CREDITS','unlimited')
    c=TestClient(app,base_url='http://127.0.0.1:8766')
    response=c.get('/api/projects/combined/flashes/cost?n=2')
    assert response.status_code==200
    assert response.json()=={'per_flash':35,'requested':70,'allowance_left':None,'unlimited':True}
