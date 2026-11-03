"""Native master detail survives preparation, model dispatch, stage reuse and export.

Cloud calls are mocked; ffmpeg decoding/encoding and pixel comparisons are real.
"""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from keyfx import flashes, graphs, image_models, keys, location, media, people, polish, projects, room_refs
from keyfx.config import SIGMAS, VIDEO_PROFILE

SIZE = (1920, 1080)


def detail():
    # A 960px intermediate destroys this alternating one-pixel detail.
    row = np.where(np.arange(1920) % 2, 220, 40).astype(np.uint8)
    return np.repeat(np.tile(row, (1080, 1))[..., None], 3, axis=2)


def frames(path, image, n=2):
    path.mkdir(parents=True, exist_ok=True)
    for i in range(n): cv2.imwrite(str(media.frame_path(path,i)),image)
    return path


def test_scene_draft_is_smaller_while_fx_keeps_native_conditioning_and_sampling():
    scene,_=graphs.environment_graph('scene',Path('scene.png'),Path('source.mp4'),opening=Path('opening.png'),n_frames=55)
    fx,_=graphs.effect_graph('fx',[],Path('source.mp4'),Path('anchor.png'),[(4,Path('key.png'),False)],n_frames=55)
    for g,size in [(scene,(960,544)),(fx,(1920,1088))]:
        for node in ['latent','cond']:
            assert (g[node]['inputs']['width'],g[node]['inputs']['height'])==size
        assert g['sigmas']['inputs']['sigmas']==SIGMAS and len(SIGMAS.split(','))==21
        assert not any('Lora' in n['class_type'] or 'Upscale' in n['class_type'] for n in g.values())
        assert g['cond']['inputs']['ref_videos.ref_video_0']==['proxy_frames',0]
        assert g['save']['inputs']['format.codec.encoding.crf']==10
        for name,node in g.items():
            if name.startswith('scale_'):assert (node['inputs']['width'],node['inputs']['height'])==size


def test_legacy_project_recovers_detail_from_original_and_builds_native_videos(tmp_path,monkeypatch):
    monkeypatch.setattr(projects,'DATA',tmp_path)
    p={'id':'recover','frames':2,'source':{'file':'plate.mp4'},'start_s':0}
    d=projects.pdir(p['id']);old=frames(d/'frames/plate',np.full((540,960,3),120,np.uint8))
    old_bytes=media.frame_path(old,0).read_bytes()
    original=d/'plate.mp4';writer=media._writer(original,*SIZE,True)
    for _ in range(2):writer.stdin.write(detail().tobytes())
    writer.stdin.close();assert writer.wait()==0
    source_bytes=original.read_bytes()
    native=projects.plate_dir(p);recovered=media.read_frame(native,0)
    assert native!=old and recovered.shape==(1080,1920,3)
    assert recovered[200,:,0].std()>80
    assert original.read_bytes()==source_bytes and media.frame_path(old,0).read_bytes()==old_bytes
    assert projects.plate_dir(p)==native
    source=media.content_video(native,2,tmp_path/'sam.mp4')
    reference=media.structure_video(native,2,tmp_path/'reference.mp4')
    assert media.probe(source)['width']==1920 and media.probe(source)['height']==1080
    assert media.probe(reference)['height']==1088
    assert len(media.read_video(reference))==5
    assert np.array_equal(media.unpad(media.pad(recovered)),recovered.astype(np.uint8))


def test_native_master_bypasses_stale_stage_cache_and_survives_muzzle_splice(tmp_path,monkeypatch):
    monkeypatch.setattr(projects,'DATA',tmp_path)
    p={'id':'native','name':'Native','mode':'plate','frames':2,'effects':[{'id':'impact','preset':'wall_hit','keys':[]}],'renders':[{'id':'fx','kind':'effect','status':'done','created':'2026-10-29 10:00'}],
       'source':{},'flash_base':'render:fx','settings':{},'flashes':[]}
    d=projects.pdir(p['id']);plate=frames(d/'frames/plate',detail())
    rd=d/'renders/fx';frames(rd/'frames',np.full((540,960,3),10,np.uint8))
    raw=rd/'raw';raw.mkdir();master=raw/'master.mp4'
    w=media._writer(master,1920,1088,True)
    for _ in range(2):w.stdin.write(media.pad(detail()).tobytes())
    w.stdin.close();assert w.wait()==0
    result=media.finish_render(master,2,plate,rd)
    assert (result['width'],result['height'])==SIZE
    stage=projects.stage_dir(p,'render:fx')
    assert stage.name=='frames_native_v1' and media.read_frame(stage,0).shape==(1080,1920,3)
    assert media.read_frame(stage,0)[200,:,0].std()>80
    flashpath=d/'flashes/test/frame.png';flashpath.parent.mkdir(parents=True)
    cv2.imwrite(str(flashpath),np.full((1080,1920,3),200,np.uint8))
    p['flashes']=[{'id':'flash','frame':1,'origin':{'x':.5,'y':.5},'tip':{'x':.6,'y':.5},'result':str(flashpath.relative_to(d)),
                   'result_base':'render:fx','result_frame_hash':projects.frame_hash(p,'render:fx',1)}]
    projects.save(p)
    final=projects.export_final(p['id']);output=d/'renders'/final['id']/'result.mp4'
    fs=media.read_video(output)
    assert all(f.shape==(1080,1920,3) for f in fs)
    assert fs[0][200,:,0].std()>75 and abs(fs[1].mean()-200)<5
    assert media.probe(d/'renders'/final['id']/'compare.mp4')['width']==3840


def test_native_sam_points_and_people_guide_keep_source_pixels(tmp_path):
    cfg=people.validate({'points':[{'frame':0,'person':0,'label':1,'x':.75,'y':.5}],'feather':0,'trim':0},2)
    g=people.graph(cfg,'qa',SIZE)
    assert json.loads(g['points0']['inputs']['coordinates_positive'])==[{'x':1440,'y':540}]
    fd=frames(tmp_path/'frames',detail());md=frames(tmp_path/'masks',np.full((1080,1920),255,np.uint8))
    guide=people.guide_video(fd,2,md,cfg,tmp_path/'people.mp4')
    decoded=media.read_video(guide,1)[0]
    assert decoded.shape==(1088,1920,3) and media.unpad(decoded)[200,:,0].std()>80


@pytest.mark.parametrize('model',['qwen','flux3','nano'])
def test_native_location_dispatch_never_reduces_input(tmp_path,monkeypatch,model):
    base=detail();captured=[]
    def fake(g,uploads,output,out,**kw):
        inp=cv2.imread(str(uploads['img'][1]));captured.append(inp)
        assert np.array_equal(inp[:1080,:1920],base)
        out.mkdir(parents=True,exist_ok=True);result=out/'result.png';cv2.imwrite(str(result),inp)
        return [result]
    def edit(source,text,seed,out,model,**kw):
        return fake({}, {'img':('image',source)},'save',out)[0]
    monkeypatch.setattr(location.comfy,'run_graph',fake)
    monkeypatch.setattr(image_models,'edit',edit)
    path=location.build(base,'scene',1,tmp_path/model,model=model)
    assert captured and np.array_equal(cv2.imread(str(path)),base)


def test_native_key_layers_and_polish_preserve_detail(tmp_path,monkeypatch):
    base=detail();fd=frames(tmp_path/'base',base);ld=tmp_path/'layers';ld.mkdir()
    layer=np.zeros((1080,1920,4),np.uint8);layer[300:320,400:420]=(10,30,230,255)
    cv2.imwrite(str(ld/'fx_0000.png'),layer)
    comp,mask=projects._layer_comp(fd,ld,0)
    assert np.array_equal(comp[:200],base[:200]) and mask.shape==(1080,1920)
    def edit(source,text,seed,out,model):
        im=cv2.imread(str(source));assert np.array_equal(im,comp.astype(np.uint8))
        out.mkdir(parents=True);result=out/'result.png';cv2.imwrite(str(result),im);return result
    monkeypatch.setattr(image_models,'edit',edit)
    polished=polish.polish_frame(comp,mask,'integrate',1,tmp_path/'cache',model='nano')
    assert polished.shape==base.shape and np.array_equal(polished[:100],base[:100])
    # Backend-generated procedural keys use native coordinates too.
    composed=keys.compose(base.astype(np.float32),[{'id':'hit','preset':'wall_hit','keys':[{'phase':'damage','frame':0,'x':.75,'y':.5,'scale':.05}]}],0)
    assert composed.shape==base.shape


def test_native_flash_canvas_is_exact_and_low_res_editor_output_is_rejected():
    base=detail();canvas=flashes.to_model_canvas(base)
    assert canvas.shape==(1152,2048,3)
    assert np.array_equal(canvas[36:1116,64:1984],base)
    assert np.array_equal(flashes.from_model_canvas(canvas,SIZE),base)
    assert flashes.graph('flash',1)[flashes.BIND['model']]['inputs']['resolution']=='2K'
    with pytest.raises(ValueError,match='lower-resolution'):
        flashes.from_model_canvas(cv2.resize(canvas,(1024,576)),SIZE)
    for model in ['qwen','flux3','nano']:
        work=image_models.prepare(base,model)
        assert np.array_equal(work[:1080,:1920],base)
        with pytest.raises(RuntimeError,match='below the requested'):
            image_models.restore(np.zeros((540,960,3),np.uint8),work,SIZE)


def test_uploaded_room_master_keeps_native_detail_above_previous_cap(tmp_path):
    import io
    from PIL import Image
    row=np.where(np.arange(3000)%2,220,40).astype(np.uint8)
    im=np.repeat(np.tile(row,(1000,1))[...,None],3,axis=2)
    out=io.BytesIO();Image.fromarray(im).save(out,format='PNG')
    ref,encoded=room_refs.prepare(out.getvalue(),'room.png')
    assert (ref['width'],ref['height'])==(3000,1000)
    restored=np.array(Image.open(io.BytesIO(encoded)))
    assert np.array_equal(restored,im)
    source=tmp_path/'room.png';source.write_bytes(encoded)
    for model in ['qwen','flux3']:
        prepared=location.prepare_reference(source,model,tmp_path/f'{model}.png',SIZE)
        actual=cv2.imread(str(prepared))
        assert np.array_equal(actual[:1000,:3000],im)


@pytest.mark.parametrize('size',[(1920,1080),(960,540)])
def test_nano_room_batch_keeps_plate_and_complete_portrait(size,tmp_path,monkeypatch):
    src=np.full((size[1],size[0],3),90,np.uint8)
    src[:,::2]=170  # preserve native plate detail, including the legacy test case
    portrait=np.full((3000,1000,3),110,np.uint8)
    portrait[:100]=(20,80,210);portrait[-100:]=(200,40,60)
    ref=tmp_path/'room.png';cv2.imwrite(str(ref),portrait)
    master=ref.read_bytes()
    def partner(g,node,source,output,out,description,additional_images=None):
        plate=cv2.imread(str(source));room=cv2.imread(str(additional_images['room_1']))
        assert np.array_equal(plate,src)
        assert room.shape==plate.shape
        x=size[0]//2
        assert np.array_equal(room[0,x],portrait[0,500])
        assert np.array_equal(room[-1,x],portrait[-1,500])
        assert np.all(room[:,0]==24)  # letterbox, not a centre crop
        out.mkdir(parents=True);result=out/'raw.png';cv2.imwrite(str(result),plate)
        return [result]
    monkeypatch.setattr(location.comfy,'run_partner_graph',partner)
    output=location.build(src,'Room',42,tmp_path/'build',model='nano',references=[ref])
    assert np.array_equal(cv2.imread(str(output)),src)
    assert ref.read_bytes()==master
