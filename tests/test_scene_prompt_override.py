"""A prompt-only scene experiment must bypass default text and preserve the video graph."""
import json

import pytest
from fastapi.testclient import TestClient

from keyfx import media, projects, prompts
from keyfx.server import app
from test_opening_workflow import scene
from scene_fakes import scene_cloud


def test_override_is_exact_frozen_and_does_not_inherit_template_exclusions(scene,monkeypatch):
    p=scene;p['people']={'selection':'auto'};projects.save(p)
    d=projects.pdir(p['id']);raw=media.structure_video(d/'frames/plate',p['frames'],d/'fake.mp4')
    text='subject_definitions:\n<Subject 1> is the performer.\n\nsummary: A continuous rooftop view.'
    r=projects.start_render(p['id'],'environment',_background=False,prompt_override=text)
    frozen=json.loads((d/'renders'/r['id']/'request.json').read_text())
    assert frozen['render']['prompt_override']==text
    # Later user edits must not change the submitted prompt or inputs.
    projects.update(p['id'],lambda q:q.update(shot='A new description',location='A different room'))
    monkeypatch.setattr(prompts,'environment_prompt',lambda *a,**kw:pytest.fail('Default prompt must not be assembled'))
    seen={}
    monkeypatch.setattr(projects.comfy,'run_graph',scene_cloud(raw,seen))
    projects._run(p['id'],r['id'])
    done=next(x for x in projects.load(p['id'])['renders'] if x['id']==r['id'])
    assert done['status']=='done',done.get('error')
    assert seen['graph']['cond']['inputs']['prompt']==text
    assert (d/'renders'/r['id']/'prompt.txt').read_text()==text
    assert seen['graph']['noise']['inputs']['noise_seed']==42
    assert 'people_video' not in seen['uploads']


@pytest.mark.parametrize('value',['', '   ',False,32,'x'*12001])
def test_invalid_overrides_are_rejected_before_creating_a_job(scene,value):
    p=scene;before=len(p['renders'])
    with pytest.raises(ValueError,match='1–12000'):
        projects.start_render(p['id'],'environment',_background=False,prompt_override=value)
    assert len(projects.load(p['id'])['renders'])==before


def test_override_cannot_accidentally_modify_other_passes(scene):
    with pytest.raises(ValueError,match='only for a scene video test'):
        projects.start_render(scene['id'],'effect',_background=False,prompt_override='A custom scene')


def test_api_accepts_the_explicit_override_but_default_generations_remain_unchanged(scene,monkeypatch):
    class Deferred:
        def __init__(self,**kw):pass
        def start(self):pass
    monkeypatch.setattr(projects.threading,'Thread',Deferred)
    c=TestClient(app,base_url='http://127.0.0.1:8766');url=f'/api/projects/{scene["id"]}/render'
    response=c.post(url,json={'kind':'environment','prompt_override':'A positive scene prompt.'})
    assert response.status_code==200,response.text
    assert response.json()['prompt_override']=='A positive scene prompt.'
    rid=response.json()['id']
    projects.update(scene['id'],lambda p:next(r for r in p['renders'] if r['id']==rid).update(status='done'))
    default=c.post(url,json={'kind':'environment'})
    assert default.status_code==200,default.text
    assert 'prompt_override' not in default.json()
