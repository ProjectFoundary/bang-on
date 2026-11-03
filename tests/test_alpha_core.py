"""Local data-alpha assistance; no video providers or production shot writes."""
import numpy as np
import pytest

from keyfx import alpha_core


@pytest.mark.parametrize('shape', [(1, 8), (2, 2), (16, 16)])
def test_opaque_masks_without_distance_seed_do_not_overflow(shape):
    guide=np.full(shape,255,np.uint8)
    with np.errstate(over='raise', invalid='raise', divide='raise'):
        result=alpha_core.core_alpha(guide)
    assert np.isfinite(result).all()
    assert np.all((result>=0)&(result<=1))


def test_eroded_blurred_core_recovers_dense_interior_without_hard_outline():
    guide = np.zeros((540, 600), np.uint8)
    guide[100:440, 100:500] = 255
    core = alpha_core.core_alpha(guide)
    assert core.dtype == np.float32 and core.shape == guide.shape
    assert core[270, 300] == pytest.approx(1)
    assert 0 < core[100, 300] < core[110, 300] < 1
    assert np.all(core[guide == 0] == 0), 'Blur expanded beyond the input silhouette'
    assert np.any((core > 0) & (core < 1)), 'Core edge was hardened'


def test_assistance_preserves_model_wisps_and_original_guide_holes():
    guide = np.zeros((540, 600), np.uint8)
    guide[100:440, 100:500] = 255
    guide[240:300, 270:330] = 0
    model = np.full(guide.shape, .05, np.float32)
    model[40:70, 20:40] = .4  # new LTX wisps outside the simulated silhouette
    assisted = alpha_core.assisted_alpha(model, guide)
    assert np.array_equal(assisted[40:70, 20:40], model[40:70, 20:40])
    assert np.array_equal(assisted[guide == 0], model[guide == 0])
    assert assisted[180, 180] > .99
    assert np.all(assisted >= model)
    # Neither fire RGB nor smoke brightness is accepted by this API.
    with pytest.raises(ValueError, match='data-alpha'):
        alpha_core.core_alpha(np.repeat(guide[..., None], 3, axis=2))


def test_thin_and_translucent_guide_details_do_not_become_opaque():
    guide = np.zeros((540, 600), np.uint8)
    guide[100:440, 100:500] = 128
    guide[40:44, 40:400] = 255  # thinner than erosion diameter
    core = alpha_core.core_alpha(guide)
    assert core[270, 300] == pytest.approx(128/255)
    assert np.max(core[35:50]) == 0
    assert np.all(core <= guide.astype(np.float32)/255)


def test_each_frame_uses_its_own_motion_and_empty_alpha_stays_empty():
    before = np.zeros((108, 192), np.uint8)
    after = before.copy()
    before[20:80, 20:70] = 255
    after[20:80, 100:150] = 255
    model = np.zeros(before.shape, np.float32)
    a = alpha_core.assisted_alpha(before.astype(np.float32)/255*.12, before)
    b = alpha_core.assisted_alpha(after.astype(np.float32)/255*.12, after)
    assert a[50, 45] > .99 and b[50, 45] == 0
    assert b[50, 125] > .99 and a[50, 125] == 0
    assert not np.any(alpha_core.assisted_alpha(model, model.astype(np.uint8)))


def test_hollow_model_core_recovers_but_displaced_and_absent_elements_do_not_ghost():
    guide = np.zeros((540, 600), np.uint8)
    guide[100:440, 100:500] = 255
    model = guide.astype(np.float32)/255*.5
    model[120:420, 120:480] = 0  # closed visible outer shell, missing dense core
    assisted = alpha_core.assisted_alpha(model, guide)
    assert assisted[270, 300] > .99
    moved = np.zeros_like(model)
    moved[20:70, 20:70] = .5
    assert np.array_equal(alpha_core.assisted_alpha(moved, guide), moved)
    assert not np.any(alpha_core.assisted_alpha(np.zeros_like(model), guide))


def test_frozen_policy_rejects_silent_processing_changes():
    guide = np.ones((16,16),np.uint8)*255
    model = np.ones(guide.shape,np.float32)*.5
    changed = alpha_core.policy()
    changed['erosion_pixels_at_540'] = 0
    with pytest.raises(ValueError,match='frozen'):
        alpha_core.assisted_alpha(model,guide,frozen_policy=changed)
    alpha_core.assisted_alpha(model,guide,frozen_policy=alpha_core.policy())


@pytest.mark.parametrize('model', [np.ones((10,10),np.uint8),
                                    np.full((10,10),np.nan),
                                    np.full((10,10),1.01),
                                    np.full((10,10),-.01),
                                    np.zeros((10,11),np.float32)])
def test_invalid_model_data_is_rejected(model):
    with pytest.raises(ValueError, match='model alpha'):
        alpha_core.assisted_alpha(model, np.zeros((10,10),np.uint8))


def test_policy_is_explicit_and_returns_a_fresh_record():
    one = alpha_core.policy()
    one['profile'] = 'changed'
    assert alpha_core.policy()['profile'] == alpha_core.PROFILE
    assert alpha_core.policy()['source'] == 'simulated_data_alpha'
