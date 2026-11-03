"""Versioned finishing-only edge detail, without changing approved v1 output."""
import cv2
import numpy as np
import pytest

from keyfx import alpha_core


def v1_policy():
    return {'profile':'eroded-guide-alpha-core-v1','erosion_pixels_at_540':6.,
            'blur_sigma_at_540':3.,'source':'simulated_data_alpha','combination':'maximum',
            'support':'bounded_by_original_guide',
            'generated_envelope':'small_close_then_fill_external_contours',
            'envelope_threshold':8/255,'envelope_close_pixels_at_540':6.}


def old_assisted(model, guide):
    scale=guide.shape[0]/540.;radius=max(1,round(6*scale))
    kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*radius+1,2*radius+1))
    eroded=cv2.erode(guide,kernel,borderType=cv2.BORDER_CONSTANT,borderValue=0)
    core=np.minimum(cv2.GaussianBlur(eroded.astype(np.float32)/255,(0,0),3*scale,
                                    borderType=cv2.BORDER_CONSTANT),guide.astype(np.float32)/255)
    env=cv2.morphologyEx(np.uint8(model>8/255)*255,cv2.MORPH_CLOSE,kernel,
                         borderType=cv2.BORDER_CONSTANT,borderValue=0)
    contours,_=cv2.findContours(env,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    filled=np.zeros_like(env);cv2.drawContours(filled,contours,-1,255,thickness=cv2.FILLED)
    gate=np.minimum(cv2.GaussianBlur(filled.astype(np.float32)/255,(0,0),3*scale,
                                    borderType=cv2.BORDER_CONSTANT),filled.astype(np.float32)/255)
    return np.maximum(model.astype(np.float32,copy=False),core*gate)


def test_frozen_v1_replays_bit_exactly_after_default_v2_change():
    rng=np.random.default_rng(42)
    guide=np.zeros((180,320),np.uint8);guide[30:150,60:250]=rng.integers(100,256,(120,190),dtype=np.uint8)
    model=guide.astype(np.float32)/255*.3;model[60:120,100:200]=0
    expected=old_assisted(model,guide)
    assert np.array_equal(alpha_core.assisted_alpha(model,guide,frozen_policy=v1_policy()),expected)
    assert alpha_core.policy()['profile']==alpha_core.V2_PROFILE


def test_v2_noise_only_attenuates_support_transition_and_keeps_dense_center():
    guide=np.zeros((540,600),np.uint8);guide[70:470,80:520]=255
    out=alpha_core.core_alpha(guide)
    kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(13,13))
    smooth=cv2.GaussianBlur(cv2.erode(guide,kernel,borderType=cv2.BORDER_CONSTANT,borderValue=0).astype(np.float32)/255,
                           (0,0),6,borderType=cv2.BORDER_CONSTANT)
    smooth=np.minimum(smooth,guide.astype(np.float32)/255)
    assert np.all(out<=smooth) and np.any(out<smooth-.02)
    assert np.all(out[120:420,130:470]==1)
    assert not np.any(out[guide==0])
    assert np.array_equal(alpha_core.core_alpha(guide),out), 'No per-frame random flicker'


def test_noise_translates_with_guide_mass_instead_of_sticking_to_the_viewer():
    guide=np.zeros((360,540),np.uint8);guide[80:220,120:350]=255
    moved=np.zeros_like(guide);moved[103:243,137:367]=255
    a=alpha_core.core_alpha(guide);b=alpha_core.core_alpha(moved)
    assert np.array_equal(a[50:250,90:380],b[73:273,107:397])


def test_v2_keeps_model_wisps_and_never_fills_absent_or_displaced_element():
    guide=np.zeros((180,320),np.uint8);guide[30:150,60:250]=255
    model=guide.astype(np.float32)/255*.15;model[5:10,5:35]=.4
    out=alpha_core.assisted_alpha(model,guide)
    assert np.array_equal(out[guide==0],model[guide==0])
    assert np.all(out>=model) and out[90,160]>.99
    assert not alpha_core.assisted_alpha(np.zeros_like(model),guide).any()
    moved=np.zeros_like(model);moved[5:20,5:20]=.5
    assert np.array_equal(alpha_core.assisted_alpha(moved,guide),moved)


def test_both_policy_versions_are_frozen_not_arbitrary_edge_settings():
    alpha_core.validate_policy(v1_policy());alpha_core.validate_policy(alpha_core.policy())
    changed=alpha_core.policy();changed['edge_breakup_seed']+=1
    with pytest.raises(ValueError,match='frozen'):alpha_core.validate_policy(changed)


def test_v2_does_not_invent_a_transparent_edge_where_smoke_crosses_the_camera_border():
    guide=np.ones((108,192),np.uint8)*255
    model=np.ones(guide.shape,np.float32)*.12
    result=alpha_core.assisted_alpha(model,guide)
    assert np.all(result>.999)
    old=alpha_core.assisted_alpha(model,guide,frozen_policy=v1_policy())
    assert old[0,0]<result[0,0]
