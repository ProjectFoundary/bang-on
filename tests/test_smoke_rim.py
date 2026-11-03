"""Rim lighting preserves flow/opacity and survives guide blending/displacement."""
import copy
import cv2
import numpy as np
import pytest
from keyfx import element_jobs, geometry, painted_guides, presets, projects, elements, connections


def config(**extra):
    return {**element_jobs.DEFAULT,'rim_strength':1,'rim_width':32,'light_rotation':105,'light_elevation':-10,**extra}


def test_neutral_controls_preserve_guide_versions_and_disabled_width_is_ignored():
    p={'frames':96,'geometry':geometry.defaults(),'settings':{}}
    e=presets.new_effect('smoke_plume_hq',0,.5,.5,'smoke',96)
    before=element_jobs.generation_signature(p,e)
    e['element']={'fill_intensity':1,'rim_strength':0,'rim_width':120}
    assert element_jobs.generation_signature(p,e)==before
    for key,value in [('fill_intensity',0),('rim_strength',1),('light_elevation',-10)]:
        candidate=copy.deepcopy(e);candidate['element'][key]=value
        assert element_jobs.generation_signature(p,candidate)!=before
    e['element'].update(guide_style='rendered',rim_strength=0)
    rendered=element_jobs.generation_signature(p,e);e['element']['rim_strength']=1
    assert element_jobs.generation_signature(p,e)==rendered


@pytest.mark.parametrize('key,value',[('fill_intensity',-.1),('fill_intensity',2.1),('fill_intensity',True),
    ('rim_width',0),('rim_width',129),('rim_strength',1.1),('rim_strength',float('nan')),('light_elevation',-91)])
def test_invalid_rim_lighting_is_rejected(key,value):
    with pytest.raises(ValueError):element_jobs.effect_config({'geometry':{}},{'element':{key:value}})


def test_rim_follows_light_and_preserves_dark_body_support():
    mask=np.zeros((144,256),np.uint8);mask[20:125,60:195]=255
    source=np.repeat((mask//2)[...,None],3,axis=2);original=source.copy()
    cfg=config();left=painted_guides.paint(source,cfg,mask)
    assert np.array_equal(left.max(axis=2)>0,mask>0)
    assert left[40:100,60:70].max()>=150 and left[40:100,180:195].max()<=65
    assert np.all(left[50:100,95:160]==65)
    right=painted_guides.paint(source,config(light_rotation=-75),mask)
    assert right[40:100,180:195].max()>=150 and right[40:100,60:70].max()<=65
    wide=painted_guides.paint(source,config(rim_width=64),mask)
    assert np.count_nonzero(wide[...,0]>65)>np.count_nonzero(left[...,0]>65)
    normal=painted_guides.paint(source,config(rim_strength=0),mask)
    partial=painted_guides.paint(source,config(rim_strength=.5),mask)
    assert np.mean(abs(partial.astype(float)-normal))<np.mean(abs(left.astype(float)-normal))
    assert np.array_equal(source,original)


def test_final_cap_prevents_blend_and_displacement_from_filling_the_core():
    image=np.zeros((144,256,3),np.uint8);image[20:125,60:195]=240
    capped=painted_guides.finish(image,config())
    assert np.array_equal(capped.max(axis=2)>0,image.max(axis=2)>0)
    assert np.all(capped[50:100,95:160]==65)
    assert capped[:,60:70].max()>150
    assert np.array_equal(painted_guides.finish(image,config(rim_strength=0)),image)
    assert np.array_equal(painted_guides.finish(image,config(guide_style='rendered')),image)
    assert 'screen-left' in painted_guides.lighting_prompt(config(fill_intensity=.03))
    assert 'back-left key light' in painted_guides.lighting_prompt(config(fill_intensity=.03))
    assert 'very low fill' in painted_guides.lighting_prompt(config(fill_intensity=.03))


def test_prepare_applies_rim_cap_after_displacement_and_before_sparse_sampling(tmp_path,monkeypatch):
    monkeypatch.setattr(projects,'DATA',tmp_path)
    monkeypatch.setattr(connections,'current',lambda:None)
    captured=[]
    def prepare(*args,**kwargs):
        captured.append((args,kwargs))
        return {'id':'rim-test','status':'prepared','take_dir':str(tmp_path/'take'),'request_sha256':'offline'}
    monkeypatch.setattr(elements,'prepare',prepare)
    class Displace:
        def __init__(self,*_):pass
        def apply(self,image,frame):
            image=image.copy();image[35:65,45:85]=255;return image
    monkeypatch.setattr(painted_guides,'Displacement',Displace)
    e=presets.new_effect('smoke_plume_hq',0,.5,.5,'smoke',24)
    e['element']=config(duration_s=.2,cadence=2,frame_blend=3,fill_intensity=.03)
    p={'id':'rim','name':'Rim fixture','mode':'plate','frames':24,'effects':[e],'renders':[],
       'geometry':geometry.defaults(),'settings':{'pipeline':'ltx_elements'},'element_takes':[],
       'native_profile':{'width':128,'height':96,'fps':24,'fps_rational':'24/1','timestamps':[i/24 for i in range(24)],'duration':1}}
    projects.pdir('rim').mkdir(parents=True);projects.save(p);spec=element_jobs.plan(p,'smoke')
    mask=np.zeros((96,128),np.uint8);mask[15:85,25:105]=255
    image=np.repeat((mask//2)[...,None],3,axis=2)
    def png(im):return cv2.imencode('.png',im)[1].tobytes()
    n=spec['active_frames'];images={i:png(image) for i in range(n)};vis={i:png(np.full_like(mask,255)) for i in range(n)}
    with pytest.raises(ValueError,match='simulated alpha'):
        element_jobs.prepare_upload('rim','smoke',{'signature':spec['signature']},images,vis)
    take=element_jobs.prepare_upload('rim','smoke',{'signature':spec['signature']},images,vis,{i:png(mask) for i in range(n)})
    rd=projects.pdir('rim')/take['inputs']
    for i in range(n):
        guide=cv2.imread(str(rd/'guide_frames'/f'{i:05d}.png'))
        if i not in (0,n-1) and i%2:assert not guide.any()
        else:assert np.all(guide[35:65,45:85]==65)
        assert np.array_equal(cv2.imread(str(rd/'guide_alpha'/f'{i:05d}.png'),0),mask)
        assert np.array_equal(cv2.imread(str(rd/'raw_guide_frames'/f'{i:05d}.png')),image)
    assert captured[0][1]['provenance']['guide_processing']['order'][-2:] == ['final directional rim cap','sparse sampling']
    assert 'screen-left' in captured[0][0][3]['prompt']
