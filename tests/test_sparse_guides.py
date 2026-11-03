"""Natural-motion defaults keep image targets sparse, including in legacy shots."""
import copy
import json

import numpy as np
import pytest

from keyfx import graphs, keys, polish, presets, projects


def explosion(onset=12, frames=48, eid='blast'):
    return presets.new_effect('explosion', onset, .15, .6, eid, frames)


def test_default_migrates_future_settings_without_rewriting_saved_recipe():
    p={'settings':{'strength':'guide','seed':42,'polish':True}}
    original=copy.deepcopy(p)
    assert projects.effect_settings(p)=={'guide_mode':'sparse','strength':'exact','seed':42,'polish':True}
    assert p==original
    assert projects.effect_settings({'settings':{'guide_mode':'full','strength':'firm'}})['strength']=='firm'
    with pytest.raises(ValueError):projects.effect_settings({'settings':{'guide_mode':'unknown'}})


def test_sparse_guides_keep_before_onset_peak_and_leave_preview_timeline_intact():
    e=explosion();original=copy.deepcopy(e)
    assert [f for f,_ in keys.guide_frames([e],48)]==[10,12,18]
    assert [f for f,_ in keys.guide_frames([e],48,'full')]==[10,12,18,24,33,47]
    assert keys.state_at(e,47)['phase']=='aftermath'
    assert e==original
    # One sparse set per explosion, not the first three frames of the whole shot.
    assert [f for f,_ in keys.guide_frames([e,explosion(32,48,'second')],48)]==[10,12,18,30,32,38]
    assert [f for f,_ in keys.guide_frames([explosion(47)],48)]==[45,47]
    assert keys.guide_frames([explosion(-30)],48)==[(0,False)]


def test_sparse_selection_does_not_remove_other_event_guides():
    flash=presets.new_effect('muzzle_side',25,.4,.4,'flash',48)
    result=dict(keys.guide_frames([explosion(),flash],48))
    assert result[25] is True
    assert {f for f,_ in keys.key_frames([flash],48)}<=result.keys()


def test_sparse_polish_does_not_pay_for_late_or_hold_frames(tmp_path,monkeypatch):
    e=explosion();seen=[]
    monkeypatch.setattr(polish,'read_frame',lambda *_:np.full((540,960,3),70,np.float32))
    def edit(image,mask,prompt,seed,cache,model):
        seen.append(seed-42);return image
    monkeypatch.setattr(polish,'polish_frame',edit)
    result=polish.polish_keys(tmp_path,[e],tmp_path/'keys',42,tmp_path/'cache',48,model='nano',guide_mode='sparse')
    assert [f for f,_,_ in result]==[10,12,18]
    assert sorted(seen)==[12,18]  # clean pre-blast frame requires no paid image edit
    assert all(path.is_file() for _,path,_ in result)


def test_app_submission_freezes_sparse_all_step_recipe(tmp_path,monkeypatch):
    monkeypatch.setattr(projects,'DATA',tmp_path)
    monkeypatch.setattr(projects.media,'read_frame',lambda *_args,**_kwargs:np.full((540,960,3),70,np.float32))
    monkeypatch.setattr(keys,'read_frame',lambda *_args,**_kwargs:np.full((540,960,3),70,np.float32))
    p={'id':'sparse','mode':'plate','frames':48,'source':{},'shot':'A person aims.','renders':[],'flashes':[],
       'effects':[explosion()],'settings':{'strength':'guide','seed':42,'polish':False}}
    projects.pdir('sparse').mkdir(parents=True);projects.save(p)
    r=projects.start_render('sparse','effect',_background=False)
    assert r['guide_mode']=='sparse' and r['strength']=='exact'
    rd=projects.pdir('sparse')/'renders'/r['id']
    frozen=json.loads((rd/'request.json').read_text())
    assert frozen['render']['guide_mode']=='sparse'
    assert any(k['phase']=='aftermath' for k in frozen['project']['effects'][0]['keys'])
    # Both the server-only and browser-layer routes must use the same sparse list.
    kl=projects._build_keys(p,r,rd)
    assert [f for f,_,_ in kl]==[10,12,18]
    (rd/'layers').mkdir()
    assert [f for f,_,_ in projects._build_keys(p,r,rd)]==[10,12,18]
    g,_=graphs.effect_graph('test',[],tmp_path/'source.mp4',tmp_path/'anchor.png',kl,r['strength'],42,n_frames=48)
    assert not any(v['class_type']=='ConditioningSetTimestepRange' for v in g.values())
    assert sorted(v['inputs']['frame_idx'] for v in g.values() if v['class_type']=='MiniMaxH3AddGuide')==[0,10,12,18]
    assert 'only' in projects.prompt_preview(p) and 'later timing descriptions are not additional image anchors' in projects.prompt_preview(p)


def test_guidance_mode_changes_invalidate_effect_cache():
    p={'shot':'A person aims.','effects':[explosion()],'settings':{'seed':42}}
    before=projects.effect_signature(p)
    p['settings'].update(guide_mode='full',strength='exact')
    assert projects.effect_signature(p)!=before
