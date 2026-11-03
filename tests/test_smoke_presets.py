"""Distinct authored plume/grenade presets stay usable through the element route."""
from pathlib import Path

import pytest

from keyfx import element_graphs, element_jobs, geometry, presets, projects


@pytest.mark.parametrize('kind,duration', [('smoke_plume_hq',5.),('smoke_grenade',3.)])
def test_new_smoke_preset_has_assets_native_timing_and_separate_model_stages(tmp_path,monkeypatch,kind,duration):
    monkeypatch.setattr(projects,'DATA',tmp_path)
    p={'id':'smoke-test','name':'Smoke fixture','mode':'plate','frames':144,'renders':[],'effects':[],
       'geometry':geometry.defaults(),'settings':{'pipeline':'ltx_elements'},'element_takes':[],
       'native_profile':{'width':320,'height':180,'fps':24.,'fps_rational':'24/1',
                         'timestamps':[i/24 for i in range(144)],'duration':6.,'source_sha256':'fixture'}}
    root=projects.pdir(p['id']);root.mkdir(parents=True);projects.save(p)
    p=projects.add_effect(p['id'],kind,12,.4,.6)
    effect=p['effects'][0];definition=presets.get(kind)
    assert definition['status']=='simulation','A port is not a live model-quality proof'
    assert definition['phases'][-1]['persist']
    assert all((Path(definition['dir'])/phase['stamp']).is_file() for phase in definition['phases'] if phase.get('stamp'))
    assert all((Path(definition['dir'])/look['file']).is_file() for look in definition['looks'])
    assert effect['element']['duration_s']==duration
    plan=element_jobs.plan(p,effect['id'])
    expected_start = 0 if kind == 'smoke_plume_hq' else 12
    assert plan['start_frame']==expected_start and plan['start_time']==expected_start/24
    assert plan['onset_frame']==(-200 if kind == 'smoke_plume_hq' else 12)
    assert plan['active_frames']==duration*24
    recipe=element_jobs._recipe(p,effect,plan)
    assert recipe['source_window'][0]==expected_start and recipe['colour_association']=='black_premultiplied'
    assert ('neutral grey' if kind=='smoke_plume_hq' else 'no flame or fireball') in recipe['prompt']
    rgb,_,_=element_graphs.graph('rgb',Path('guide.mp4'),recipe,width=320,height=192)
    alpha,_,_=element_graphs.graph('alpha',Path('rgb.mp4'),recipe,width=320,height=192)
    assert rgb['lora']['inputs']['lora_name']==element_graphs.DEFAULT_MODELS['fluid']
    assert alpha['lora']['inputs']['lora_name']==element_graphs.DEFAULT_MODELS['alpha']
    assert alpha['pos']['inputs']['text']==''
    signature=element_jobs.generation_signature(p,effect)
    effect['element'].update(opacity=.4,light=.3,shadow=.2,gain=1.2)
    assert element_jobs.generation_signature(p,effect)==signature
    effect['element']['seed']+=1
    assert element_jobs.generation_signature(p,effect)!=signature


def test_legacy_smoke_ids_keep_their_existing_definitions_and_prompts():
    assert presets.get('smoke_white')['name']=='White smoke (grenade)'
    assert presets.get('smoke_plume')['name']=='Smoke plume (black)'
    assert 'smoke_white' in element_jobs.KINDS and 'smoke_plume' in element_jobs.KINDS
    assert presets.get('smoke_grenade')['id']!=presets.get('smoke_plume_hq')['id']


def test_new_simulation_profile_revision_invalidates_only_the_new_preset(monkeypatch):
    p={'frames':144,'geometry':geometry.defaults(),'settings':{'pipeline':'ltx_elements'}}
    hq=presets.new_effect('smoke_plume_hq',0,.4,.6,'hq',144)
    legacy=presets.new_effect('smoke_white',0,.4,.6,'legacy',144)
    before_hq=element_jobs.generation_signature(p,hq)
    before_legacy=element_jobs.generation_signature(p,legacy)
    monkeypatch.setitem(element_jobs.SIMULATION_PROFILES,'smoke_plume_hq','future-authoring-revision')
    assert element_jobs.generation_signature(p,hq)!=before_hq
    assert element_jobs.generation_signature(p,legacy)==before_legacy


@pytest.mark.parametrize('kind',['smoke_white','smoke_plume','smoke_plume_hq','smoke_grenade'])
def test_smoke_appearance_is_neutral_at_one_and_versions_only_its_guide(kind):
    p={'frames':144,'geometry':geometry.defaults(),'settings':{'pipeline':'ltx_elements'}}
    effect=presets.new_effect(kind,0,.4,.6,'smoke',144)
    other=presets.new_effect('explosion',0,.7,.6,'explosion',144)
    before=element_jobs.generation_signature(p,effect);other_signature=element_jobs.generation_signature(p,other)
    effect['element']={'density':1,'brightness':1}
    assert element_jobs.generation_signature(p,effect)==before
    for key,value in [('density',.3),('brightness',2)]:
        effect['element']={key:value}
        assert element_jobs.generation_signature(p,effect)!=before
        assert element_jobs.generation_signature(p,other)==other_signature
        assert element_jobs.effect_config(p,effect)[key]==value
    other['element']={'density':0,'brightness':0}
    assert element_jobs.generation_signature(p,other)==other_signature, 'Smoke controls must not affect fire/explosions'


@pytest.mark.parametrize('kind',['smoke_white','smoke_grenade','smoke_plume','smoke_plume_hq','explosion','fire'])
def test_grenade_dispersion_is_explicit_and_versions_only_its_guide(kind):
    p={'frames':144,'geometry':geometry.defaults(),'settings':{'pipeline':'ltx_elements'}}
    effect=presets.new_effect(kind,0,.4,.6,'effect',144)
    before=element_jobs.generation_signature(p,effect)
    assert element_jobs.effect_config(p,effect)['smoke_dispersion']==0
    effect['element']={'wind_dispersion':1,'smoke_dispersion':0}
    assert element_jobs.generation_signature(p,effect)==before
    effect['element']['smoke_dispersion']=1.5
    changed=element_jobs.generation_signature(p,effect)!=before
    assert changed==(kind in ('smoke_grenade','smoke_white'))
