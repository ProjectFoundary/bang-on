"""Guide order, temporal averaging, and local controls."""
import copy
import cv2
import numpy as np
import pytest
from keyfx import element_jobs, geometry, painted_guides, presets, post_holdouts


def test_defaults_validation_and_guide_version():
    p={'frames':96,'geometry':geometry.defaults(),'settings':{}}
    e=presets.new_effect('explosion',0,.5,.5,'blast',96)
    cfg=element_jobs.effect_config(p,e)
    assert (cfg['cadence'],cfg['frame_blend'],cfg['guide_style'])==(10,10,'painted')
    assert cfg['light_intensity']==1
    baseline=element_jobs.generation_signature(p,e)
    neutral=copy.deepcopy(e);neutral['element']={'light_intensity':1}
    assert element_jobs.generation_signature(p,neutral)==baseline
    for key,value in [('cadence',7),('frame_blend',1),('light_intensity',0),('light_intensity',4),('light_rotation',90),('light_elevation',25),('guide_displacement',40),('guide_displacement_blur',64),('guide_style','rendered')]:
        candidate=copy.deepcopy(e);candidate['element']={key:value}
        if key=='guide_displacement_blur':candidate['element']['guide_displacement']=40
        assert element_jobs.effect_config(p,candidate)[key]==value
        assert element_jobs.generation_signature(p,candidate)!=baseline
    for key,value in [('cadence',True),('cadence',2.5),('cadence',0),('frame_blend',49),('frame_blend',False),('light_intensity',True),('light_intensity',-1),('light_intensity',4.1),('light_intensity',float('nan')),('light_rotation',float('nan')),('guide_displacement',-1)]:
        candidate=copy.deepcopy(e);candidate['element']={key:value}
        with pytest.raises(ValueError):element_jobs.effect_config(p,candidate)


def test_linear_average_precedes_sparse_selection(tmp_path):
    images=[]
    for i in range(16):
        image=np.full((4,6,3),255 if i==7 else 0,np.uint8);images.append(image)
        cv2.imwrite(str(tmp_path/f'{i:05d}.png'),image)
    actual=list(painted_guides.blend(tmp_path,len(images),10))
    assert np.all(actual[2]==89) and np.all(actual[11]==89)
    assert not actual[1].any() and not actual[12].any()
    assert np.all(actual[10]==89), 'Non-key source frame7 must contribute to key10'
    for f in range(16):
        reference=np.mean([painted_guides._LINEAR[images[min(15,max(0,f+k))]] for k in range(-4,6)],axis=0)
        encoded=np.where(reference<=.0031308,reference*12.92,1.055*reference**(1/2.4)-.055)
        assert np.array_equal(actual[f],np.round(encoded*255).astype(np.uint8))
    assert all(np.array_equal(a,b) for a,b in zip(images,painted_guides.blend(tmp_path,16,1)))


def test_paint_retains_flame_chroma_and_shading():
    image=np.array([[[0,0,0],[30,30,30],[70,70,70],[100,100,100],[0,60,120]]],np.uint8)
    cfg={**element_jobs.DEFAULT,'blur':0};painted=painted_guides.paint(image,cfg)
    assert painted[0,:4,0].tolist()==[0,65,150,240]
    assert painted[0,4].tolist()==[0,120,240]
    assert np.array_equal(painted_guides.paint(image,{**cfg,'guide_style':'rendered'}),image)


def test_turbulence_repeatable_and_guide_only(tmp_path,monkeypatch):
    h,w=54,96;mask=np.zeros((h,w),np.float32);mask[15:40,30:60]=1
    image=np.zeros((h,w,3),np.uint8);image[12:42,20:76]=150
    original=image.copy();mask_copy=mask.copy()
    monkeypatch.setattr(post_holdouts,'camera_matrices',lambda *_:[np.eye(3)]*16)
    monkeypatch.setattr(post_holdouts,'read_mask',lambda *_a,**_kw:mask)
    p={'geometry':{'mattes':[{'id':'wall','role':'both','static':True}]}}
    spec={'settings':{**element_jobs.DEFAULT,'guide_displacement':58,'colliders':['wall']},'width':w,'height':h,'source_frames':list(range(16))}
    a=painted_guides.Displacement(tmp_path,p,{},spec);b=painted_guides.Displacement(tmp_path,p,{},spec)
    first=a.apply(image,0);same=b.apply(image,0)
    assert np.array_equal(first,same) and not np.array_equal(first,image)
    assert np.array_equal(image,original) and np.array_equal(mask,mask_copy)
    assert not first[:4].any()
    zero=copy.deepcopy(spec);zero['settings']['guide_displacement']=0
    assert np.array_equal(painted_guides.Displacement(tmp_path,p,{},zero).apply(image,0),image)


def test_cg_boxes_static_orthonormal_validation():
    box={'center':[0,0,-5],'axes':np.eye(3).tolist(),'halfSize':[2,.2,2]}
    good={'mattes':[{'id':'roof','static':True,'cameraBox':box}]}
    assert geometry.validate(good,96)['mattes'][0]['cameraBox']==box
    for change in [{'static':False},{'cameraBox':{**box,'halfSize':[0,1,1]}},{'cameraBox':{**box,'axes':np.zeros((3,3)).tolist()}}]:
        with pytest.raises(ValueError):geometry.validate({'mattes':[{**good['mattes'][0],**change}]},96)


def test_saved_legacy_recipe_is_preserved_until_painted_defaults_are_chosen():
    p={'frames':96,'geometry':geometry.defaults(),'settings':{}}
    e=presets.new_effect('explosion',0,.5,.5,'blast',96)
    e['element']={'cadence':1,'blur':2,'shading':1,'seed':42,'duration_s':2}
    cfg=element_jobs.effect_config(p,e)
    assert cfg['guide_style']=='rendered' and cfg['frame_blend']==1
    before=element_jobs.generation_signature(p,e)
    e['element'].update(frame_blend=1,guide_style='rendered',light_rotation=0,light_elevation=60,guide_displacement=0,guide_displacement_blur=80)
    assert element_jobs.generation_signature(p,e)==before
    e['element'].update(cadence=10,frame_blend=10,guide_style='painted')
    assert element_jobs.generation_signature(p,e)!=before
