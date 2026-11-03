"""Room-photo storage, role routing and frozen requests. No paid generation."""
import io
import json

import cv2
import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from keyfx import comfy,location,projects,room_refs
from keyfx.server import app


def photo(colour=(20,90,180),size=(80,120),orientation=None):
    im=Image.new('RGB',size,colour);out=io.BytesIO()
    if orientation:
        exif=im.getexif();exif[274]=orientation;im.save(out,format='JPEG',exif=exif)
    else:im.save(out,format='PNG')
    return out.getvalue()


@pytest.fixture
def room_project(tmp_path,monkeypatch):
    monkeypatch.setattr(projects,'DATA',tmp_path)
    for method in ['run_graph','run_partner_graph']:
        monkeypatch.setattr(comfy,method,lambda *a,**kw:pytest.fail('Unexpected Cloud inference'))
    p={'id':'room-qa','name':'Room QA','mode':'greenscreen','frames':3,'created':'2026-10-29',
       'source':{},'shot':'Two actors stand','location':'A warm room','location_role':'scene','empty_set':True,
       'opening_reference':None,'environment':None,'settings':{'seed':42},'effects':[],'renders':[]}
    d=projects.pdir(p['id']);fd=d/'frames/plate';fd.mkdir(parents=True)
    for i in range(3):cv2.imwrite(str(fd/f'{i:05d}.png'),np.full((540,960,3),90,np.uint8))
    projects.save(p)
    return p,TestClient(app,base_url='http://127.0.0.1:8766')


def upload(c,values=None):
    values=values or [('room-a.png',photo()),('room-b.png',photo((90,180,20))) ]
    return c.post('/api/projects/room-qa/room-references',files=[('files',(name,data,'image/png')) for name,data in values])


def test_multiple_photos_persist_preview_and_remove_without_touching_history(room_project):
    p,c=room_project
    r=upload(c);assert r.status_code==200
    refs=r.json()['room_references'];assert len(refs)==2
    assert all(x['file'].startswith('room_references/') for x in refs)
    assert refs[0]['id']!=refs[1]['id']
    assert projects.load(p['id'])['location']=='A warm room'
    assert projects.load(p['id'])['opening_reference'] is None
    assert c.get('/api/projects/room-qa').json()['room_references']==refs
    preview=c.get(f'/api/projects/room-qa/room-references/{refs[0]["id"]}')
    assert preview.status_code==200 and preview.headers['content-type']=='image/png'
    immutable=(projects.pdir(p['id'])/refs[0]['file']).read_bytes()
    removed=c.delete(f'/api/projects/room-qa/room-references/{refs[0]["id"]}')
    assert removed.status_code==200 and len(removed.json()['room_references'])==1
    assert (projects.pdir(p['id'])/refs[0]['file']).read_bytes()==immutable
    assert c.get(f'/api/projects/room-qa/room-references/{refs[0]["id"]}').status_code==404


def test_bad_batch_is_atomic_and_upload_caps_are_enforced(room_project,monkeypatch):
    p,c=room_project
    bad=upload(c,[('valid.png',photo()),('bad.png',b'not an image')])
    assert bad.status_code==400 and projects.load(p['id']).get('room_references',[])==[]
    assert not (projects.pdir(p['id'])/'room_references').exists()
    assert upload(c,[(f'{i}.png',photo()) for i in range(6)]).status_code==200
    assert upload(c,[('extra.png',photo())]).status_code==400
    assert len(projects.load(p['id'])['room_references'])==6
    monkeypatch.setattr(room_refs,'MAX_BYTES',20)
    assert upload(c,[('large.png',b'x'*21)]).status_code==400


def test_phone_orientation_and_canonical_image_size():
    ref,data=room_refs.prepare(photo(size=(80,120),orientation=6),'../../room.jpg')
    assert (ref['width'],ref['height'])==(120,80)
    assert ref['name']=='room.jpg' and Image.open(io.BytesIO(data)).format=='PNG'
    ref,_=room_refs.prepare(photo(size=(3000,1000)),'large.png')
    assert (ref['width'],ref['height'])==(3000,1000)


def test_model_limits_fail_before_cloud_and_references_are_optional(room_project):
    p,c=room_project
    assert projects.public(p)['room_references']==[]
    assert upload(c,[(f'{i}.png',photo()) for i in range(3)]).status_code==200
    with pytest.raises(ValueError,match='Qwen accepts two'):
        projects.start_render(p['id'],'location',_background=False)
    assert projects.load(p['id'])['renders']==[]
    p=projects.load(p['id']);p['image_models']={'location':'nano'};projects.save(p)
    r=projects.start_render(p['id'],'location',_background=False)
    assert len(r['room_references'])==3


@pytest.mark.parametrize('model,count',[('qwen',2),('nano',6),('flux3',6)])
def test_model_socket_order_keeps_plate_first(model,count,tmp_path):
    source=tmp_path/'source.png';refs=[tmp_path/f'room{i}.png' for i in range(count)]
    text=location.reference_prompt('Build this room.',count)
    g,uploads,_=location.reference_graph(model,text,42,source,refs)
    if model=='qwen':
        assert uploads['img']==('image',source)
        assert g['enc']['inputs']['pixels']==['img',0]
        assert g['pos']['inputs']['image1']==['img',0]
        for i in range(1,count+1):assert g['pos']['inputs'][f'image{i+1}']==[f'room_{i}',0]
    elif model=='nano':
        assert uploads['11']==('image',source)
        assert g['room_batch']['inputs']['images.image0']==['11',0]
        assert g['35']['inputs']['images']==['room_batch',0]
        for i in range(1,count+1):assert g['room_batch']['inputs'][f'images.image{i}']==[f'room_{i}',0]
    else:
        assert uploads['img']==('image',source)
        assert g['enc']['inputs']['pixels']==['img',0]
        assert g['ref']['inputs']['images.image0']==['img',0]
        for i in range(1,count+1):assert g['ref']['inputs'][f'images.image{i}']==[f'room_{i}',0]
    assert 'sole authority' in text and 'Their camera viewpoints' in text
    assert text.index('Picture 1')<text.index('Picture 2')


def test_render_freezes_photos_and_saves_provenance_after_removal(room_project,monkeypatch):
    p,c=room_project;refs=upload(c).json()['room_references']
    r=projects.start_render(p['id'],'location',_background=False)
    d=projects.pdir(p['id']);snapshots=[d/x['file'] for x in r['room_references']]
    original=[path.read_bytes() for path in snapshots]
    # Simulate later draft changes without altering this queued request.
    current=projects.load(p['id']);current['room_references']=[];projects.save(current)
    def build(frame,text,seed,out,**kw):
        assert kw['references']==snapshots
        assert [path.read_bytes() for path in kw['references']]==original
        assert np.all(frame==90)
        result=out/'location.png';cv2.imwrite(str(result),frame)
        return result
    monkeypatch.setattr(location,'build',build)
    projects._run(p['id'],r['id'])
    done=projects.load(p['id']);assert done['renders'][0]['status']=='done',done['renders'][0].get('error')
    assert done['room_references']==[] and len(done['locations'][0]['room_references'])==2
    assert [x['sha256'] for x in done['locations'][0]['room_references']]==[x['sha256'] for x in refs]


def test_room_photos_only_feed_environment_pass_not_actor_lighting(room_project,monkeypatch,tmp_path):
    p,c=room_project;refs=upload(c).json()['room_references'];d=projects.pdir(p['id'])
    mask=tmp_path/'mask.png';a=np.zeros((540,960),np.uint8);a[100:400,100:200]=255;cv2.imwrite(str(mask),a)
    calls=[]
    def build(frame,text,seed,out,**kw):
        calls.append(kw);out.mkdir(parents=True,exist_ok=True)
        path=out/'location.png';cv2.imwrite(str(path),np.full((540,960,3),70,np.uint8));return path
    monkeypatch.setattr(location,'build',build)
    paths=room_refs.paths(d,refs)
    location.build_opening(np.full((540,960,3),90,np.uint8),mask,{'trim':0,'feather':0},'Room','match',42,tmp_path/'opening',references=paths)
    assert calls[0]['references']==paths
    assert 'references' not in calls[1]


def test_removal_is_blocked_during_processing_and_changed_bytes_are_rejected(room_project):
    p,c=room_project;refs=upload(c).json()['room_references']
    current=projects.load(p['id']);current['renders']=[{'id':'pending','kind':'location','status':'queued','files':{}}];projects.save(current)
    assert c.delete(f'/api/projects/room-qa/room-references/{refs[0]["id"]}').status_code==409
    assert upload(c,[('later.png',photo())]).status_code==409
    current['renders']=[];projects.save(current)
    (projects.pdir(p['id'])/refs[0]['file']).write_bytes(photo((250,0,0)))
    with pytest.raises(ValueError,match='changed on disk'):
        projects.start_render(p['id'],'location',_background=False)


def test_room_photos_can_be_added_and_removed_during_background_action_analysis(room_project):
    p,c=room_project
    p['renders']=[{'id':'background','kind':'analysis','status':'rendering','files':{}}];projects.save(p)
    response=upload(c);assert response.status_code==200
    rid=response.json()['room_references'][0]['id']
    assert c.delete(f'/api/projects/room-qa/room-references/{rid}').status_code==200
    assert projects.load(p['id'])['renders'][0]['status']=='rendering'


def test_nano_letterboxes_room_photos_instead_of_stretching(tmp_path):
    original=tmp_path/'portrait.png';original.write_bytes(photo((70,120,190),size=(100,300)))
    path=location.prepare_reference(original,'nano',tmp_path/'prepared.png')
    im=cv2.imread(str(path));assert im.shape==(1080,1920,3)
    assert np.all(im[0,0]==24)
    assert np.all(im[390:690,910:1010]==[190,120,70])


@pytest.mark.parametrize('model',['qwen','nano','flux3'])
def test_location_dispatch_really_supplies_every_photo_to_selected_model(room_project,monkeypatch,tmp_path,model):
    p,c=room_project;refs=upload(c).json()['room_references'];d=projects.pdir(p['id'])
    paths=room_refs.paths(d,refs);seen=[]
    def result(out):
        out.mkdir(parents=True,exist_ok=True);path=out/'fake.png'
        cv2.imwrite(str(path),np.full((736 if model=='flux3' else 720,1280,3),90,np.uint8));return [path]
    def native(g,uploads,output,out,**kw):
        seen.append(g)
        assert set(uploads)>={'img','room_1','room_2'}
        assert all(path.is_file() for _,path in uploads.values())
        assert np.all(cv2.imread(str(uploads['img'][1]))==90)
        if model=='flux3':assert 'mask' in uploads
        return result(out)
    def partner(g,node,source,output,out,description,additional_images=None):
        seen.append(g)
        assert node=='11' and set(additional_images)=={'room_1','room_2'}
        assert all(cv2.imread(str(path)).shape==cv2.imread(str(source)).shape for path in additional_images.values())
        assert np.all(cv2.imread(str(source))==90)
        return result(out)
    monkeypatch.setattr(comfy,'run_graph',native);monkeypatch.setattr(comfy,'run_partner_graph',partner)
    out=location.build(np.full((540,960,3),90,np.uint8),'Build the environment.',42,tmp_path/model,model=model,references=paths)
    assert out.exists() and len(seen)==1
    prompt=(out.parent/'prompt.txt').read_text()
    assert 'Picture 1 is the original plate' in prompt and 'Picture 3 is room reference 2' in prompt
    assert (out.parent/'reference_map.json').exists()
