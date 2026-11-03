"""Fire is a continuous native element with ignition timing and separate alpha."""
from pathlib import Path

import pytest

from keyfx import element_graphs, element_jobs, geometry, presets


def shot():
    p={'id':'fire-test','mode':'plate','frames':144,'effects':[],
       'geometry':geometry.defaults(),'settings':{'pipeline':'ltx_elements'},
       'native_profile':{'width':320,'height':180,'fps':24.,'fps_rational':'24/1',
                         'timestamps':[i/24 for i in range(144)],'duration':6.,'source_sha256':'fixture'}}
    e=presets.new_effect('fire',24,.4,.6,'flame',144)
    e['element']={**element_jobs.DEFAULT,'duration_s':3.}
    p['effects']=[e]
    return p,e


def test_fire_uses_ignition_even_when_a_later_phase_was_retimed_earlier():
    p,e=shot()
    next(k for k in e['keys'] if k['phase']=='burning')['frame']=3
    plan=element_jobs.plan(p,e['id'])
    assert plan['onset_frame']==24 and plan['start_frame']==24
    assert plan['start_time']==1 and plan['active_frames']==72
    assert element_jobs.model_index(plan,.99) is None
    assert element_jobs.model_index(plan,1)==0
    assert element_jobs.model_index(plan,4) is None


def test_fire_recipe_uses_continuous_flame_prompt_and_independent_alpha_stage():
    p,e=shot();plan=element_jobs.plan(p,e['id'])
    recipe=element_jobs._recipe(p,e,plan)
    assert 'continuous ground fire' in recipe['prompt']
    assert 'keeps burning' in recipe['prompt']
    assert 'no explosive fireball' in recipe['prompt']
    assert recipe['source_window'][0]==24
    rgb,_,_=element_graphs.graph('rgb',Path('guide.mp4'),recipe,width=320,height=192)
    alpha,_,_=element_graphs.graph('alpha',Path('rgb.mp4'),recipe,width=320,height=192)
    assert rgb['lora']['inputs']['lora_name']==element_graphs.DEFAULT_MODELS['fluid']
    assert alpha['lora']['inputs']['lora_name']==element_graphs.DEFAULT_MODELS['alpha']
    assert alpha['pos']['inputs']['text']=='' and alpha['neg']['inputs']['text']==''
    assert alpha['guide']['inputs']['latent_downscale_factor']==1.


def test_fire_post_controls_reuse_generation_but_source_changes_require_new_version(monkeypatch):
    p,e=shot();original=element_jobs.generation_signature(p,e)
    e['element'].update(opacity=.4,gain=1.3,light=.2,shadow=.1)
    assert element_jobs.generation_signature(p,e)==original
    monkeypatch.setitem(element_jobs.SIMULATION_PROFILES,'fire','future-groundfire')
    assert element_jobs.generation_signature(p,e)!=original


def test_fire_can_preroll_and_all_disabled_phases_fail_before_generation():
    p,e=shot()
    next(k for k in e['keys'] if k['phase']=='ignite')['frame']=-24
    plan=element_jobs.plan(p,e['id'])
    assert plan['onset_frame']==-24 and plan['start_frame']==0 and plan['start_time']==0
    for k in e['keys']:k['enabled']=False
    with pytest.raises(ValueError,match='Enable at least one effect phase'):
        element_jobs.plan(p,e['id'])
