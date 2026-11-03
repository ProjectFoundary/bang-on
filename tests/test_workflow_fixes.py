"""Clip-length contracts, recoverable action drafts and actual matte compositing; no paid inference."""
import copy,json
from pathlib import Path
import cv2,numpy as np,pytest
from fastapi.testclient import TestClient
from keyfx import analysis,graphs,media,people,projects
from keyfx.config import render_length
from keyfx.server import app


@pytest.fixture
def clip(tmp_path,monkeypatch):
    monkeypatch.setattr(projects,'DATA',tmp_path)
    class Deferred:
        def __init__(self,**kwargs):pass
        def start(self):pass
    monkeypatch.setattr(projects.threading,'Thread',Deferred)
    monkeypatch.setattr(projects.comfy,'run_graph',lambda *a,**kw:pytest.fail('Unexpected Cloud inference'))
    p={'id':'matteqa','name':'Matte QA','created':'2026-10-29','mode':'plate','frames':3,'source':{},'shot':'My edited action',
       'location':'','environment':None,'settings':{'seed':7},'effects':[],'renders':[],
       'people':{'selection':'manual','mode':'guide','points':[{'person':0,'frame':0,'label':1,'x':.2,'y':.4}],'feather':0,'trim':0}}
    d=projects.pdir(p['id']);fd=d/'frames/plate';fd.mkdir(parents=True)
    for i in range(3):cv2.imwrite(str(fd/f'{i:05d}.png'),np.full((540,960,3),90+i,np.uint8))
    projects.save(p);return p


def test_generation_requests_smallest_valid_grid():
    assert [render_length(n) for n in [1,5,6,55,56,77,99,124,362]]==[5,5,22,56,56,90,107,124,362]
    with pytest.raises(ValueError):render_length(363)
    for n in [55,77,99,362]:
        g,_=graphs.environment_graph('source',Path('scene.png'),Path('source.mp4'),n_frames=n)
        assert g['latent']['inputs']['length']==render_length(n)
        assert g['cond']['inputs']['length']==render_length(n)
        e,_=graphs.effect_graph('source',[],Path('source.mp4'),Path('anchor.png'),[],n_frames=n)
        assert e['latent']['inputs']['length']==render_length(n)


def test_structure_and_analysis_videos_have_distinct_length_contracts(clip,tmp_path):
    fd=projects.pdir(clip['id'])/'frames/plate'
    reference=media.structure_video(fd,3,tmp_path/'h3.mp4')
    analysis_video=media.content_video(fd,3,tmp_path/'analysis.mp4')
    assert len(media.read_video(reference))==5
    assert len(media.read_video(analysis_video))==3


def test_incomplete_render_is_not_accepted_as_success(clip,tmp_path):
    fd=projects.pdir(clip['id'])/'frames/plate';raw=media.content_video(fd,3,tmp_path/'short.mp4')
    with pytest.raises(RuntimeError,match='3 of the required 4'):media.finish_render(raw,4,fd,tmp_path)
    assert not (tmp_path/'result.mp4').exists()


def test_action_draft_never_silently_replaces_user_text(clip,monkeypatch):
    monkeypatch.setattr(analysis,'draft',lambda *a,**kw:'At 0 seconds the actor stands, then turns left.')
    r=projects.start_render(clip['id'],'analysis',_background=False);projects._run(clip['id'],r['id'])
    p=projects.load(clip['id']);r=p['renders'][0]
    assert r['status']=='done' and 'then turns left' in r['draft']
    assert p['shot']=='My edited action'


def test_multiple_people_and_corrections_keep_separate_object_ids(clip):
    cfg=copy.deepcopy(clip['people']);cfg['points'] += [
       {'person':1,'frame':0,'label':1,'x':.6,'y':.4},
       {'person':0,'frame':2,'label':1,'x':.3,'y':.5},
       {'person':0,'frame':2,'label':0,'x':.4,'y':.5}]
    g=people.graph(cfg,'qa')
    assert [g[f'points{i}']['inputs']['object_index'] for i in range(3)]==[0,1,0]
    assert g['points2']['inputs']['prev_inference_state']==['points1',1]
    assert json.loads(g['points2']['inputs']['coordinates_negative'])[0]['x']==384
    cfg['points'][1]['frame']=1
    with pytest.raises(ValueError,match='first frame'):people.graph(cfg,'qa')


def add_matte(p):
    r={'id':'matte1','kind':'matte','status':'done','created':'2026-10-29','files':{},'mask_signature':people.mask_signature(p)}
    p['renders'].append(r);projects.save(p)
    md=projects.pdir(p['id'])/'renders/matte1/masks';md.mkdir(parents=True)
    for i in range(p['frames']):
        m=np.zeros((540,960),np.uint8);m[:,0:200]=255;cv2.imwrite(str(md/f'{i:05d}.png'),m)
    return md


def test_edge_edits_reuse_tracking_but_new_selection_invalidates_it(clip):
    add_matte(clip);assert people.current(clip)
    old=people.signature(clip);clip['people']['feather']=2
    assert people.current(clip) and people.signature(clip)!=old
    clip['people']['points'][0]['x']=.3
    assert people.current(clip) is None


def test_cutout_really_restores_source_and_retains_background_outside_mask(clip,tmp_path):
    masks=add_matte(clip);fd=projects.pdir(clip['id'])/'frames/plate'
    raw=tmp_path/'background.mp4';w=media._writer(raw,960,544,True)
    for _ in range(3):w.stdin.write(np.full((544,960,3),30,np.uint8).tobytes())
    w.stdin.close();assert w.wait()==0
    out=people.composite(raw,fd,3,masks,clip['people'],tmp_path/'cutout.mp4')
    frames=media.read_video(out)
    assert len(frames)==3
    for i,f in enumerate(frames):
        assert abs(float(f[100,50].mean())-(90+i))<2
        assert abs(float(f[100,700].mean())-30)<2


def test_cutout_download_contains_real_alpha_channel(clip,tmp_path):
    masks=add_matte(clip);out=tmp_path/'cutout.mov'
    people.export_cutout(projects.pdir(clip['id'])/'frames/plate',3,masks,clip['people'],out)
    import subprocess
    raw=subprocess.run(['ffmpeg','-v','error','-i',str(out),'-frames:v','1','-f','rawvideo','-pix_fmt','rgba','-'],capture_output=True,check=True).stdout
    im=np.frombuffer(raw,np.uint8).reshape(540,960,4)
    assert im[100,50,3]>250 and im[100,700,3]<5


def test_matte_api_validation_and_preview_are_local(clip):
    add_matte(clip);c=TestClient(app,base_url='http://127.0.0.1:8766');base='/api/projects/matteqa'
    assert c.patch(base,json={'people':{'points':[{'frame':0,'person':0,'label':1,'x':1.2,'y':.2}]}}).status_code==400
    r=c.get(base+'/people/frame/0?view=cutout')
    assert r.status_code==200 and r.headers['content-type']=='image/png'
    assert c.get(base+'/people/frame/3').status_code==404


def test_duplicate_analysis_submission_does_not_buy_two_jobs(clip):
    projects.start_render(clip['id'],'analysis')
    with pytest.raises(ValueError,match='already in progress'):projects.start_render(clip['id'],'analysis')
    assert len(projects.load(clip['id'])['renders'])==1


@pytest.mark.parametrize('order',[('analysis','matte'),('matte','analysis')])
def test_background_analysis_and_matte_can_run_together_but_generations_stay_serial(clip,order):
    for kind in order:projects.start_render(clip['id'],kind)
    p=projects.load(clip['id'])
    assert {r['kind'] for r in p['renders']}=={'analysis','matte'}
    assert projects.generation_active(p)
    with pytest.raises(ValueError,match='current processing'):projects.start_render(clip['id'],'matte')


def test_background_draft_does_not_lock_setup_or_overwrite_new_edits(clip,monkeypatch):
    r=projects.start_render(clip['id'],'analysis')
    assert not projects.generation_active(projects.load(clip['id']))
    c=TestClient(app,base_url='http://127.0.0.1:8766')
    response=c.patch('/api/projects/matteqa',json={'shot':'My newer performance description','location':'Brick room'})
    assert response.status_code==200
    monkeypatch.setattr(analysis,'draft',lambda *a,**kw:'The model suggests a turn.')
    monkeypatch.setattr(projects,'plate_dir',lambda *a:pytest.fail('Analysis must not rebuild native generation media'))
    projects._run(clip['id'],r['id'])
    p=projects.load(clip['id'])
    assert p['shot']=='My newer performance description' and p['location']=='Brick room'
    assert p['renders'][0]['status']=='done' and p['renders'][0]['draft']=='The model suggests a turn.'


def test_complete_take_can_start_while_analysis_runs(clip):
    projects.start_render(clip['id'],'analysis')
    r=projects.start_pipeline(clip['id'])
    assert r['kind']=='pipeline' and projects.pipeline_active(projects.load(clip['id']))


def test_light_analysis_samples_whole_clip_and_preserves_original_timing(clip,tmp_path):
    # Only these source frames need to exist, even for a long native clip.
    fd=tmp_path/'native';fd.mkdir()
    indices=analysis.sample_frames(362)
    assert len(indices)==8 and indices[0]==0 and indices[-1]==361 and indices==sorted(set(indices))
    for i,f in enumerate(indices):
        cv2.imwrite(str(fd/f'{f:05d}.png'),np.full((1080,1920,3),i*25,np.uint8))
    proxy=analysis.preview(fd,362,tmp_path/'small.mp4')
    info=media.probe(proxy);frames=media.read_video(proxy)
    assert (info['width'],info['height'])==(512,288) and len(frames)==8
    assert all(abs(float(f.mean())-i*25)<4 for i,f in enumerate(frames))
    assert cv2.imread(str(fd/'00000.png')).shape==(1080,1920,3)
    assert proxy.stat().st_size<100_000
    g=analysis.graph(362,'test');cfg=g['analyse']['inputs']
    assert cfg['model_name']=='Qwen3-VL-4B-Instruct' and cfg['frame_count']==8 and cfg['max_tokens']==256
    assert '15.042s' in cfg['custom_prompt'] and 'Ignore the preview playback rate' in cfg['custom_prompt']
    assert analysis.sample_frames(1)==[0] and analysis.sample_frames(3)==[0,1,2]
    with pytest.raises(ValueError):analysis.sample_frames(0)


def test_draft_uploads_only_small_proxy_and_retains_sample_provenance(clip,tmp_path,monkeypatch):
    def cloud(g,uploads,node,out,**kw):
        source=uploads['input'][1]
        assert source.name=='action_preview.mp4'
        assert media.probe(source)['width']<=512 and len(media.read_video(source))==3
        out.mkdir(parents=True);text=out/'caption.txt';text.write_text('The person turns.');return [text]
    monkeypatch.setattr(analysis.comfy,'run_graph',cloud)
    assert analysis.draft(projects.pdir(clip['id'])/'frames/plate',3,tmp_path)=='The person turns.'
    assert json.loads((tmp_path/'samples.json').read_text())['frames']==[0,1,2]
    assert not (tmp_path/'source.mp4').exists()


def test_action_review_survives_reload_and_rejects_unknown_draft(clip):
    clip['renders'].append({'id':'draft1','kind':'analysis','status':'done','draft':'An observed action','files':{}})
    projects.save(clip);c=TestClient(app,base_url='http://127.0.0.1:8766');base='/api/projects/matteqa'
    assert c.patch(base,json={'action_review':'not-a-draft'}).status_code==400
    assert c.patch(base,json={'shot':'My reviewed version','action_review':'draft1'}).status_code==200
    p=c.get(base).json()
    assert p['shot']=='My reviewed version' and p['action_review']=='draft1'


def test_pipeline_failure_gets_a_later_terminal_timestamp_than_completed_video(clip):
    clip['renders']=[{'id':'fx','kind':'effect','status':'rendering','files':{}},{'id':'take','kind':'pipeline','status':'effects','files':{}}]
    projects.save(clip)
    projects._set(clip['id'],'fx',status='done')
    projects._set(clip['id'],'take',status='error',error='Muzzle frame failed')
    video,pipeline=projects.load(clip['id'])['renders']
    assert pipeline['finished_at']>=video['finished_at']
    assert pipeline['status']=='error' and pipeline['error']=='Muzzle frame failed'
