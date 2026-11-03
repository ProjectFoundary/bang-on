"""Source preservation and version/provenance regressions; no Cloud calls."""
import copy
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from keyfx import projects, setdressing
from keyfx.server import app


def draft(source='scene.png'):
    return {'source': source, 'seed': 281040, 'margin': .04, 'people': True,
            'objects': [{'id': 'left', 'text': 'a wooden crate', 'action': 'add',
                         'box': {'x0': .02, 'y0': .65, 'x1': .25, 'y1': .98}},
                        {'id': 'right', 'text': 'a small distant crate', 'action': 'add',
                         'box': {'x0': .91, 'y0': .58, 'x1': .985, 'y1': .69}}],
            'protected': [{'id': 'person', 'box': {'x0': .4, 'y0': .25, 'x1': .89, 'y1': 1.}}]}


@pytest.fixture
def shot(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, 'DATA', tmp_path)
    class Deferred:
        def __init__(self, **kw): pass
        def start(self): pass
    monkeypatch.setattr(projects.threading, 'Thread', Deferred)
    monkeypatch.setattr(setdressing.comfy, 'client', lambda: pytest.fail('Unexpected Cloud call'))
    p = {'id': 'dressing', 'name': 'Placement', 'mode': 'greenscreen', 'frames': 3, 'created': '2026-10-28',
         'source': {}, 'shot': 'A soldier aims', 'location': 'warehouse', 'settings': {'seed': 7},
         'effects': [], 'flashes': [], 'renders': [], 'environment': None,
         'background': 'scene.png', 'opening_reference': 'opening.png',
         'locations': [{'id': 'scene', 'file': 'scene.png', 'frame': 40, 'role': 'scene', 'empty_set': False},
                       {'id': 'opening', 'file': 'opening.png', 'frame': 0, 'role': 'opening', 'empty_set': False}]}
    d = projects.pdir(p['id']); d.mkdir(parents=True)
    rng = np.random.default_rng(81)
    image = rng.integers(0, 255, (720, 1280, 3), np.uint8)
    for file in ('scene.png', 'opening.png'): cv2.imwrite(str(d / file), image)
    projects.save(p)
    return p


@pytest.mark.parametrize('change', [
    lambda d: d['objects'][0]['box'].update(x1=.001),
    lambda d: d['objects'][0]['box'].update(x0=float('nan')),
    lambda d: d['objects'][0].update(action='move'),
    lambda d: d['objects'][0].update(id='person'),
    lambda d: d.update(seed=1.2),
    lambda d: d.update(margin=.9),
    lambda d: d.update(people='yes'),
])
def test_malformed_requests_are_rejected_before_cloud(change):
    d = draft(); change(d)
    with pytest.raises(ValueError): setdressing.validate(d)


def test_two_boxes_use_native_yxyx_grid_with_padding():
    d = setdressing.validate(draft())
    text = setdressing.caption(d, (1280, 720), (1280, 736))
    rows = json.loads(text[text.rfind('[{'):])
    assert rows[0]['bbox'] == [636, 20, 959, 250]
    assert rows[1]['bbox'] == [567, 910, 675, 985]
    assert '<obj_1>' in text and '<obj_2>' in text
    assert 'grey' not in text


@pytest.mark.parametrize('size', [(1280, 720), (2560, 1440)])
def test_real_build_preserves_source_pixels_and_uploads_unpainted_photo(tmp_path, monkeypatch, size):
    w, h = size
    rng = np.random.default_rng(8)
    src = rng.integers(0, 255, (h, w, 3), np.uint8)
    source = tmp_path / 'source.png'; cv2.imwrite(str(source), src)
    original_bytes = source.read_bytes()
    captured = {}
    def render(g, inputs, out, on_status=None):
        im = cv2.imread(str(inputs / 'source_padded.png'))
        captured.update(image=im, inputs=inputs, graph=g)
        assert np.array_equal(im[:h,:w], src)
        result = out / 'raw.png'; cv2.imwrite(str(result), np.full_like(im, (40,150,210)))
        return result
    monkeypatch.setattr(setdressing, '_render', render)
    d = setdressing.validate(draft()); out = tmp_path / 'render'
    final = cv2.imread(str(setdressing.build(source,d,out)))
    noise, permission, protected, alpha = setdressing.masks((w,h),d)
    assert np.array_equal(src[permission==0],final[permission==0])
    assert np.array_equal(src[protected>0],final[protected>0])
    assert np.any(src[alpha==1] != final[alpha==1])
    assert source.read_bytes() == original_bytes
    assert np.array_equal(captured['image'][:720],cv2.imread(str(captured['inputs']/'source_padded.png'))[:720])
    assert captured['graph']['ref']['inputs']['images.image0'] == ['img',0]


def test_protection_blocks_fully_covered_targets():
    d = setdressing.validate(draft())
    d['protected'] = [{'id':'all','box':{'x0':0.,'y0':0.,'x1':1.,'y1':1.}}]
    with pytest.raises(ValueError,match='completely covered'):setdressing.masks((1280,720),d)


def test_source_approval_and_people_protection_are_required(shot):
    shot['set_dressing'] = draft(); projects.save(shot)
    with pytest.raises(ValueError,match='approve'): projects.start_render(shot['id'],'dressing')
    p = projects.approve_dressing_source(shot['id'],'scene.png')
    p['set_dressing'] = draft(); p['set_dressing']['protected'] = []; projects.save(p)
    with pytest.raises(ValueError,match='protected areas'):projects.start_render(shot['id'],'dressing')
    p['set_dressing']['people'] = False; projects.save(p)
    assert projects.start_render(shot['id'],'dressing')['status']=='queued'
    with pytest.raises(ValueError,match='current object edit'):projects.start_render(shot['id'],'dressing')


def test_changed_approved_source_fails_before_submission(shot):
    p = projects.approve_dressing_source(shot['id'],'scene.png')
    p['set_dressing'] = draft(); projects.save(p)
    cv2.imwrite(str(projects.pdir(shot['id'])/'scene.png'),np.zeros((720,1280,3),np.uint8))
    with pytest.raises(ValueError,match='approve'): projects.start_render(shot['id'],'dressing')
    assert not projects.load(shot['id'])['renders']


def test_frozen_request_and_source_snapshot_survive_edits_and_candidate_is_not_selected(shot, monkeypatch):
    p = projects.approve_dressing_source(shot['id'],'scene.png');p['set_dressing']=draft();projects.save(p)
    r = projects.start_render(shot['id'],'dressing')
    p = projects.load(shot['id']);p['set_dressing']['objects'][0]['text']='a giant elephant';projects.save(p)
    cv2.imwrite(str(projects.pdir(shot['id'])/'scene.png'),np.zeros((720,1280,3),np.uint8))
    def render(g, inputs, out, on_status=None):
        assert 'elephant' not in g['pos']['inputs']['text']
        assert cv2.imread(str(inputs/'source_padded.png')).mean()>100
        path=out/'raw.png';cv2.imwrite(str(path),cv2.imread(str(inputs/'source_padded.png')))
        return path
    monkeypatch.setattr(setdressing,'_render',render)
    projects._run(shot['id'],r['id'])
    p=projects.load(shot['id']);result=next(x for x in p['renders'] if x['id']==r['id'])
    assert result['status']=='done',result.get('error')
    version=p['locations'][0]
    assert version['parent_source']=='scene.png' and version['frame']==40
    assert version['objects'][0]['text']=='a wooden crate'
    assert p['background']=='scene.png' and p['opening_reference']=='opening.png'
    assert p['set_dressing']['objects'][0]['text']=='a giant elephant'
    assert not version.get('dressing_approved_at')
    assert version['file'] not in {p['background'],p['opening_reference']}


def test_api_binds_draft_to_approved_source_and_inherits_protection(shot):
    client=TestClient(app,base_url='http://127.0.0.1:8766')
    base=f'/api/projects/{shot["id"]}'
    assert client.patch(base,json={'set_dressing':draft()}).status_code==400
    approved=client.post(base+'/dressing/source',json={'file':'scene.png'})
    assert approved.status_code==200 and approved.json()['set_dressing']['source']=='scene.png'
    assert client.patch(base,json={'set_dressing':draft()}).status_code==200
    assert client.patch(base,json={'set_dressing':draft('../escape.png')}).status_code==400
    assert client.post(base+'/dressing/source',json={'file':'../escape.png'}).status_code==400
    changed=client.post(base+'/dressing/source',json={'file':'opening.png'}).json()
    assert changed['set_dressing']['objects']==[]  # coordinates are never shifted to another base
    p=projects.load(shot['id']);p['locations'][0]['protected_areas']=draft()['protected'];projects.save(p)
    changed=client.post(base+'/dressing/source',json={'file':'scene.png'}).json()
    assert changed['set_dressing']['protected']==draft()['protected']


def test_unknown_submission_is_never_automatically_resubmitted(tmp_path, monkeypatch):
    class Client:
        def close(self):pass
    monkeypatch.setattr(setdressing.comfy,'client',lambda:Client())
    (tmp_path/'submission_started.json').write_text('{}')
    with pytest.raises(RuntimeError,match='outcome is unknown'):setdressing._render({},tmp_path,tmp_path)


def test_executed_recipe_check_allows_upload_names_but_catches_model_or_prompt_changes():
    expected=setdressing.graph('original instruction',7)
    actual=copy.deepcopy(expected)
    actual['img']['inputs']['image']='cloud-upload-123.png'
    assert setdressing.workflow_differences(expected,actual)==[]
    actual['pos']['inputs']['text']='different instruction'
    actual['unet']['inputs']['unet_name']='other-model.safetensors'
    assert setdressing.workflow_differences(expected,actual)==[['unet','unet_name'],['pos','text']]
