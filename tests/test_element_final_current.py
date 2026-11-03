"""Final/composite freshness with offline native fixtures and cached FX only."""
import copy
import hashlib
from pathlib import Path

import pytest

from keyfx import element_jobs, elements, geometry, presets, projects
from test_element_pipeline import generate, pipeline
from test_element_review import _render_smoke


def _two_ready(p,monkeypatch):
    blast,blast_calls=generate(p,monkeypatch);element_jobs.approve(p['id'],blast['id']);p=projects.load(p['id'])
    smoke=presets.new_effect('smoke_grenade',2,.3,.6,'smoke',p['frames'])
    smoke['element']={**element_jobs.DEFAULT,'duration_s':.1,'blur':0,'seed':79}
    p['effects'].append(smoke);p['flash_base']='plate';projects.save(p)
    smoke_calls=_render_smoke(p,monkeypatch);p=projects.load(p['id'])
    smoke_row=element_jobs.selected_take(p,p['effects'][1]);element_jobs.approve(p['id'],smoke_row['id'])
    monkeypatch.setattr(elements,'run',lambda *a,**kw:pytest.fail('A cached test tried new inference'))
    return blast,smoke_row,blast_calls,smoke_calls


def _model_hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'elements').rglob('*') if p.is_file()}


@pytest.mark.parametrize('control,value',[('opacity',.4),('gain',1.5),('light',.4),('shadow',.3)])
def test_cached_post_rebuild_stales_old_final_and_preserves_model_pins(pipeline,monkeypatch,control,value):
    create,exports,_=pipeline;p,root,_=create(fps='24/1');blast,smoke,blast_calls,smoke_calls=_two_ready(p,monkeypatch)
    first=element_jobs.composite('shot');p=projects.load('shot');p['flash_base']='render:'+first['id'];projects.save(p)
    old_final=projects.export_final('shot');p=projects.load('shot');pins=copy.deepcopy(p['element_approvals']);frozen=_model_hashes(root)
    assert projects.render_current(p,old_final)
    p['effects'][0]['element'][control]=value;projects.save(p)
    assert p['element_approvals']==pins and all(r['current'] and r['approved'] for r in element_jobs.records(p))
    assert not projects.render_current(p,first) and not projects.render_current(p,old_final)
    assert projects.flash_base(p)=='plate','The persisted old flash base must not keep stale pixels current'
    rebuilt=element_jobs.composite('shot');p=projects.load('shot')
    public=projects.public(p);rows={r['id']:r for r in public['renders']}
    assert rows[rebuilt['id']]['source_current'] and not rows[old_final['id']]['source_current']
    assert not any(r['kind']=='final' and r['status']=='done' and r['source_current'] for r in public['renders'])
    assert next(r for r in public['renders'] if r['kind']=='effect' and r['status']=='done' and r['source_current'])['id']==rebuilt['id']
    assert projects.flash_base(p)=='render:'+rebuilt['id']
    new_final=projects.export_final('shot');p=projects.load('shot')
    assert projects.render_current(p,new_final) and not projects.render_current(p,old_final)
    assert p['element_approvals']==pins and _model_hashes(root)==frozen
    assert blast_calls==['rgb','alpha'] and smoke_calls==['rgb','alpha']


def test_seed_restore_recovers_exact_pin_and_cache_without_new_inference(pipeline,monkeypatch):
    create,_,_=pipeline;p,root,_=create(fps='24/1');blast,smoke,_,_=_two_ready(p,monkeypatch)
    p=projects.load('shot');pins=copy.deepcopy(p['element_approvals']);frozen=_model_hashes(root)
    original=p['effects'][1]['element']['seed'];p['effects'][1]['element']['seed']=original+1;projects.save(p)
    rows={r['effect_id']:r for r in element_jobs.records(p)}
    assert rows['blast']['approved'] and rows['blast']['current']
    assert rows['smoke']['approval_outdated'] and not rows['smoke']['approved'] and not rows['smoke']['current']
    assert p['element_approvals']==pins and element_jobs.selected_take(p,p['effects'][0])['id']==blast['id']
    with pytest.raises(ValueError,match='approved element changed'):element_jobs.selected_take(p,p['effects'][1])
    p['effects'][1]['element']['seed']=original;projects.save(p)
    assert all(r['approved'] and r['current'] for r in element_jobs.records(p))
    assert element_jobs.selected_take(p,p['effects'][1])['id']==smoke['id']
    assert p['element_approvals']==pins and _model_hashes(root)==frozen
    element_jobs.composite('shot');assert projects.load('shot')['renders'][0]['status']=='done'
    assert _model_hashes(root)==frozen


def test_ltx_cannot_treat_signatureless_archived_effect_or_its_final_as_current():
    archived={'id':'old-h3','kind':'effect','status':'done','created':'2026-01-01 00:00:00','base':'plate'}
    p={'id':'offline','mode':'plate','frames':8,'effects':[presets.new_effect('smoke_grenade',0,.5,.5,'smoke',8)],
       'settings':{'pipeline':'ltx_elements'},'geometry':geometry.defaults(),'renders':[archived],
       'flashes':[],'flash_base':'render:old-h3'}
    final={'id':'old-final','kind':'final','status':'done','base':'render:old-h3','flash_signature':projects.flash_signature(p)}
    p['renders'].insert(0,final)
    assert not projects.render_current(p,archived)
    assert not projects.render_current(p,final)
    # Existing unversioned H3 archives keep their previous compatibility path.
    p['settings']['pipeline']='h3'
    assert projects.render_current(p,archived) and projects.render_current(p,final)


def test_clean_plate_final_is_stale_after_adding_an_unrendered_ltx_element():
    p={'id':'offline','mode':'plate','frames':8,'effects':[],'settings':{'pipeline':'ltx_elements'},
       'geometry':geometry.defaults(),'renders':[],'flashes':[],'flash_base':'plate'}
    final={'id':'clean-final','kind':'final','status':'done','base':'plate','flash_signature':projects.flash_signature(p)}
    p['renders'].append(final);assert projects.render_current(p,final)
    p['effects'].append(presets.new_effect('smoke_grenade',0,.5,.5,'smoke',8))
    assert not projects.render_current(p,final)
    p['effects'].clear();assert projects.render_current(p,final)
