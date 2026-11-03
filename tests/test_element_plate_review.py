"""Free selected-element review must match the native final composite."""
import copy
import hashlib
import json
from pathlib import Path

import cv2
from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
import pytest

from keyfx import element_api, element_jobs, elements, media, native_media, presets, projects
from test_element_pipeline import generate, pipeline, prepare, catalogue
from keyfx import element_graphs


def no_spend(*args, **kwargs):
    pytest.fail('Local review attempted provider work')


def test_selected_review_is_free_unapproved_and_matches_native_final(pipeline, monkeypatch):
    create,exports,_=pipeline;p,root,originals=create(fps='24/1');row,calls=generate(p,monkeypatch)
    p=projects.load('shot')
    unfinished=presets.new_effect('smoke_grenade',0,.3,.6,'unready',p['frames'])
    p['effects'].append(unfinished);projects.save(p)
    before=(root/'project.json').read_bytes();assets=copy.deepcopy(p['element_takes'])
    source_hashes=[hashlib.sha256((root/'native'/'frames'/f'{i:05d}.png').read_bytes()).hexdigest() for i in range(p['frames'])]
    monkeypatch.setattr(elements,'run',no_spend)
    native_frames=[];real=element_jobs._composite_frame
    def observe(*args):
        frame=real(*args);native_frames.append(frame.copy());return frame
    monkeypatch.setattr(element_jobs,'_composite_frame',observe)
    review=element_jobs.review_over_plate('shot',row['id'])
    assert review['selected_only'] and not review['photographic_master'] and not review['cached']
    assert (review['frames'],review['fps'],review['width'],review['height'])==(8,24,18,32)
    assert review['source_frames']==list(range(8))
    assert (root/'project.json').read_bytes()==before and not exports
    assert projects.load('shot')['element_takes']==assets
    assert projects.load('shot').get('element_approvals',{})=={}
    assert np.array_equal(native_frames[0],originals[0]) and np.array_equal(native_frames[1],originals[1])
    assert not np.array_equal(native_frames[3],originals[3])
    cache=root/'element_reviews'/row['id']/review['signature']
    probe=media.probe(cache/'review.mp4');assert (probe['frames'],probe['width'],probe['height'])==(8,18,32)
    monkeypatch.setattr(element_jobs,'_composite_frame',no_spend)
    assert element_jobs.review_over_plate('shot',row['id'])['cached']
    assert (root/'project.json').read_bytes()==before
    monkeypatch.setattr(element_jobs,'_composite_frame',real)
    p=projects.load('shot');p['effects'].pop();projects.save(p)
    element_jobs.approve('shot',row['id']);element_jobs.composite('shot')
    assert len(exports)==1 and all(np.array_equal(a,b) for a,b in zip(native_frames,exports[0]['frames']))
    assert calls==['rgb','alpha']
    assert source_hashes==[hashlib.sha256((root/'native'/'frames'/f'{i:05d}.png').read_bytes()).hexdigest() for i in range(p['frames'])]


def test_review_rejects_outdated_or_tampered_take_without_new_state(pipeline,monkeypatch):
    create,_,_=pipeline;p,root,_=create(fps='24/1');row,_=generate(p,monkeypatch)
    monkeypatch.setattr(elements,'run',no_spend)
    p=projects.load('shot');p['effects'][0]['size']*=2;projects.save(p);before=(root/'project.json').read_bytes()
    with pytest.raises(ValueError,match='outdated'):element_jobs.review_over_plate('shot',row['id'])
    assert (root/'project.json').read_bytes()==before and not (root/'element_reviews').exists()
    p['effects'][0]['size']/=2;projects.save(p)
    alpha=Path(row['assets']['alpha']['frames_dir'])/'00000.png';im=cv2.imread(str(alpha),0);im[0,0]^=255;cv2.imwrite(str(alpha),im)
    with pytest.raises(ValueError,match='cached RGB/alpha asset'):element_jobs.review_over_plate('shot',row['id'])
    assert not (root/'element_reviews').exists()


def test_review_endpoints_cache_integrity_and_busy_guard(pipeline,monkeypatch):
    create,_,_=pipeline;p,root,_=create(fps='24/1');row,calls=generate(p,monkeypatch)
    monkeypatch.setattr(elements,'run',no_spend)
    app=FastAPI();app.include_router(element_api.router);client=TestClient(app)
    url=f'/api/projects/shot/element-takes/{row["id"]}/review'
    response=client.post(url);assert response.status_code==200,response.text;review=response.json()
    movie=client.get(review['url']);assert movie.status_code==200 and movie.headers['content-type']=='video/mp4'
    assert client.post(url).json()['cached']
    assert client.post('/api/projects/shot/element-takes/missing/review').status_code==400
    assert client.get(review['url'].replace(review['signature'],'bad')).status_code==404
    path=root/'element_reviews'/row['id']/review['signature']/'review.mp4';path.write_bytes(b'tampered')
    assert client.get(review['url']).status_code==409
    p=projects.load('shot');p['renders'].append({'id':'busy','kind':'element','status':'queued'});projects.save(p)
    assert client.post(url).status_code==409
    assert calls==['rgb','alpha'] and projects.load('shot').get('element_approvals',{})=={}


def test_post_adjustment_versions_only_local_review(pipeline,monkeypatch):
    create,_,_=pipeline;p,root,_=create(fps='24/1');row,calls=generate(p,monkeypatch);monkeypatch.setattr(elements,'run',no_spend)
    first=element_jobs.review_over_plate('shot',row['id']);p=projects.load('shot')
    p['effects'][0]['element'].update(opacity=.4,gain=1.2,light=.2,shadow=.3);projects.save(p)
    second=element_jobs.review_over_plate('shot',row['id'])
    assert first['signature']!=second['signature'] and not second['cached']
    assert projects.load('shot')['element_takes']==p['element_takes'] and calls==['rgb','alpha']


def test_review_samples_native_timeline_as_explicit_24fps_viewing_copy(pipeline,monkeypatch):
    create,_,_=pipeline;p,_,_=create(fps='60/1');row,_=generate(p,monkeypatch);monkeypatch.setattr(elements,'run',no_spend)
    review=element_jobs.review_over_plate('shot',row['id'])
    times=p['native_profile']['timestamps']
    expected=[min(range(len(times)),key=lambda f:abs(times[f]-i/24)) for i in range(review['frames'])]
    assert review['fps']==24 and review['frames']==4 and review['source_frames']==expected


def test_completed_rgb_survives_failed_alpha_and_is_independently_reviewable(pipeline,monkeypatch):
    create,_,_=pipeline;p,root,_=create(fps='24/1');row=prepare(p);calls=[];actual=elements.run
    def provider(*,graph,uploads,out_dir,on_status,**kwargs):
        stage='alpha' if 'alpha-gen' in graph['lora']['inputs']['lora_name'] else 'rgb';calls.append(stage)
        g=graph['latent']['inputs'];value=(0,0,255) if stage=='alpha' else (40,60,80)
        frames=[np.broadcast_to(np.array(value,np.uint8),(g['height'],g['width'],3)).copy() for _ in range(g['length'])]
        out_dir.mkdir(parents=True,exist_ok=True);path=out_dir/'result.mp4';elements._encode(path,frames)
        on_status('fake-'+stage,'running');return [path]
    nodes=catalogue([g for _,_,g,_ in element_graphs.requirements({})])
    monkeypatch.setattr(elements,'run',lambda td,on_status=None:actual(td,executor=provider,catalog=nodes,on_status=on_status))
    element_jobs.start('shot',row['id']);p=projects.load('shot');failed=p['element_takes'][0]
    assert failed['status']=='error' and set(failed['assets'])=={'rgb'} and calls==['rgb','alpha']
    assert failed.get('render_id') and p['renders'][0]['status']=='error'
    monkeypatch.setattr(elements,'run',no_spend)
    app=FastAPI();app.include_router(element_api.router);client=TestClient(app)
    rgb=f'/api/projects/shot/element-takes/{row["id"]}/rgb.mp4'
    assert client.get(rgb).status_code==200
    assert client.get(rgb.replace('/rgb.mp4','/alpha.mp4')).status_code==404
    assert client.post(f'/api/projects/shot/element-takes/{row["id"]}/approve').status_code==400
    assert client.post(f'/api/projects/shot/element-takes/{row["id"]}/review').status_code==400
    assert client.post('/api/projects/shot/elements/composite').status_code==400
    # Older failed rows did not publish successful assets. Read-only listing
    # still finds the independently verified RGB without rewriting that row.
    failed['assets']={};projects.save(p);before=(root/'project.json').read_bytes()
    public=client.get('/api/projects/shot/elements').json()['takes'][0]
    assert set(public['assets'])=={'rgb'} and public['assets']['rgb']['url']==rgb
    assert (root/'project.json').read_bytes()==before
    take=elements.inspect(row['take_dir']);image=Path(take['assets']['rgb']['frames_dir'])/'00000.png'
    im=cv2.imread(str(image));im[0,0]^=255;cv2.imwrite(str(image),im)
    assert client.get(rgb).status_code==409
    assert client.get('/api/projects/shot/elements').json()['takes'][0]['assets']=={}
    assert calls==['rgb','alpha']


def test_local_download_recovery_syncs_take_without_spend_or_approval(pipeline,monkeypatch):
    create,_,_=pipeline;p,root,_=create(fps='24/1');row=prepare(p);calls=[];actual=elements.run
    monkeypatch.setattr(elements,'ALPHA_CHANNEL_SPREAD_LIMIT',4)
    def provider(*,graph,uploads,out_dir,on_status,**kwargs):
        stage='alpha' if 'alpha-gen' in graph['lora']['inputs']['lora_name'] else 'rgb';calls.append(stage)
        g=graph['latent']['inputs'];value=(123,128,133) if stage=='alpha' else (40,60,80)
        frames=[np.broadcast_to(np.array(value,np.uint8),(g['height'],g['width'],3)).copy() for _ in range(g['length'])]
        out_dir.mkdir(parents=True,exist_ok=True);path=out_dir/'result.mp4';elements._encode(path,frames)
        on_status('fake-'+stage,'running');return [path]
    nodes=catalogue([g for _,_,g,_ in element_graphs.requirements({})])
    monkeypatch.setattr(elements,'run',lambda td,on_status=None:actual(td,executor=provider,catalog=nodes,on_status=on_status))
    element_jobs.start('shot',row['id']);p=projects.load('shot');assert p['element_takes'][0]['status']=='error'
    rgb=copy.deepcopy(p['element_takes'][0]['assets']['rgb'])
    state=elements.inspect(row['take_dir']);sp=Path(state['stages']['alpha']['cache_dir'])
    frozen={path:hashlib.sha256(path.read_bytes()).hexdigest() for path in [Path(row['take_dir'])/'request.json',sp/'graph.json',sp/'claim',*list((sp/'raw').glob('*.mp4'))]}
    app=FastAPI();app.include_router(element_api.router);client=TestClient(app)
    public=client.get('/api/projects/shot/elements').json()['takes'][0]
    candidate=public['recoverable_stages'][0];assert candidate['stage']=='alpha'
    endpoint=f'/api/projects/shot/element-takes/{row["id"]}/recover-downloaded'
    before=(root/'project.json').read_bytes()
    assert client.post(endpoint,json={'stage':'alpha','expected_raw_sha256':'0'*64}).status_code==400
    assert (root/'project.json').read_bytes()==before
    monkeypatch.setattr(elements,'ALPHA_CHANNEL_SPREAD_LIMIT',12);monkeypatch.setattr(elements,'run',no_spend)
    body={'stage':'alpha','expected_raw_sha256':candidate['raw_sha256']}
    response=client.post(endpoint,json=body);assert response.status_code==200,response.text
    recovered=projects.load('shot');take=recovered['element_takes'][0]
    assert take['status']=='done' and set(take['assets'])=={'rgb','alpha'} and take['assets']['rgb']==rgb
    assert recovered['renders'][0]['status']=='done' and recovered['renders'][0]['recovered_locally']
    assert recovered.get('element_approvals',{})=={} and calls==['rgb','alpha']
    assert all(hashlib.sha256(path.read_bytes()).hexdigest()==sha for path,sha in frozen.items())
    assert client.get(f'/api/projects/shot/element-takes/{row["id"]}/alpha.mp4').status_code==200
    after=(root/'project.json').read_bytes();assert client.post(endpoint,json=body).status_code==200
    assert (root/'project.json').read_bytes()==after
    assert client.post(endpoint,json={**body,'extra':'unsafe'}).status_code==400
