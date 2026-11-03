import copy
import cv2
import numpy as np
import pytest

from keyfx import matte_treatment, element_jobs, geometry, projects, lighting_integration
from test_alpha_core_review import frozen
from keyfx import alpha_core_review


def sample():
    a=np.zeros((160,200),np.float32)
    a[20:140,25:175]=.12
    a[10:14,10:45]=.08  # Fine isolated wisp must survive exactly.
    return a


def test_default_saturates_core_and_preserves_thin_wisps_and_background():
    a=sample();out=matte_treatment.apply(a)
    assert out[80,100]==1 and np.all(out>=a)
    assert np.array_equal(out[10:14,10:45],a[10:14,10:45])
    assert np.array_equal(out[:25],a[:25])
    assert np.array_equal(matte_treatment.apply(a,{'matte_enabled':False}),a)


def test_blur_softens_inset_and_more_erosion_retreats_the_reinforcement():
    a=sample()
    hard=matte_treatment.apply(a,{'matte_erode':12,'matte_blur':0})
    soft=matte_treatment.apply(a)
    inset=matte_treatment.apply(a,{'matte_erode':24})
    assert np.any((soft>.12)&(soft<.99)&(hard==1))
    assert np.all(inset<=soft+1e-6) and np.any(inset<soft-.1)
    assert inset[80,100]==1


@pytest.mark.parametrize('settings',[{'matte_multiply':np.nan},{'matte_erode':-1},{'matte_erode':1.5},
    {'matte_blur':25},{'matte_enabled':1},{'matte_multiply':True},{'matte_blur':'3'}])
def test_invalid_controls_rejected(settings):
    with pytest.raises(ValueError,match='[Mm]atte'):matte_treatment.policy(settings)


def test_controls_invalidate_finish_and_lighting_but_keep_paid_generation_current(monkeypatch):
    e={'id':'blast','preset':'explosion','keys':[{'phase':'onset','frame':0}],'element':{}}
    p={'id':'test','effects':[e],'frames':5,'renders':[],'geometry':geometry.defaults(),'settings':{'pipeline':'ltx_elements'}}
    generation=element_jobs.generation_signature(p,e);finish=projects.effect_signature(p)
    row={'id':'take','generation_signature':generation}
    monkeypatch.setattr(lighting_integration,'_rows',lambda project:[(project['effects'][0],row)])
    light=lighting_integration.input_signature(p)
    for key,value in [('matte_multiply',32),('matte_erode',24),('matte_blur',6),('matte_enabled',False)]:
        q=copy.deepcopy(p);q['effects'][0]['element'][key]=value
        assert element_jobs.generation_signature(q,q['effects'][0])==generation
        assert projects.effect_signature(q)!=finish
        assert lighting_integration.input_signature(q)!=light


def test_treated_review_supports_legacy_without_simulation_alpha_and_changes_cache(frozen):
    root,row,take=frozen
    row['plan'].pop('alpha_core')
    first=alpha_core_review.build(root,row,take,treatment={})
    assert first['frames']==5 and first['policy']['matte_multiply']==16
    second=alpha_core_review.build(root,row,take,treatment={'matte_erode':0,'matte_blur':0})
    assert first['signature']!=second['signature']
    with pytest.raises(ValueError,match='different inputs'):
        alpha_core_review.validate_movie(root,row,take,first['signature'],treatment={'matte_erode':0})


def test_active_core_respects_visibility_opacity_and_export_with_rgb_unchanged(tmp_path,monkeypatch):
    from keyfx import layer_exports
    monkeypatch.setattr(projects,'DATA',tmp_path)
    root=projects.pdir('test');root.mkdir(parents=True)
    for directory in ['rgb','alpha','inputs/visibility']:(root/directory).mkdir(parents=True)
    alpha=np.rint(sample()*255).astype(np.uint8)
    rgb=np.full((*alpha.shape,3),(10,20,30),np.uint8)
    visibility=np.full(alpha.shape,255,np.uint8);visibility[60:90,80:110]=128
    for directory,image in [('rgb',rgb),('alpha',alpha),('inputs/visibility',visibility)]:
        assert cv2.imwrite(str(root/directory/'00000.png'),image)
    p={'id':'test','geometry':geometry.defaults()}
    effect={'id':'fx','preset':'explosion','element':{'opacity':.5}}
    row={'inputs':'inputs','plan':{'start_time':0,'active_frames':1}}
    take={'geometry':{'content_rect':[0,0,200,160]},'assets':{
        'rgb':{'frames_dir':str(root/'rgb'),'colour_association':'black_premultiplied'},
        'alpha':{'frames_dir':str(root/'alpha')}}}
    post=np.ones(alpha.shape,np.float32);post[60:90,120:140]=0
    class Optical:
        def visibility(self,frame):return post
    profile={'timestamps':[0],'width':200,'height':160}
    active=element_jobs.element_frame_layers(p,effect,row,take,profile,{'fx':Optical()},0)
    assert active['alpha'][80,60]==.5, 'Opaque core must receive user opacity once'
    assert active['alpha'][80,100]==pytest.approx(128/255*.5), 'Geometric visibility must apply after core treatment'
    assert active['alpha'][80,130]==0 and not active['rgb'][80,130].any()
    raw_effect=copy.deepcopy(effect);raw_effect['element']['matte_enabled']=False
    raw=element_jobs.element_frame_layers(p,raw_effect,row,take,profile,{'fx':Optical()},0)
    assert raw['alpha'][80,60]<.07 and np.array_equal(raw['rgb'],active['rgb'])
    target=tmp_path/'layer.exr'
    layer_exports._write_exr(target,np.dstack([active['rgb'],active['alpha']]))
    decoded=cv2.imread(str(target),cv2.IMREAD_UNCHANGED)
    assert np.array_equal(decoded[...,3],active['alpha'])
