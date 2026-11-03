"""Offline finishing integration: real reference movies/EXR/native math, fake model."""
import copy
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keyfx import (connections, element_jobs, elements, finish_approval,
                   finishing_api, lighting_integration as lighting,
                   native_colour, native_media, post_holdouts, projects)
from test_element_pipeline import pipeline, catalogue
from test_element_final_current import _two_ready, _model_hashes


@pytest.fixture
def ready(pipeline,monkeypatch):
    create,exports,_=pipeline
    p,root,originals=create(fps='24/1')
    blast,smoke,_,_=_two_ready(p,monkeypatch)
    return projects.load('shot'),root,originals,exports,blast,smoke


def assets(root):
    return {str(path.relative_to(root)):elements._sha(path)
            for folder in ('native','elements','element_inputs')
            for path in (root/folder).rglob('*') if path.is_file()}


def fake_light(p,root,monkeypatch,*,fail=False):
    calls=[]
    spec=lighting.plan(p)
    graph,_,_=lighting.graph(spec,root/'clean.mp4',root/'unlit.mp4',42,'offline/lighting')
    nodes=catalogue([graph])
    def executor(*,graph,uploads,out_node,out_dir,on_status,idempotency_key):
        calls.append({'graph':copy.deepcopy(graph),'uploads':uploads,'idempotency':idempotency_key})
        assert set(uploads)=={'proxy_video','effect_video'}
        assert graph['cond']['inputs']['ref_videos.ref_video_0']==['proxy_frames',0]
        assert graph['cond']['inputs']['ref_videos.ref_video_1']==['effect_frames',0]
        clean,rate=elements._read_video(uploads['proxy_video'][1]);unlit,_=elements._read_video(uploads['effect_video'][1])
        assert rate==24 and len(clean)==len(unlit)==spec['model_frames']
        assert all(im.shape==(spec['canvas_height'],spec['canvas_width'],3) for im in clean+unlit)
        # _read_video and _encode use BGR; a double conversion would swap the
        # actual photographed plate and make the lighting colour incorrect.
        directory=uploads['effect_video'][1].parent
        assert np.max(np.abs(clean[0].astype(int)-cv2.imread(str(directory/'clean'/'00000.png')).astype(int)))<=3
        assert np.max(np.abs(unlit[0].astype(int)-cv2.imread(str(directory/'unlit'/'00000.png')).astype(int)))<=3
        for sequence in (clean,unlit):
            assert all(np.array_equal(im,sequence[spec['active_frames']-1]) for im in sequence[spec['active_frames']:])
        assert not (uploads['proxy_video'][1].parent/'../native').is_file()
        assert len(idempotency_key)>20
        if fail:raise RuntimeError('Injected uncertain model submission')
        # Add receiver changes to the actual submitted (decoded) RGB reference,
        # so this fixture models lighting rather than correcting input encoding.
        frames=[]
        for i in range(spec['model_frames']):
            im=unlit[i][...,::-1].astype(np.float32)/255
            linear=native_colour.srgb_to_linear(im)
            delta=np.zeros_like(linear);delta[...]=[.08,.02,-.06]
            rgb=native_colour.encode(np.clip(linear+delta,0,1),{'colour':{'transfer':'srgb','primaries':'bt709'}})
            frames.append(np.uint8(np.round(rgb*255))[...,::-1])
        out_dir.mkdir(parents=True,exist_ok=True)
        path=out_dir/'model.mp4';elements._encode(path,frames)
        on_status('fake-minimax-light','done')
        return [path]
    return spec,nodes,executor,calls


def run_light(ready,monkeypatch):
    p,root,_,_,_,_=ready
    spec,nodes,executor,calls=fake_light(p,root,monkeypatch)
    row=lighting.start('shot',executor=executor,catalog=nodes)
    current=projects.load('shot')
    result=next(r for r in current['lighting_takes'] if r['id']==row['id'])
    assert result['status']=='done',result
    return current,result,calls,spec,nodes,executor


def test_two_references_padding_signed_exr_and_original_assets_stay_immutable(ready,monkeypatch):
    p,root,originals,_,_,_=ready;before=assets(root);pins=copy.deepcopy(p['element_approvals'])
    current,row,calls,spec,nodes,executor=run_light(ready,monkeypatch)
    assert len(calls)==1 and current['element_approvals']==pins and assets(root)==before
    assert current['light_integration']=={'enabled':True,'take_id':row['id'],'gain':1}
    folder=root/'lighting'/row['id'];assert (folder/'claim').exists()
    request=json.loads((folder/'request.json').read_text())
    assert set(request['uploads'])=={'proxy_video','effect_video'}
    assert all(elements._sha(Path(item['path']))==item['sha256'] for item in request['uploads'].values())
    manifest=json.loads((folder/'manifest.json').read_text())
    assert manifest['signed'] and len(manifest['frames'])==spec['active_frames']
    layer=lighting.signed_layer(current,spec['source_frames'][0],current['native_profile'])
    assert layer.shape==originals[0].shape and layer.dtype in (np.float32,np.float64)
    assert layer[...,0].max()>0 and layer[...,2].min()<0,'Negative receiver shadows must not become absolute values'
    assert lighting.signed_layer(current,0,current['native_profile']) is None
    # Reuse means no second job/claim/new model call.
    reused=lighting.start('shot',executor=executor,catalog=nodes)
    assert reused['id']==row['id'] and len(calls)==1


@pytest.mark.parametrize('fps',['60000/1001','60/1','24000/1001'])
def test_plan_maps_native_pts_and_only_holds_the_model_tail(pipeline,monkeypatch,fps):
    create,_,_=pipeline;p,root,_=create(fps=fps);_two_ready(p,monkeypatch);p=projects.load('shot')
    spec=lighting.plan(p);times=np.asarray(p['native_profile']['timestamps'])
    assert spec['source_frames']==[int(np.argmin(np.abs(times-(spec['start_time']+i/24)))) for i in range(spec['active_frames'])]
    assert spec['end_time']<=p['native_profile']['duration']
    assert spec['model_frames']>=spec['active_frames'] and (spec['model_frames']-5)%17==0
    assert spec['canvas_width']%16==spec['canvas_height']%16==0
    assert spec['canvas_width']>=spec['width'] and spec['canvas_height']>=spec['height']


def test_signed_difference_cleanup_preserves_receivers_and_clears_changed_fire():
    h,w=128,192;unlit=np.full((h,w,3),.2,np.float32)
    desired=np.zeros_like(unlit);desired[:]=[.03,.01,-.02]
    model=unlit+desired;alpha=np.zeros((h,w),np.float32);alpha[45:85,75:115]=1
    rgb=np.zeros_like(unlit);rgb[alpha>0]=.25
    model[alpha>0]+=[.8,.3,0]
    protected=np.zeros((h,w),bool);protected[60,90]=True
    cleaned=lighting.cleanup_difference(model,unlit,alpha,rgb)
    assert np.array_equal(cleaned[0,0],model[0,0]-unlit[0,0])
    assert np.max(np.abs(cleaned[55,80]-desired[55,80]))<1e-5
    assert cleaned[...,2].min()<0
    protected_result=lighting.cleanup_difference(model,unlit,alpha,rgb,protected)
    assert np.array_equal(protected_result[60,90],model[60,90]-unlit[60,90]),'Opaque foreground receiver is not fire-cleaned'
    zero=lighting.cleanup_difference(model,model,np.zeros_like(alpha),np.zeros_like(rgb))
    assert not zero.any()


def test_registration_requires_enough_tracked_points_and_recovers_small_translation(monkeypatch):
    rng=np.random.default_rng(17);unlit=rng.integers(20,220,(128,192,3),np.uint8)
    transform=np.array([[1,0,3],[0,1,-2]],np.float32)
    model=cv2.warpAffine(unlit,transform,(192,128),borderMode=cv2.BORDER_REFLECT)
    support=np.zeros((128,192),bool);support[40:80,60:100]=True
    aligned,info=lighting._align(model,unlit,support)
    assert info['registered'] and np.max(np.abs(np.asarray(info['matrix'])[:2,2]-[-3,2]))<.2
    assert np.mean(np.abs(aligned[10:-10,10:-10].astype(float)-unlit[10:-10,10:-10]))<8
    points=np.arange(40,dtype=np.float32).reshape(20,1,2)
    monkeypatch.setattr(cv2,'goodFeaturesToTrack',lambda *a,**kw:points)
    monkeypatch.setattr(cv2,'calcOpticalFlowPyrLK',lambda *a,**kw:(points.copy(),np.array([1]*3+[0]*17,np.uint8).reshape(-1,1),None))
    monkeypatch.setattr(cv2,'estimateAffinePartial2D',lambda *a,**kw:pytest.fail('Too few tracked points reached affine estimation'))
    unchanged,info=lighting._align(model,unlit,support)
    assert not info['registered'] and np.array_equal(unchanged,model)


def test_native_composite_adds_signed_light_before_the_unchanged_elements(ready,monkeypatch):
    current,row,_,_,_,_=run_light(ready,monkeypatch)
    _,root,originals,_,_,_=ready;profile=current['native_profile']
    selected=[(e,r,element_jobs._validated_take(root,r)) for e,r in lighting._rows(current)]
    optical={e['id']:post_holdouts.NativePostHoldouts(current,e,profile,source_depth=r['plan'].get('source_depth')) for e,r,_ in selected}
    f=3;delta=lighting.signed_layer(current,f,profile)
    expected=native_colour.composite_native(originals[f],profile,light_rgb=delta)
    unlit=originals[f].copy()
    for e,r,take in selected:
        layer=element_jobs.element_frame_layers(current,e,r,take,profile,optical,f)
        if layer is None:continue
        rgb=native_colour.encode(layer['rgb'],{'colour':{'transfer':'srgb','primaries':'bt709'}})
        expected=native_colour.composite_native(expected,profile,rgb,layer['alpha'],premultiplied=True,light_rgb=layer['light'],shadow=layer['shadow'])
        unlit=native_colour.composite_native(unlit,profile,rgb,layer['alpha'],premultiplied=True,light_rgb=layer['light'],shadow=layer['shadow'])
    actual=element_jobs._composite_frame(current,selected,profile,optical,f)
    assert np.array_equal(actual,expected)
    assert not np.array_equal(actual,native_colour.composite_native(unlit,profile,light_rgb=delta)),'Light must not be added over fixed fire radiance'
    assert np.array_equal(element_jobs._composite_frame(current,selected,profile,optical,0),originals[0])


@pytest.mark.parametrize('change',['opacity','gain','pin','manifest','frame'])
def test_stale_or_tampered_lighting_fails_closed(ready,monkeypatch,change):
    current,row,_,_,_,_=run_light(ready,monkeypatch);_,root,_,_,_,_=ready
    if change in ('opacity','gain'):current['effects'][0]['element'][change]=.4
    elif change=='pin':current['element_approvals'].pop(current['effects'][0]['id'])
    elif change=='manifest':(root/'lighting'/row['id']/'manifest.json').write_text('{}')
    else:(root/'lighting'/row['id']/'delta'/'00000.exr').write_bytes(b'changed')
    with pytest.raises(ValueError):lighting.signed_layer(current,row['source_frames'][0],current['native_profile'])


@pytest.mark.parametrize('target',['manifest','delta'])
def test_cached_lighting_cannot_be_reenabled_after_its_files_change(ready,monkeypatch,target):
    current,row,calls,_,nodes,executor=run_light(ready,monkeypatch);_,root,_,_,_,_=ready
    lighting.configure('shot',{'enabled':False})
    folder=root/'lighting'/row['id']
    path=folder/'manifest.json' if target=='manifest' else folder/'delta'/'00000.exr'
    path.write_bytes(b'changed')
    before=(root/'project.json').read_bytes()
    with pytest.raises(ValueError):lighting.start('shot',executor=executor,catalog=nodes)
    assert (root/'project.json').read_bytes()==before and len(calls)==1


def test_uncertain_lighting_submission_is_not_automatically_retried(ready,monkeypatch):
    p,root,_,_,_,_=ready;_,nodes,executor,calls=fake_light(p,root,monkeypatch,fail=True)
    row=lighting.start('shot',executor=executor,catalog=nodes)
    current=projects.load('shot');record=next(r for r in current['lighting_takes'] if r['id']==row['id'])
    assert record['status']=='error' and len(calls)==1 and (root/'lighting'/row['id']/'claim').exists()
    before=(root/'project.json').read_bytes()
    with pytest.raises(ValueError,match='failed or is uncertain'):lighting.start('shot',executor=executor,catalog=nodes)
    assert len(calls)==1 and (root/'project.json').read_bytes()==before
    explicit=lighting.start('shot',new_version=True,executor=executor,catalog=nodes)
    assert explicit['id']!=row['id'] and len(calls)==2
    assert calls[0]['idempotency']!=calls[1]['idempotency']


def test_duplicate_click_reuses_pending_attempt_instead_of_queuing_another_model(ready,monkeypatch):
    p,root,_,_,_,_=ready;_,nodes,executor,calls=fake_light(p,root,monkeypatch);pending=[]
    class PendingThread:
        def __init__(self,target,**kwargs):self.target=target
        def start(self):pending.append(self.target)
    monkeypatch.setattr(lighting.threading,'Thread',PendingThread)
    first=lighting.start('shot',executor=executor,catalog=nodes)
    second=lighting.start('shot',executor=executor,catalog=nodes)
    assert first['id']==second['id'] and len(pending)==1 and calls==[]
    assert len(projects.load('shot')['lighting_takes'])==1
    assert not (root/'lighting'/first['id']/'claim').exists()
    pending[0]()
    assert len(calls)==1 and projects.load('shot')['lighting_takes'][0]['status']=='done'


def test_scoped_element_submission_allowance_does_not_authorize_a_lighting_spend(ready,monkeypatch):
    p,root,_,_,_,_=ready;_,nodes,executor,calls=fake_light(p,root,monkeypatch)
    monkeypatch.setattr(lighting.development_gate,'_config',lambda:{'test':'element-only allowance'})
    before=(root/'project.json').read_bytes()
    with pytest.raises(ValueError,match='does not authorize'):lighting.start('shot',executor=executor,catalog=nodes)
    assert calls==[] and (root/'project.json').read_bytes()==before


def test_failed_model_preflight_never_claims_or_submits(ready,monkeypatch):
    p,root,_,_,_,_=ready;_,_,executor,calls=fake_light(p,root,monkeypatch)
    row=lighting.start('shot',executor=executor,catalog={})
    current=projects.load('shot');record=next(r for r in current['lighting_takes'] if r['id']==row['id'])
    assert record['status']=='error' and calls==[]
    assert not (root/'lighting'/row['id']/'claim').exists()


def test_identity_model_output_has_zero_lighting_not_video_encode_residue(ready,monkeypatch):
    p,root,_,_,_,_=ready;_,nodes,_,_=fake_light(p,root,monkeypatch)
    def identity(*,uploads,**kwargs):return [uploads['effect_video'][1]]
    row=lighting.start('shot',executor=identity,catalog=nodes)
    current=projects.load('shot');result=next(r for r in current['lighting_takes'] if r['id']==row['id'])
    assert result['status']=='done',result
    layer=lighting.signed_layer(current,row['source_frames'][0],current['native_profile'])
    assert not np.any(layer), 'Identical submitted/returned video must not become receiver lighting'


@pytest.mark.parametrize('change',['settings','backend','asset'])
def test_inputs_changed_during_preparation_stop_before_the_paid_claim(ready,monkeypatch,change):
    p,root,_,_,blast,_=ready;_,nodes,executor,calls=fake_light(p,root,monkeypatch)
    original=lighting._prepare
    def prepare(*args):
        result=original(*args)
        if change=='settings':
            current=projects.load('shot');current['effects'][0]['element']['opacity']=.4;projects.save(current)
        elif change=='backend':
            other=connections.Connection(mode='local',local_url='http://127.0.0.1:8199',comfyui_url='http://127.0.0.1:8188')
            monkeypatch.setattr(connections,'current',lambda:other)
        else:(Path(blast['assets']['alpha']['frames_dir'])/'00000.png').write_bytes(b'changed')
        return result
    monkeypatch.setattr(lighting,'_prepare',prepare)
    row=lighting.start('shot',executor=executor,catalog=nodes)
    current=projects.load('shot');record=next(r for r in current['lighting_takes'] if r['id']==row['id'])
    assert record['status']=='error' and calls==[]
    assert not (root/'lighting'/row['id']/'claim').exists()


def review_with_unapproved_smoke(ready):
    p,_,_,_,blast,smoke=ready;p['element_approvals'].pop('smoke');projects.save(p)
    render=element_jobs.review_composite('shot',[blast['id'],smoke['id']])
    return projects.load('shot'),render


def test_approve_preview_pins_exact_selection_after_every_asset_is_valid(ready):
    p,render=review_with_unapproved_smoke(ready);_,root,_,_,blast,smoke=ready
    before=assets(root)
    fresh=finish_approval.approve_preview('shot',render['id'])
    assert fresh['element_approvals']['blast']==blast['id'] and fresh['element_approvals']['smoke']==smoke['id']
    assert assets(root)==before


@pytest.mark.parametrize('change',['controls','asset','request-selection'])
def test_approve_preview_is_atomic_and_rejects_stale_or_changed_selection(ready,change):
    p,render=review_with_unapproved_smoke(ready);_,root,_,_,blast,smoke=ready
    if change=='controls':p['effects'][0]['element']['gain']=2;projects.save(p)
    elif change=='asset':(Path(smoke['assets']['alpha']['frames_dir'])/'00000.png').write_bytes(b'changed')
    else:
        clone={**copy.deepcopy(smoke),'id':'other-current-smoke'};p['element_takes'].append(clone);projects.save(p)
        path=root/'renders'/render['id']/'request.json';request=json.loads(path.read_text());request['elements']=[blast['id'],clone['id']];path.write_text(json.dumps(request))
    before=(root/'project.json').read_bytes()
    with pytest.raises((ValueError,OSError)):finish_approval.approve_preview('shot',render['id'])
    assert (root/'project.json').read_bytes()==before


def test_finishing_api_rejects_malformed_approval_and_lighting_controls_without_spending(ready,monkeypatch):
    app=FastAPI();app.include_router(finishing_api.router);client=TestClient(app,base_url='http://127.0.0.1',raise_server_exceptions=False)
    monkeypatch.setattr(lighting,'start',lambda *a,**kw:pytest.fail('Malformed input reached a model submission'))
    for bad in ({},{'render_id':None},{'render_id':[]},{'render_id':17},{'render_id':'unknown','approve':True}):
        assert client.post('/api/projects/shot/finishing/approve-preview',json=bad).status_code==400
    for bad in ({'new_version':'yes'},{'new_version':True,'extra':1}):
        assert client.post('/api/projects/shot/lighting/run',json=bad).status_code==400
    for bad in ({'gain':float('inf')},{'enabled':'yes'},{'unknown':1}):
        if bad.get('gain')==float('inf'):continue  # JSON transport correctly forbids nonfinite values itself.
        assert client.patch('/api/projects/shot/lighting',json=bad).status_code==400
