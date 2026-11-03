"""The 24Hz model's padding must not lengthen an authored native-frame range."""
import pytest
from keyfx import element_jobs,geometry


def shot(start,duration):
    effect={'id':'fx','preset':'explosion','keys':[{'phase':'onset','frame':start,'x':.5,'y':.5}],
            'element':{'duration_s':duration}}
    return {'id':'native','frames':120,'settings':{},'geometry':geometry.defaults(),'effects':[effect],
            'native_profile':{'width':1920,'height':1080,'fps':60,'timestamps':[i/60 for i in range(120)],'duration':2}}


def test_native_end_is_independent_of_model_sampling_padding():
    plan=element_jobs.plan(shot(30,.1),'fx')
    assert plan['active_frames']==3  # The model needs .125s of samples.
    assert plan['end_time']==pytest.approx(.6)
    assert element_jobs.model_index(plan,29/60) is None
    assert element_jobs.model_index(plan,30/60)==0
    assert element_jobs.model_index(plan,35/60)==2
    assert element_jobs.model_index(plan,36/60) is None
    assert element_jobs.model_index(plan,37/60) is None


def test_tail_is_clipped_to_the_actual_shot_end():
    plan=element_jobs.plan(shot(117,5),'fx')
    assert plan['end_time']==2
    assert element_jobs.model_index(plan,119/60) is not None
    assert element_jobs.model_index(plan,2) is None


def test_older_frozen_takes_keep_their_recorded_model_interval():
    spec={'start_time':.5,'active_frames':3}
    assert element_jobs.model_index(spec,.6)==2
    assert element_jobs.model_index(spec,.625) is None


def test_minimum_model_sample_count_does_not_push_a_short_tail_past_clip_end():
    plan=element_jobs.plan(shot(119,5),'fx')
    assert plan['active_frames']==2
    assert plan['end_time']==2
    assert element_jobs.model_index(plan,119/60)==0
    assert element_jobs.model_index(plan,2) is None
