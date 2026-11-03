"""Default matte-assisted opening and optional H3 reference. No paid inference."""
import copy
import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from keyfx import location,media,people,projects
from keyfx.config import render_length
from keyfx.server import app
from scene_fakes import scene_cloud


@pytest.fixture
def scene(tmp_path,monkeypatch):
    monkeypatch.setattr(projects,'DATA',tmp_path)
    monkeypatch.setattr(projects.comfy,'run_graph',lambda *a,**k:pytest.fail('Unexpected Cloud call'))
    monkeypatch.setattr(projects.comfy,'run_partner_graph',lambda *a,**k:pytest.fail('Unexpected partner call'))
    p={'id':'opening-qa','name':'Opening QA','mode':'greenscreen','frames':3,'created':'2026-10-29',
       'source':{},'shot':'Two actors turn and fall','location':'moonlit rooftop','lighting':'night',
       'opening_reference':'opening.png','reference_mode':'opening_only','environment':None,
       'settings':{'seed':42,'strength':'guide','polish':False},'effects':[],'renders':[],
       'locations':[{'id':'opening','file':'opening.png','frame':0}],
       'people':{'selection':'manual','mode':'guide','points':[{'person':0,'frame':0,'label':1,'x':.14,'y':.4}],'feather':0,'trim':0}}
    d=projects.pdir(p['id']);fd=d/'frames/plate';fd.mkdir(parents=True)
    for i in range(p['frames']):
        f=np.full((540,960,3),50+i,np.uint8)
        f[100:400,80:180]=[100+i,120,140]
        f[100:400,800:900]=[10,20,210]  # unwanted crew retained in the original video
        cv2.imwrite(str(fd/f'{i:05d}.png'),f)
    cv2.imwrite(str(d/'opening.png'),np.full((540,960,3),80,np.uint8))
    matte={'id':'matte','kind':'matte','status':'done','created':'2026-10-29','files':{},'mask_signature':people.mask_signature(p)}
    p['renders'].append(matte)
    md=d/'renders/matte/masks';md.mkdir(parents=True)
    for i in range(p['frames']):
        mask=np.zeros((540,960),np.uint8);mask[100:400,80:180]=255
        cv2.imwrite(str(md/f'{i:05d}.png'),mask)
    projects.save(p)
    return p


def test_extra_reference_is_opt_in_and_does_not_invalidate_the_matte(scene):
    assert people.validate({**scene['people']},3)['extra_reference'] is False
    sig=people.signature(scene);opening=people.opening_signature(scene)
    scene['people']['extra_reference']=True
    assert people.current(scene) is not None
    assert people.signature(scene)!=sig
    assert people.opening_signature(scene)==opening
    with pytest.raises(ValueError,match='extra people reference'):
        people.validate({'extra_reference':'true'},3)
    c=TestClient(app,base_url='http://127.0.0.1:8766')
    response=c.patch('/api/projects/opening-qa',json={'people':scene['people']})
    assert response.status_code==200 and response.json()['people']['extra_reference'] is True
    assert c.get('/api/projects/opening-qa').json()['people']['extra_reference'] is True


@pytest.mark.parametrize('n',[3,55])
def test_optional_video_keeps_all_source_frames_on_same_h3_grid(scene,tmp_path,n):
    fd=projects.pdir(scene['id'])/'frames/plate';md=projects.pdir(scene['id'])/'renders/matte/masks'
    for i in range(3,n):
        cv2.imwrite(str(fd/f'{i:05d}.png'),np.full((540,960,3),50+i,np.uint8))
        cv2.imwrite(str(md/f'{i:05d}.png'),cv2.imread(str(md/'00000.png'),cv2.IMREAD_GRAYSCALE))
    path=people.guide_video(fd,n,md,people.validate(scene['people'],n),tmp_path/'people.mp4')
    fs=media.read_video(path)
    assert len(fs)==render_length(n)
    assert all(f.shape==(544,960,3) for f in fs)
    assert all(np.count_nonzero(f[:2])==0 and np.count_nonzero(f[-2:])==0 for f in fs)
    assert all(np.count_nonzero(f[:,600:])==0 for f in fs)
    assert abs(float(fs[n-1][202,120].mean())-float(media.read_frame(fd,n-1)[200,120].mean()))<2
    assert all(np.array_equal(f,fs[n-1]) for f in fs[n:])


def test_opening_inserts_only_selected_people_and_retains_empty_architecture(scene,monkeypatch,tmp_path):
    d=projects.pdir(scene['id']);frame=media.read_frame(d/'frames/plate',0)
    seen=[];stages=[]
    def edit(im,text,seed,out,model='qwen'):
        seen.append((im.copy(),text,model));out.mkdir(parents=True,exist_ok=True)
        path=out/'location.png'
        cv2.imwrite(str(path),np.full((540,960,3),20 if len(seen)==1 else 230,np.uint8))
        return path
    monkeypatch.setattr(location,'build',edit)
    path=location.build_opening(frame,d/'renders/matte/masks/00000.png',scene['people'],
        'rooftop','night',42,tmp_path/'opening',model='nano',on_stage=stages.append)
    assert len(seen)==2 and all(x[2]=='nano' for x in seen)
    assert 'Remove the people' in seen[0][1]
    np.testing.assert_array_equal(seen[0][0],frame)
    np.testing.assert_array_equal(seen[1][0][200,120],frame[200,120])
    assert np.all(seen[1][0][200,850]==20)  # crew absent from the lighting input
    assert 'facial identity' in seen[1][1] and 'screen position unchanged' in seen[1][1]
    final=cv2.imread(str(path))
    assert np.all(final[200,120]==230)  # lighting result allowed on actors
    assert np.all(final[420,130]==20)   # bright generated detail is not pasted onto the floor
    assert np.all(final[200,850]==20)   # protected architecture/background
    assert stages==['building_location','integrating_people']


@pytest.mark.parametrize('extra',[False,True])
def test_environment_always_gets_full_original_and_extra_is_optional(scene,monkeypatch,extra):
    scene['people']['extra_reference']=extra;projects.save(scene)
    r=projects.start_render(scene['id'],'environment',_background=False)
    # Freeze the choice even if UI settings subsequently change.
    current=projects.load(scene['id']);current['people']['extra_reference']=not extra;projects.save(current)
    d=projects.pdir(scene['id']);raw=media.structure_video(d/'frames/plate',3,d/'fake.mp4')
    seen={}
    monkeypatch.setattr(projects.comfy,'run_graph',scene_cloud(raw,seen))
    projects._run(scene['id'],r['id'])
    done=next(x for x in projects.load(scene['id'])['renders'] if x['id']==r['id'])
    assert done['status']=='done',done.get('error')
    g=seen['graph'];uploads=seen['uploads']
    assert g['cond']['inputs']['ref_videos.ref_video_0']==['proxy_frames',0]
    full=media.read_video(uploads['proxy_video'][1]);assert full[0][202,850,2]>200
    assert ('ref_videos.ref_video_1' in g['cond']['inputs']) is extra
    assert ('people_video' in uploads) is extra
    assert ('<Video 2>' in g['cond']['inputs']['prompt']) is extra
    assert 'only the selected people visible in <Picture 1>' in g['cond']['inputs']['prompt']
    if extra:assert len(media.read_video(uploads['people_video'][1]))==5


def test_opening_requires_current_selection_and_candidate_records_its_provenance(scene,monkeypatch):
    d=projects.pdir(scene['id']);no_people=copy.deepcopy(scene);no_people['people']['points']=[]
    projects.save(no_people)
    with pytest.raises(ValueError,match='Select the people'):
        projects.start_render(scene['id'],'location',_background=False)
    projects.save(scene)
    def opening(frame,mask,cfg,description,lighting,seed,out,**kw):
        assert description=='moonlit rooftop' and mask==d/'renders/matte/masks/00000.png'
        path=out/'location.png';cv2.imwrite(str(path),frame.astype(np.uint8));(out/'prompt.txt').write_text('Selected people integration')
        return path
    monkeypatch.setattr(projects.location,'build_opening',opening)
    r=projects.start_render(scene['id'],'location',_background=False);projects._run(scene['id'],r['id'])
    p=projects.load(scene['id']);candidate=p['locations'][0]
    assert next(x for x in p['renders'] if x['id']==r['id'])['status']=='done'
    assert candidate['opening_method']=='matte' and candidate['people_opening_signature']==people.opening_signature(p)
    assert p['opening_reference']=='opening.png'  # candidate not silently selected
    p['opening_reference']=candidate['file'];p['people']['feather']=2;projects.save(p)
    assert projects.public(p)['locations'][0]['people_current'] is False
    with pytest.raises(ValueError,match='earlier people selection'):
        projects.start_render(scene['id'],'environment',_background=False)


def test_separate_scene_has_distinct_opening_picture_slot(scene,monkeypatch):
    scene.update(reference_mode='scene_and_opening',background='scene.png')
    d=projects.pdir(scene['id']);cv2.imwrite(str(d/'scene.png'),np.full((540,960,3),120,np.uint8));projects.save(scene)
    raw=media.structure_video(d/'frames/plate',3,d/'fake.mp4');seen={}
    monkeypatch.setattr(projects.comfy,'run_graph',scene_cloud(raw,seen))
    r=projects.start_render(scene['id'],'environment',_background=False);projects._run(scene['id'],r['id'])
    assert seen['graph']['cond']['inputs']['ref_images.ref_image_1']==['img_opening',0]
    assert 'only the selected people visible in <Picture 2>' in seen['graph']['cond']['inputs']['prompt']
