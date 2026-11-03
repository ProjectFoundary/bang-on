import copy
import cv2
import numpy as np
import pytest
from keyfx import element_finishing as finish, element_jobs, geometry, projects, lighting_integration, native_colour
from keyfx import alpha_core_review
from test_alpha_core_review import frozen
from test_element_pipeline import pipeline, png


def planes():
    guide=np.zeros((160,200),np.float32);guide[25:140,25:175]=.75
    generated=guide.copy();generated[55:105,65:135]=0
    generated[12:16,8:65]=.18
    rgb=np.full((160,200,3),.1,np.float32)
    visibility=np.ones(guide.shape,np.float32);visibility[50:110,90:100]=0
    return rgb,generated,guide,visibility


def test_dial_preserves_body_fills_no_generated_holes_and_exposes_new_wisps():
    rgb,generated,guide,visibility=planes()
    outputs=[]
    for amount in [0,.5,1]:
        c,a=finish.blend_layers(rgb,generated,guide,visibility,{'edge_blend':amount})
        assert a[80,80]==.75 and np.array_equal(c[80,80],rgb[80,80])
        assert a[80,95]==0 and not c[80,95].any(), 'Original occlusion was replaced'
        outputs.append(a)
    assert outputs[0][13,30]==0 and outputs[1][13,30]==pytest.approx(.09) and outputs[2][13,30]==pytest.approx(.18)
    assert np.array_equal(outputs[1],(outputs[0]+outputs[2])*.5)


def test_building_pixels_choose_original_result_instead_of_full_cutout():
    rgb,generated,guide,visibility=planes();protected=np.zeros(guide.shape,bool);protected[10:140,30:45]=True
    c,a=finish.blend_layers(rgb,generated,guide,visibility,{'edge_blend':1},protected)
    assert np.array_equal(a[protected],(guide*visibility)[protected])
    assert np.array_equal(c[protected],(np.minimum(rgb,guide[...,None])*visibility[...,None])[protected])


def test_visibility_blur_softens_holdout_edge_but_keeps_uniform_fields_neutral():
    vis=np.ones((60,100),np.float32);vis[:,50:]=0
    out=finish.soften_visibility(vis,2)
    assert 0<out[30,49]<1 and 0<out[30,50]<1
    assert out[30,10]==1 and out[30,90]==0
    assert np.array_equal(finish.soften_visibility(np.ones_like(vis),2),np.ones_like(vis))
    assert np.array_equal(finish.soften_visibility(vis,0),vis)


def test_glow_is_bounded_local_bloom_from_visible_highlights():
    rgb=np.zeros((100,200,3),np.float32);rgb[45:55,95:105]=[2,1,.3]
    assert finish.glow(rgb,0) is None
    bloom=finish.glow(rgb,.06)
    assert bloom[50,106,0]>0 and bloom.min()>=0
    assert bloom.max()<.12 and not bloom[:10].any()
    assert not finish.glow(np.full_like(rgb,.1),.06).any()


@pytest.mark.parametrize('cfg',[{'body_matte':1},{'edge_blend':True},{'edge_blend':-1},{'edge_blend':1.01},
    {'holdout_blur':13},{'holdout_blur':float('nan')},{'ltx_glow':.6},{'ltx_glow':'0.1'}])
def test_invalid_finishing_controls_are_rejected(cfg):
    with pytest.raises(ValueError):finish.policy(cfg)


def test_controls_only_invalidate_local_finish_and_receiver_lighting(monkeypatch):
    e={'id':'blast','preset':'explosion','keys':[{'phase':'onset','frame':0}],'element':{}}
    p={'id':'test','effects':[e],'frames':5,'renders':[],'geometry':geometry.defaults(),'settings':{'pipeline':'ltx_elements'}}
    generation=element_jobs.generation_signature(p,e);composite=projects.effect_signature(p)
    monkeypatch.setattr(lighting_integration,'_rows',lambda p:[(p['effects'][0],{'id':'take','generation_signature':generation})])
    light=lighting_integration.input_signature(p)
    for key,value in [('body_matte',True),('edge_blend',.5),('holdout_blur',2),('ltx_glow',.06)]:
        q=copy.deepcopy(p);q['effects'][0]['element'][key]=value
        assert element_jobs.generation_signature(q,q['effects'][0])==generation
        assert projects.effect_signature(q)!=composite and lighting_integration.input_signature(q)!=light


def test_hybrid_alpha_movie_uses_original_body_and_invalidates_with_edge_dial(frozen):
    root,row,take=frozen
    one=alpha_core_review.build(root,row,take,treatment={'body_matte':True,'edge_blend':0})
    two=alpha_core_review.build(root,row,take,treatment={'body_matte':True,'edge_blend':1})
    assert one['signature']!=two['signature']
    alpha_core_review.validate_movie(root,row,take,two['signature'],treatment={'body_matte':True,'edge_blend':1})
    missing=copy.deepcopy(row);missing['plan'].pop('alpha_core')
    with pytest.raises(ValueError,match='captured simulation alpha'):
        alpha_core_review.build(root,missing,take,treatment={'body_matte':True})


def test_actual_frame_layers_share_opacity_gain_holdout_and_post_glow(tmp_path,monkeypatch):
    monkeypatch.setattr(projects,'DATA',tmp_path)
    root=projects.pdir('test');root.mkdir(parents=True)
    for d in ['rgb','alpha','inputs/visibility','inputs/guide_alpha']:(root/d).mkdir(parents=True)
    rgb,generated,guide,visibility=planes()
    for directory,image in [('rgb',np.full((160,200,3),180,np.uint8)),('alpha',np.uint8(generated*255)),
                            ('inputs/visibility',np.uint8(visibility*255)),('inputs/guide_alpha',np.uint8(guide*255))]:
        cv2.imwrite(str(root/directory/'00000.png'),image)
    p={'id':'test','geometry':geometry.defaults()}
    e={'id':'fx','preset':'explosion','element':{'body_matte':True,'edge_blend':1,'opacity':.5,'gain':2,'ltx_glow':.06}}
    row={'inputs':'inputs','plan':{'start_time':0,'active_frames':1,'alpha_core':{}}}
    # Use a truthy real frozen core declaration as production captures do.
    from keyfx import alpha_core
    row['plan']['alpha_core']=alpha_core.policy()
    take={'geometry':{'content_rect':[0,0,200,160]},'assets':{
        'rgb':{'frames_dir':str(root/'rgb'),'colour_association':'black_premultiplied'},'alpha':{'frames_dir':str(root/'alpha')}}}
    class Masks:
        def pixels(self,f):return np.zeros((160,200),bool)
    class Optical:
        _body_masks=Masks()
        def visibility(self,f):return None
    profile={'width':200,'height':160,'timestamps':[0]}
    layer=element_jobs.element_frame_layers(p,e,row,take,profile,{'fx':Optical()},0)
    assert layer['alpha'][80,80]==pytest.approx(191/255*.5)
    assert layer['alpha'][13,30]==pytest.approx(45/255*.5)
    assert not layer['rgb'][80,95].any()
    assert layer['rgb'][80,80,0]==pytest.approx(float(native_colour.srgb_to_linear(np.array(180/255)))*2*.5)
    assert layer['light'] is not None


@pytest.mark.parametrize('kind',['explosion','smoke_plume','fire'])
def test_new_effects_capture_original_body_for_smoke_and_fire_too(pipeline,kind):
    create,_,_=pipeline
    p,root,_=create(fps='24/1')
    p=projects.add_effect(p['id'],kind,0,.5,.5)
    e=p['effects'][-1]
    assert e['element']['body_matte'] is True
    e['element']['duration_s']=.1;projects.save(p)
    spec=element_jobs.plan(p,e['id']);shape=(spec['height'],spec['width'])
    guides={k:png(np.full((*shape,3),160,np.uint8)) for k in range(spec['active_frames'])}
    scalar={k:png(np.full(shape,190,np.uint8)) for k in range(spec['active_frames'])}
    row=element_jobs.prepare_upload(p['id'],e['id'],{'signature':spec['signature']},guides,scalar,guide_alpha=scalar)
    assert row['plan']['alpha_core']
    assert (root/row['inputs']/'guide_alpha'/'00000.png').is_file()
