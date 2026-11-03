"""Source identity in stills and optional people selection; no Cloud calls."""
import copy

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from keyfx import location, media, people, projects
from keyfx.server import app
from test_opening_workflow import scene
from scene_fakes import scene_cloud


def test_automatic_is_default_and_needs_no_matte():
    assert people.validate({},3)['selection']=='auto'
    assert people.current({'frames':3,'people':{},'renders':[]}) is None


@pytest.mark.parametrize('import_mode',['greenscreen','plate','saved-points'])
def test_location_api_accepts_automatic_without_matte_in_both_entry_paths(scene,monkeypatch,import_mode):
    p=copy.deepcopy(scene);p.update(mode='plate' if import_mode=='plate' else 'greenscreen',renders=[])
    p['people']={'points':p['people']['points']} if import_mode=='saved-points' else {'selection':'auto'}
    projects.save(p)
    monkeypatch.setattr(projects,'_run',lambda *a:None)  # Register only; no Cloud work.
    c=TestClient(app,base_url='http://127.0.0.1:8766');url=f'/api/projects/{p["id"]}'
    if import_mode=='plate':
        assert c.patch(url,json={'mode':'greenscreen'}).status_code==200
    response=c.post(url+'/render',json={'kind':'location'})
    assert response.status_code==200,response.text
    assert response.json()['kind']=='location'
    assert [r['kind'] for r in projects.load(p['id'])['renders']]==['location']


def test_app_entry_is_not_cached_between_ui_updates():
    response=TestClient(app,base_url='http://127.0.0.1:8766').get('/')
    assert response.headers['cache-control']=='no-store'


def test_saved_points_do_not_opt_in_but_remain_reusable_with_explicit_manual_mode(scene):
    legacy=copy.deepcopy(scene);legacy['people'].pop('selection')
    assert people.validate(legacy['people'],3)['selection']=='auto'
    assert people.current(legacy) is None and legacy['people']['points']
    legacy['people']['selection']='manual'
    signature=people.opening_signature(legacy);assert people.current(legacy)
    legacy['people']['selection']='auto'
    assert people.current(legacy) is None and people.signature(legacy) is None
    assert people.opening_signature(legacy)!=signature
    assert legacy['people']['points']  # optional selection remains recoverable
    legacy['people']['selection']='manual'
    assert people.current(legacy) and people.opening_signature(legacy)==signature
    with pytest.raises(ValueError,match='Automatic or Manual'):
        people.validate({'selection':'unexpected'},3)


def test_automatic_opening_requires_no_tracked_matte_and_records_provenance(scene,monkeypatch):
    p=copy.deepcopy(scene);p['people']={};p['renders']=[]
    d=projects.pdir(p['id']);fd=d/'frames/plate'
    projects.save(p)
    seen=[]
    def opening(frame,text,seed,out,**kw):
        np.testing.assert_array_equal(frame,cv2.imread(str(fd/'00000.png')))
        assert 'original face, hair or headwear' in text and 'motion blur' in text
        seen.append(text)
        path=out/'location.png';cv2.imwrite(str(path),frame);(out/'prompt.txt').write_text(text)
        return path
    monkeypatch.setattr(location,'build',opening)
    r=projects.start_render(p['id'],'location',_background=False)
    projects._run(p['id'],r['id']);p=projects.load(p['id'])
    assert seen and p['renders'][0]['status']=='done',p['renders'][0].get('error')
    assert len(seen)==1 and p['locations'][0]['opening_method']=='plate'
    assert p['locations'][0]['people_opening_signature']==people.opening_signature(p)
    assert projects.public(p)['people_matte'] is None
    assert p['opening_reference']=='opening.png'  # no replacement of the selected take
    assert not list((d/'renders'/r['id']).glob('*mask*'))
    with pytest.raises(ValueError,match='Manual selection'):projects.start_render(p['id'],'matte',_background=False)
    p['people']={'selection':'manual','mode':'guide','points':[]};projects.save(p)
    assert projects.public(p)['locations'][0]['people_current'] is False


def test_default_ignores_saved_selection_until_manual_is_chosen(scene):
    p=copy.deepcopy(scene);p['people']['selection']='auto';projects.save(p)
    assert people.current(p) is None and p['people']['points']
    r=projects.start_render(p['id'],'location',_background=False)
    assert r['kind']=='location' and people.current(projects.load(p['id'])) is None


def test_default_scene_video_uses_full_original_and_no_isolated_people_video(scene,monkeypatch):
    p=copy.deepcopy(scene);p['people']['selection']='auto';projects.save(p)
    d=projects.pdir(p['id']);raw=media.structure_video(d/'frames/plate',3,d/'fake.mp4');seen={}
    monkeypatch.setattr(projects.comfy,'run_graph',scene_cloud(raw,seen))
    r=projects.start_render(p['id'],'environment',_background=False);projects._run(p['id'],r['id'])
    done=next(x for x in projects.load(p['id'])['renders'] if x['id']==r['id'])
    assert done['status']=='done',done.get('error')
    assert 'people_video' not in seen['uploads']
    assert 'ref_videos.ref_video_1' not in seen['graph']['cond']['inputs']
    full=media.read_video(seen['uploads']['proxy_video'][1])
    assert full[0][202,850,2]>200  # original background/crew preserved as motion evidence


def test_original_cutout_still_requires_tracked_selection(scene):
    p=copy.deepcopy(scene);p['people']={'selection':'auto','mode':'cutout'};projects.save(p)
    for kind in ['location','environment']:
        with pytest.raises(ValueError,match='Original cutout needs a tracked matte'):
            projects.start_render(p['id'],kind,_background=False)


def test_relight_keeps_source_texture_and_silhouette_instead_of_generated_person():
    frame=np.full((540,960,3),60,np.uint8)
    frame[100:400,200:500]=np.where(np.arange(300)[None,:,None]%2,90,130)
    a=np.zeros((540,960,1),np.float32);a[100:400,200:500]=1
    base=np.full_like(frame,100)
    edited=np.full_like(frame,180)
    edited[60:110,200:500]=240   # invented helmet beyond source silhouette
    edited[200:300,200:500]=np.where(np.arange(300)[None,:,None]%2,240,20)  # inverted AI texture
    result=location.preserve_people_lighting(frame,base,edited,a)
    # Original one-pixel texture remains in its original phase, despite the
    # generated replacement having the opposite details. Lighting does change.
    row=result[240,260:440,0].astype(float)
    assert np.all(row[::2]>row[1::2]) and float(row.mean())>float(frame[240,260:440,0].mean())
    assert np.array_equal(result[70,300],base[70,300])
    assert np.array_equal(result[200,700],base[200,700])
    assert result.shape==frame.shape
    # Shadow transfer can darken the contact floor without copying AI detail.
    dark=np.full_like(frame,30)
    shadowed=location.preserve_people_lighting(frame,base,dark,a)
    assert np.all(shadowed[405,350]<base[405,350])
    assert np.array_equal(shadowed[80,700],base[80,700])


def test_prompt_preserves_blur_and_does_not_restyle_person():
    text=location.edit_prompt('Neon street',[],'night',True)
    assert 'motion blur' in text and 'original face, hair or headwear' in text
    assert 'appearance and geometry stay fixed' in text
    empty=location.edit_prompt('Neon street',[],'night',False)
    assert 'Remove the people' in empty and 'Preserve each existing person' not in empty
