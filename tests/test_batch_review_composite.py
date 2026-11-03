"""Batch review uses explicit cached takes without approving or exporting them."""
import copy
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from keyfx import element_api, element_jobs, elements, projects
from test_element_pipeline import pipeline, prepare
from test_element_final_current import _two_ready


@pytest.fixture
def review_shot(pipeline,monkeypatch):
    create,exports,_=pipeline
    p,root,originals=create(fps='24/1')
    blast,smoke,_,_=_two_ready(p,monkeypatch)
    p=projects.load('shot');p['element_approvals'].pop('smoke');projects.save(p)
    monkeypatch.setattr(elements,'run',lambda *a,**kw:pytest.fail('A review composite submitted inference'))
    return p,root,blast,smoke,exports,originals


def test_review_reuses_pin_and_preserves_unapproved_candidates_and_native_plate(review_shot):
    p,root,blast,smoke,exports,originals=review_shot
    pins=copy.deepcopy(p['element_approvals'])
    # Omitted approved IDs are filled by the backend, never replaced by latest.
    job=element_jobs.review_composite('shot',[smoke['id']])
    p=projects.load('shot');render=p['renders'][0]
    assert render['id']==job['id'] and render['status']=='done' and render['review_only']
    assert render['review_take_ids']==[blast['id'],smoke['id']]
    assert p['element_approvals']==pins
    records={r['effect_id']:r for r in element_jobs.records(p)}
    assert records['blast']['approved'] and not records['smoke']['approved']
    assert projects.render_current(p,render)
    assert all(s['id']!=f'render:{render["id"]}' for s in projects.stages(p))
    assert projects.stage_dir(p,f'render:{render["id"]}')==root/'renders'/render['id']/'display_frames'
    assert projects.flash_base(p)=='plate'
    assert exports[-1]['profile']==p['native_profile']
    # Both effects start after frame0; no model or review may edit that plate.
    assert (exports[-1]['frames'][0]==originals[0]).all()


def test_stale_approval_is_not_reset_by_explicit_review_candidate(review_shot):
    p,_,blast,smoke,_,_=review_shot
    stale={**copy.deepcopy(smoke),'id':'old-smoke-pin','generation_signature':'old-guide'}
    p['element_takes'].append(stale);p['element_approvals']['smoke']=stale['id'];projects.save(p)
    with pytest.raises(ValueError,match='approved element changed'):
        element_jobs.selected_take(p,p['effects'][1])
    job=element_jobs.review_composite('shot',[blast['id'],smoke['id']])
    current=projects.load('shot')
    assert current['renders'][0]['status']=='done'
    assert current['element_approvals']['smoke']==stale['id']
    assert not next(r for r in element_jobs.records(current) if r['id']==smoke['id'])['approved']
    assert projects.render_current(current,current['renders'][0])


def test_current_pin_cannot_be_replaced_by_an_unreviewed_take(review_shot):
    p,_,blast,smoke,_,_=review_shot
    newer={**copy.deepcopy(blast),'id':'newer-unreviewed'}
    p['element_takes'].insert(0,newer);projects.save(p)
    with pytest.raises(ValueError,match='reuse the current approved'):
        element_jobs.review_composite('shot',[newer['id'],smoke['id']])
    assert not any(r.get('review_only') for r in projects.load('shot')['renders'])
    element_jobs.review_composite('shot',[smoke['id']])
    assert projects.load('shot')['renders'][0]['review_take_ids']==[blast['id'],smoke['id']]


@pytest.mark.parametrize('change',[
    lambda p,r:p['effects'][0]['element'].__setitem__('gain',1.4),
    lambda p,r:p['effects'][0]['element'].__setitem__('seed',193),
    lambda p,r:p['element_approvals'].__setitem__('smoke',r['review_take_ids'][1]),
    lambda p,r:p['element_takes'][0].__setitem__('status','error'),
    lambda p,r:p['element_takes'][0]['assets']['alpha'].__setitem__('sha256','changed'),
])
def test_review_fingerprint_tracks_controls_approvals_and_selected_assets(review_shot,change):
    p,_,blast,smoke,_,_=review_shot
    element_jobs.review_composite('shot',[blast['id'],smoke['id']])
    p=projects.load('shot');render=p['renders'][0]
    assert projects.render_current(p,render)
    change(p,render)
    assert not projects.render_current(p,render)


def test_review_result_cannot_become_a_finish_or_direct_native_source(review_shot):
    p,_,blast,smoke,_,_=review_shot
    element_jobs.review_composite('shot',[blast['id'],smoke['id']])
    p=projects.load('shot');render=p['renders'][0]
    p['flash_base']=f'render:{render["id"]}';projects.save(p)
    assert projects.flash_base(p)=='plate'
    with pytest.raises(ValueError,match='unapproved review composite'):
        projects.native_frame_dir(p,p['flash_base'])
    with pytest.raises(ValueError,match='Generate the effects video'):
        projects.export_final('shot')
    forged={'kind':'final','base':p['flash_base'],'flash_signature':projects.flash_signature(p)}
    assert not projects.render_current(p,forged)
    # Manual approvals plus the regular composite restore the finishing path.
    element_jobs.approve('shot',smoke['id'])
    normal=element_jobs.composite('shot');p=projects.load('shot')
    assert not normal.get('review_only')
    assert projects.flash_base(p)==f'render:{normal["id"]}'
    assert projects.export_final('shot')['status']=='done'


def test_review_validates_immutable_model_frames_before_compositing(review_shot):
    p,_,blast,smoke,_,_=review_shot
    frame=Path(smoke['assets']['alpha']['frames_dir'])/'00000.png'
    frame.write_bytes(b'changed')
    before=copy.deepcopy(projects.load('shot')['renders'])
    with pytest.raises(ValueError):element_jobs.review_composite('shot',[blast['id'],smoke['id']])
    assert projects.load('shot')['renders']==before


def test_review_endpoint_validates_selection_and_idle_state(review_shot):
    p,_,blast,smoke,_,_=review_shot
    app=FastAPI();app.include_router(element_api.router)
    client=TestClient(app,base_url='http://127.0.0.1')
    url='/api/projects/shot/elements/review-composite'
    for body in ({'take_ids':[smoke['id'],smoke['id']]},{'take_ids':[]},{'take_ids':None},{'take_ids':'not-a-list'},
                 {'take_ids':['unknown']},{'take_ids':[{}]},{'take_ids':[smoke['id']],'approve':True}):
        response=client.post(url,json=body)
        assert response.status_code==400,response.text
    response=client.post(url,json={'take_ids':[blast['id'],smoke['id']]})
    assert response.status_code==200,response.text
    assert response.json()['review_only']
    assert projects.load('shot')['element_approvals']==p['element_approvals']
    current=projects.load('shot');current['renders'].insert(0,{'id':'active','kind':'element','status':'rgb'});projects.save(current)
    assert client.post(url,json={'take_ids':[blast['id'],smoke['id']]}).status_code==409


def test_effective_alpha_review_routes_keep_raw_assets_and_approvals_separate(review_shot,monkeypatch):
    from keyfx import alpha_core, alpha_core_review
    p,root,blast,_,_,_=review_shot
    pins=copy.deepcopy(p['element_approvals']);raw=Path(blast['assets']['alpha']['path']).read_bytes()
    signature='a'*64;movie=root/'element_alpha_reviews'/blast['id']/signature/'effective-alpha.mp4'
    movie.parent.mkdir(parents=True);movie.write_bytes(b'local-derived-review')
    def build(project_root,row,take,treatment=None,context=None):
        assert project_root==root and row['id']==blast['id'] and take['status']=='done'
        assert context[0]['id']==p['id'] and context[1]['id']==blast['effect_id']
        assert treatment['matte_multiply']==16 and treatment['matte_erode']==12 and treatment['matte_blur']==3
        return {'signature':signature,'path':str(movie),'video_sha256':elements._sha(movie),'frames':3,'fps':24,
                'width':64,'height':64,'cached':False,'policy':alpha_core.policy(),
                'view_only':True,'model_alpha_unchanged':True}
    def validate(project_root,row,take,version,treatment=None,context=None):
        assert context[0]['id']==p['id'] and context[1]['id']==blast['effect_id']
        if version!=signature:raise ValueError('Mismatched input fingerprint')
        return movie
    monkeypatch.setattr(alpha_core_review,'build',build)
    monkeypatch.setattr(alpha_core_review,'validate_movie',validate)
    app=FastAPI();app.include_router(element_api.router);client=TestClient(app,base_url='http://127.0.0.1')
    prefix=f'/api/projects/shot/element-takes/{blast["id"]}'
    response=client.post(prefix+'/effective-alpha-review')
    assert response.status_code==200,response.text
    data=response.json();assert 'path' not in data and str(root) not in response.text
    assert data['model_alpha_unchanged'] and data['view_only']
    assert data['url'].endswith(f'/alpha-reviews/{signature}/effective-alpha.mp4')
    video=client.get(data['url']);assert video.status_code==200 and video.content==movie.read_bytes()
    assert video.headers['content-type']=='video/mp4'
    assert client.get(data['url'].replace(signature,'b'*64)).status_code==404
    monkeypatch.setattr(alpha_core_review,'build',lambda *a,**kw:(_ for _ in ()).throw(ValueError('Recapture simulation alpha')))
    assert 'Recapture simulation alpha' in client.post(prefix+'/effective-alpha-review').json()['detail']
    assert projects.load('shot')['element_approvals']==pins and Path(blast['assets']['alpha']['path']).read_bytes()==raw


def test_batch_start_atomically_reuses_a_pin_that_appeared_after_capture(review_shot,monkeypatch):
    p,_,blast,_,_,_=review_shot
    p['element_approvals'].pop('blast');projects.save(p)
    candidate=prepare(p)
    assert candidate['id']!=blast['id'] and candidate['status']=='prepared'
    # A second client reviews the existing version during the capture window.
    element_jobs.approve('shot',blast['id'])
    before=copy.deepcopy(projects.load('shot'));calls=[]
    def submitted(*args,**kwargs):
        calls.append(args)
        raise RuntimeError('Offline test observed an individual model attempt')
    monkeypatch.setattr(elements,'run',submitted)
    result=element_jobs.start('shot',candidate['id'],skip_if_approved=True)
    assert result=={'skipped_approved':True,'take_id':blast['id'],'effect_id':'blast'}
    assert calls==[] and projects.load('shot')==before
    app=FastAPI();app.include_router(element_api.router);client=TestClient(app,base_url='http://127.0.0.1')
    route=f'/api/projects/shot/element-takes/{candidate["id"]}'
    response=client.post(route+'/generate-batch')
    assert response.status_code==200 and response.json()==result
    assert calls==[] and projects.load('shot')==before
    # Explicit individual generation retains its existing variation behaviour.
    response=client.post(route+'/generate')
    assert response.status_code==200 and len(calls)==1
    after=projects.load('shot')
    assert after['element_approvals']==before['element_approvals']
    assert next(r for r in after['element_takes'] if r['id']==candidate['id'])['status']=='error'


def test_batch_start_does_not_skip_for_a_stale_pin(review_shot,monkeypatch):
    p,_,blast,_,_,_=review_shot
    candidate=prepare(p);p=projects.load('shot')
    p['element_approvals']['blast']='old-pin'
    p['element_takes'].append({**copy.deepcopy(blast),'id':'old-pin','generation_signature':'old-guide'})
    projects.save(p);calls=[]
    def submitted(*args,**kwargs):
        calls.append(args)
        raise RuntimeError('Offline test observed the intended model attempt')
    monkeypatch.setattr(elements,'run',submitted)
    result=element_jobs.start('shot',candidate['id'],skip_if_approved=True)
    assert result['kind']=='element' and not result.get('skipped_approved') and len(calls)==1
    assert projects.load('shot')['element_approvals']['blast']=='old-pin'


def test_batch_start_fails_closed_if_the_current_pin_has_changed_assets(review_shot,monkeypatch):
    p,_,blast,_,_,_=review_shot
    candidate=prepare(p)
    (Path(blast['assets']['alpha']['frames_dir'])/'00000.png').write_bytes(b'changed')
    before=copy.deepcopy(projects.load('shot'));calls=[]
    monkeypatch.setattr(elements,'run',lambda *a,**k:calls.append(a))
    with pytest.raises(ValueError):element_jobs.start('shot',candidate['id'],skip_if_approved=True)
    assert calls==[] and projects.load('shot')==before
