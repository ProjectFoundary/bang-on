"""True simulated alpha through preparation/cache/native composition; no provider."""
import copy
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from keyfx import alpha_core, connections, element_graphs, element_jobs, elements, matte_treatment
from keyfx import native_colour, native_media, projects
from test_element_pipeline import pipeline, png, catalogue


def inputs(p):
    spec = element_jobs.plan(p, 'blast'); rgb, visibility, alphas = {}, {}, {}
    for i in range(spec['active_frames']):
        im = np.zeros((spec['height'],spec['width'],3),np.uint8)
        im[6:26,3:15] = (20,50,100+i)
        guide = np.zeros(im.shape[:2],np.uint8)
        guide[4:28,2:16] = 160+i*20
        vis = np.full(im.shape[:2],128,np.uint8);vis[:,8:10] = 0
        rgb[i],visibility[i],alphas[i] = png(im),png(vis),png(guide)
    return spec,rgb,visibility,alphas


def prepare(p, *, assisted=True):
    spec,rgb,visibility,alphas = inputs(p)
    row = element_jobs.prepare_upload(p['id'],'blast',{'signature':spec['signature']},rgb,visibility,
                                      guide_alpha=alphas if assisted else None)
    return row,alphas


def complete(p,row,calls):
    """Real independent stages and validation; only transport is a tiny fake."""
    def provider(*,graph,uploads,out_dir,on_status,**_):
        stage = 'alpha' if 'alpha-gen' in graph['lora']['inputs']['lora_name'] else 'rgb'
        calls.append(stage)
        assert set(uploads)=={'control'} and '/native/' not in str(uploads['control'][1])
        dims=graph['latent']['inputs']; w,h,n=dims['width'],dims['height'],dims['length']
        x,y=(w-row['plan']['width'])//2,(h-row['plan']['height'])//2
        frames=[]
        for _ in range(n):
            im=np.zeros((h,w,3),np.uint8)
            im[y+6:y+26,x+3:x+15]=(20,50,100) if stage=='rgb' else 64
            im[y+9:y+23,x+5:x+13]=0  # black smoke RGB; AlphaGen has a hollow core
            frames.append(im)
        out_dir.mkdir(parents=True,exist_ok=True);path=out_dir/'output.mp4'
        elements._encode(path,frames);on_status('fake-'+stage,'running');return [path]
    nodes=catalogue([g for _,_,g,_ in element_graphs.requirements({})])
    done=elements.run(Path(row['take_dir']),executor=provider,catalog=nodes)
    current=projects.load(p['id'])
    target=next(r for r in current['element_takes'] if r['id']==row['id'])
    target.update(status='done',assets=done['assets']);projects.save(current)
    return copy.deepcopy(target),done


def test_sparse_rgb_keeps_every_true_alpha_frame_and_freezes_policy(pipeline):
    create,_,_=pipeline;p,root,_=create(fps='24/1')
    p['effects'][0]['element'].update(duration_s=.2,cadence=5);projects.save(p)
    row,alphas=prepare(p);rd=root/row['inputs'];spec=row['plan']
    assert spec['alpha_core']==alpha_core.policy() and spec['active_frames']==5
    for i,data in alphas.items():
        actual=cv2.imread(str(rd/'guide_alpha'/f'{i:05d}.png'),0)
        assert np.array_equal(actual,cv2.imdecode(np.frombuffer(data,np.uint8),0))
    assert not cv2.imread(str(rd/'guide_frames/00001.png')).any()
    assert cv2.imread(str(rd/'guide_alpha/00001.png'),0).any(), 'Sparse cadence erased core motion'
    manifest=json.loads((rd/'manifest.json').read_text())
    assert manifest['version']==2
    assert all(manifest['files'][f'guide_alpha/{i:05d}.png']==elements._sha(rd/'guide_alpha'/f'{i:05d}.png') for i in alphas)
    frozen=element_jobs._verify_inputs(root,row)
    assert frozen['provenance']['plan']['alpha_core']==alpha_core.policy()


def test_guide_alpha_tamper_invalidates_frozen_take(pipeline):
    create,_,_=pipeline;p,root,_=create()
    row,_=prepare(p)
    path=root/row['inputs']/'guide_alpha/00001.png'
    image=cv2.imread(str(path),0);image[16,8]^=255;assert cv2.imwrite(str(path),image)
    with pytest.raises(ValueError,match='input changed'):
        element_jobs._verify_inputs(root,row)


@pytest.mark.parametrize('malformed',['missing','colour','wrong_size','uint16'])
def test_preparation_rejects_non_data_or_incomplete_alpha(pipeline,malformed):
    create,_,_=pipeline;p,root,_=create();spec,rgb,vis,alphas=inputs(p)
    if malformed=='missing':alphas.pop(1)
    elif malformed=='colour':alphas[1]=png(np.full((spec['height'],spec['width'],3),(10,90,200),np.uint8))
    elif malformed=='wrong_size':alphas[1]=png(np.zeros((2,2),np.uint8))
    else:alphas[1]=png(np.ones((spec['height'],spec['width']),np.uint16)*40000)
    before=(root/'project.json').read_bytes()
    with pytest.raises(ValueError):
        element_jobs.prepare_upload(p['id'],'blast',{'signature':spec['signature']},rgb,vis,guide_alpha=alphas)
    assert (root/'project.json').read_bytes()==before


@pytest.mark.parametrize('association',['black_premultiplied','straight'])
def test_core_is_applied_before_visibility_opacity_and_straight_premultiply(pipeline,association):
    create,_,_=pipeline;p,root,originals=create(fps='24/1')
    p['effects'][0]['element'].update(opacity=.4,gain=.8);projects.save(p)
    row,_=prepare(p);row,take=complete(p,row,[]);spec=row['plan']
    preserved={str(path):elements._sha(path) for path in root.rglob('*') if path.is_file()}
    effect=projects.load('shot')['effects'][0]
    take=copy.deepcopy(take);take['assets']['rgb']['colour_association']=association
    f=spec['start_frame'];k=element_jobs.model_index(spec,p['native_profile']['timestamps'][f])
    x,y,w,h=take['geometry']['content_rect']
    rgb=cv2.cvtColor(cv2.imread(str(Path(take['assets']['rgb']['frames_dir'])/f'{k:05d}.png'))[y:y+h,x:x+w],cv2.COLOR_BGR2RGB).astype(np.float32)/255
    raw_alpha=cv2.imread(str(Path(take['assets']['alpha']['frames_dir'])/f'{k:05d}.png'),0)[y:y+h,x:x+w].astype(np.float32)/255
    guide=cv2.imread(str(root/row['inputs']/'guide_alpha'/f'{k:05d}.png'),0)
    assisted=matte_treatment.apply(raw_alpha,effect['element'])
    assert raw_alpha[16,6]<.01, 'Fixture did not exercise the missing black core'
    vis=cv2.imread(str(root/row['inputs']/'visibility'/f'{k:05d}.png'),0).astype(np.float32)/255
    factor=vis*.4
    if association=='straight':factor=factor*assisted
    light=native_colour.srgb_to_linear(rgb)*.8*factor[...,None]
    expected=native_colour.from_linear(native_colour.to_linear(originals[f],p['native_profile'])*(1-assisted[...,None]*vis[...,None]*.4)+light,p['native_profile'],np.uint16)
    class NoPost:
        def visibility(self,frame):return None
    actual=element_jobs._composite_frame(p,[(effect,row,take)],p['native_profile'],{effect['id']:NoPost()},f)
    assert np.max(np.abs(actual.astype(np.int32)-expected.astype(np.int32)))<=1
    assert np.array_equal(actual[:,8:10],originals[f][:,8:10]), 'Core was added after foreground visibility'
    assert all(elements._sha(Path(path))==sha for path,sha in preserved.items())


def test_legacy_approval_stays_pinned_and_raw_treatment_needs_no_core_recapture(pipeline,monkeypatch):
    create,_,_=pipeline;p,root,_=create()
    signature=element_jobs.generation_signature(p,p['effects'][0]);row,_=prepare(p,assisted=False)
    row,take=complete(p,row,[])
    current=projects.load('shot');record=next(r for r in element_jobs.records(current) if r['id']==row['id'])
    assert record['current'] and not record['needs_alpha_core'] and not record['approved']
    element_jobs.approve('shot',row['id']);current=projects.load('shot')
    monkeypatch.setattr(alpha_core,'PROFILE','future-finishing-only-policy')
    assert element_jobs.generation_signature(current,current['effects'][0])==signature
    record=next(r for r in element_jobs.records(current) if r['id']==row['id'])
    assert record['approved'] and record['current'] and not record['needs_alpha_core']
    assert element_jobs.selected_take(current,current['effects'][0])['id']==row['id']
    manifest=json.loads((root/row['inputs']/'manifest.json').read_text())
    assert manifest['version']==1 and 'alpha_core' not in row['plan']


def test_new_guide_alpha_changes_only_post_inputs_and_reuses_both_paid_stages(pipeline):
    create,_,_=pipeline;p,root,_=create()
    old,_=prepare(p,assisted=False);calls=[];old,old_take=complete(p,old,calls)
    p=projects.load('shot');new,_=prepare(p,assisted=True)
    assert new['id']!=old['id'] and new['generation_signature']==old['generation_signature']
    assert elements._sha(root/new['inputs']/'guide.mp4')==elements._sha(root/old['inputs']/'guide.mp4')
    new,new_take=complete(p,new,calls)
    assert calls==['rgb','alpha'], 'Finishing-only alpha guidance submitted a new model stage'
    assert new_take['stages']['rgb']['cached'] and new_take['stages']['alpha']['cached']
    assert new_take['assets']==old_take['assets']
