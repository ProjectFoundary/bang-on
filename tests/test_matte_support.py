"""Local/free current-take matte upgrades with immutable model/approval pins."""
import copy
import json
from pathlib import Path
import struct

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keyfx import alpha_core,element_jobs,elements,finishing_api,matte_support,post_holdouts,projects
from test_element_pipeline import pipeline,png
from test_explosion_guide_core import prepare,complete


@pytest.fixture
def legacy(pipeline,monkeypatch):
    create,_,_=pipeline;p,root,originals=create(fps='24/1')
    row,frames=prepare(p,assisted=False);row,take=complete(p,row,[])
    element_jobs.approve('shot',row['id']);p=projects.load('shot')
    monkeypatch.setattr(elements,'run',lambda *a,**kw:pytest.fail('Matte refresh attempted inference'))
    return p,root,row,take,frames,originals


def raw_hashes(root):
    return {str(path.relative_to(root)):elements._sha(path)
            for folder in ('native','elements','element_inputs') for path in (root/folder).rglob('*') if path.is_file()}


def attach(legacy):
    p,_,row,_,frames,_=legacy;spec=matte_support.plan(p,'blast',row['id'])
    matte_support.prepare('shot','blast',row['id'],spec,frames)
    current=projects.load('shot');effect=current['effects'][0]
    return current,effect,effect['element']['alpha_support']


def test_legacy_approved_take_can_get_new_opacity_support_without_a_model_or_pin_change(legacy):
    p,root,row,take,frames,_=legacy;before=raw_hashes(root);pins=copy.deepcopy(p['element_approvals'])
    signature=element_jobs.generation_signature(p,p['effects'][0]);model_before=copy.deepcopy(row)
    current,effect,sid=attach(legacy)
    assert current['element_approvals']==pins and current['element_takes'][0]==model_before
    assert element_jobs.generation_signature(current,effect)==signature
    assert raw_hashes(root)==before
    record=element_jobs.records(current)[0];assert record['approved'] and record['current']
    folder=root/'matte_support'/sid;manifest=json.loads((folder/'manifest.json').read_text())
    assert manifest['policy']==alpha_core.policy() and len(manifest['frames'])==row['plan']['active_frames']
    for k,data in frames.items():
        assert np.array_equal(cv2.imread(str(folder/'frames'/f'{k:05d}.png'),0),cv2.imdecode(np.frombuffer(data,np.uint8),0))
    # Raw alpha is hollow; guide supplies its interior without erasing new wisps.
    k=1;x,y,w,h=take['geometry']['content_rect']
    model=cv2.imread(str(Path(take['assets']['alpha']['frames_dir'])/f'{k:05d}.png'),0)[y:y+h,x:x+w].astype(np.float32)/255
    out=matte_support.apply(current,effect,row,take,k,model)
    guide=cv2.imdecode(np.frombuffer(frames[k],np.uint8),0)
    assert np.array_equal(out,alpha_core.assisted_alpha(model,guide,frozen_policy=manifest['policy']))
    assert out[16,11]>model[16,11]


def test_legacy_support_is_retained_but_not_stacked_onto_default_raw_matte_treatment(legacy):
    p,root,row,take,_,originals=legacy;profile=p['native_profile'];f=3
    optical=post_holdouts.NativePostHoldouts(p,p['effects'][0],profile,source_depth=row['plan'].get('source_depth'))
    before=element_jobs.element_frame_layers(p,p['effects'][0],row,take,profile,{'blast':optical},f)
    current,effect,_=attach(legacy)
    after=element_jobs.element_frame_layers(current,effect,row,take,profile,{'blast':optical},f)
    assert np.array_equal(after['alpha'],before['alpha'])
    assert np.array_equal(after['rgb'],before['rgb'])
    assert not after['alpha'][:,8:10].any(),'The refreshed matte must not fill a foreground holdout'
    assert np.array_equal(element_jobs._composite_frame(current,[(effect,row,take)],profile,{'blast':optical},0),originals[0])


@pytest.mark.parametrize('bad',['signature','plan_signature','missing','colour','geometry','sixteen','empty'])
def test_bad_or_stale_capture_leaves_project_and_approvals_unchanged(legacy,bad):
    p,root,row,_,frames,_=legacy;spec=matte_support.plan(p,'blast',row['id']);frames=copy.deepcopy(frames)
    if bad in ('signature','plan_signature'):spec[bad]='old'
    elif bad=='missing':frames.pop(0)
    elif bad=='colour':frames[0]=png(np.full((spec['height'],spec['width'],3),(0,30,80),np.uint8))
    elif bad=='geometry':frames[0]=png(np.zeros((1,1),np.uint8))
    elif bad=='sixteen':frames[0]=png(np.zeros((spec['height'],spec['width']),np.uint16))
    else:frames={k:png(np.zeros((spec['height'],spec['width']),np.uint8)) for k in frames}
    before=(root/'project.json').read_bytes()
    with pytest.raises(ValueError):matte_support.prepare('shot','blast',row['id'],spec,frames)
    assert (root/'project.json').read_bytes()==before
    assert not (root/'matte_support').exists()


@pytest.mark.parametrize('bad',['frame','manifest','record','generation','index','model'])
def test_same_take_attachment_tamper_and_staleness_fail_closed(legacy,bad):
    _,root,row,take,_,_=legacy;current,effect,sid=attach(legacy);k=1
    if bad=='frame':(root/'matte_support'/sid/'frames'/'00001.png').write_bytes(b'changed')
    elif bad=='manifest':(root/'matte_support'/sid/'manifest.json').write_text('{}')
    elif bad=='record':current['matte_supports'][sid]['manifest_sha256']='changed'
    elif bad=='generation':effect['size']=2
    elif bad=='index':k=row['plan']['active_frames']
    else:take=copy.deepcopy(take);take['assets']['alpha']['sha256']='changed'
    model=np.zeros((row['plan']['height'],row['plan']['width']),np.float32)
    with pytest.raises(ValueError):matte_support.apply(current,effect,row,take,k,model)


def test_no_attachment_and_new_take_selection_fall_back_without_reusing_stale_support(legacy):
    p,_,row,take,_,_=legacy;model=np.zeros((row['plan']['height'],row['plan']['width']),np.float32)
    assert matte_support.apply(p,p['effects'][0],row,take,0,model) is None
    current,effect,sid=attach(legacy)
    newer={**row,'id':'new-model-variation'}
    assert matte_support.apply(current,effect,newer,take,0,model) is None
    effect['element']['alpha_support']='missing'
    with pytest.raises(ValueError):matte_support.apply(current,effect,row,take,0,model)


def test_current_model_assets_and_idle_state_are_verified_before_accepting_capture(legacy):
    p,root,row,_,frames,_=legacy;spec=matte_support.plan(p,'blast',row['id'])
    current=copy.deepcopy(p);current['renders'].append({'id':'busy','kind':'element','status':'queued'});projects.save(current)
    with pytest.raises(ValueError,match='Wait'):matte_support.prepare('shot','blast',row['id'],spec,frames)
    projects.save(p)
    (Path(row['assets']['alpha']['frames_dir'])/'00000.png').write_bytes(b'changed')
    before=(root/'project.json').read_bytes()
    with pytest.raises(ValueError):matte_support.prepare('shot','blast',row['id'],spec,frames)
    assert (root/'project.json').read_bytes()==before and not (root/'matte_support').exists()


def test_png_geometry_is_bounded_before_decoding_untrusted_alpha(legacy,monkeypatch):
    p,root,row,_,frames,_=legacy;spec=matte_support.plan(p,'blast',row['id']);frames=copy.deepcopy(frames)
    frames[0]=b'\x89PNG\r\n\x1a\n'+struct.pack('>I',13)+b'IHDR'+struct.pack('>II',100000,100000)+b'\x08\x00'
    monkeypatch.setattr(cv2,'imdecode',lambda *a,**kw:pytest.fail('Oversized declared PNG reached image allocation'))
    before=(root/'project.json').read_bytes()
    with pytest.raises(ValueError,match='frozen explosion canvas'):matte_support.prepare('shot','blast',row['id'],spec,frames)
    assert (root/'project.json').read_bytes()==before


def test_plan_and_multipart_api_upgrade_current_approved_legacy_without_inference(legacy):
    p,_,row,_,frames,_=legacy;app=FastAPI();app.include_router(finishing_api.router)
    client=TestClient(app,base_url='http://127.0.0.1');base='/api/projects/shot/elements/blast/matte-support'
    response=client.get(base+'/plan',params={'take_id':row['id']});assert response.status_code==200,response.text
    spec=response.json();assert spec['source_frames']==row['plan']['source_frames'] and spec['plan_signature']
    files=[(f'alpha_{k:04d}.png',('data.png',data,'image/png')) for k,data in frames.items()]
    metadata={'signature':spec['signature'],'plan_signature':spec['plan_signature']}
    bad=client.post(base+'/'+row['id'],data={'metadata':json.dumps(metadata)},files=files[:-1])
    assert bad.status_code==400
    result=client.post(base+'/'+row['id'],data={'metadata':json.dumps(metadata)},files=files)
    assert result.status_code==200,result.text
    public=result.json();assert public['effects'][0]['element']['alpha_support']
    assert public['element_approvals']==p['element_approvals']
    current=next(t for t in public['element_takes'] if t['id']==row['id'])
    assert current['approved'] and current['current']
