"""Offline release acceptance against an installed wheel, never a checkout.

Run outside the source tree with a fresh environment containing the wheel and
httpx. Only Comfy transport is synthetic; import, immutable stage lifecycle,
approval, native composite and containers/layer archives use the installed app.
"""
from __future__ import annotations
import argparse, copy, hashlib, io, json, os, re, socket, subprocess, tempfile, threading, time, zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    work=Path(tempfile.mkdtemp(prefix='bang-on-installed-',dir='/tmp'))
    for key in ('COMFY_API_KEY','COMFY_BASE_URL','COMFY_LOCAL_TOKEN','COMFYUI_URL','KEYFX_FLASH_CREDITS','BANG_ON_DEVELOPMENT_GATE'):
        os.environ.pop(key,None)
    os.environ['KEYFX_DATA']=str(work/'data')
    os.environ['KEYFX_SKIP_EXAMPLES']='1'
    import keyfx, cv2, numpy as np
    from fastapi.testclient import TestClient
    from keyfx import config, connections, element_graphs, element_jobs, elements, flashes, native_media, projects
    from keyfx.server import app
    assert 'site-packages' in keyfx.__file__,keyfx.__file__
    assert config.WEB_DIST.name=='web_dist' and config.WEB_DIST.is_dir()
    assert flashes.budget_credits()==0
    original_connect=socket.socket.connect
    def loopback_only(sock,address):
        if isinstance(address,tuple) and address[0] not in ('127.0.0.1','localhost','::1'):
            raise AssertionError('Outbound provider/network access forbidden in release QA')
        return original_connect(sock,address)
    def deny(*a,**kw):raise AssertionError('Real Comfy transport forbidden during release QA')
    frames=work/'input';frames.mkdir()
    for f in range(12):
        image=np.zeros((64,96,3),np.uint16);image[:]=[13000+f*70,20000,30000]
        image[15:45,20+f:40+f]=[20000,25000,35000]
        native_media.write_native_frame(frames,f,image)
    source=work/'source.mkv'
    subprocess.run(['ffmpeg','-v','error','-y','-framerate','24000/1001','-i',str(frames/'%05d.png'),
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=0.5005','-map','0:v','-map','1:a',
        '-c:v','ffv1','-pix_fmt','gbrp16le','-color_primaries','bt709','-color_trc','bt709','-colorspace','0',
        '-c:a','pcm_s16le','-shortest',str(source)],check=True)
    original=source.read_bytes();calls=[]
    rgb_started=threading.Event();release_rgb=threading.Event()
    cfg=connections.Connection(mode='local',local_url='http://127.0.0.1:8189',comfyui_url='http://127.0.0.1:8188')
    catalog={}
    for _,_,graph,_ in element_graphs.requirements({}):
        for node in graph.values():
            inputs=catalog.setdefault(node['class_type'],{'input':{'required':{}}})['input']['required']
            for key,val in node['inputs'].items():
                if key in connections._MODEL_FIELDS and isinstance(val,str):
                    choices=inputs.setdefault(key,[[],{}])[0]
                    if val not in choices:choices.append(val)
    def provider(*,graph,uploads,out_dir,on_status,idempotency_key,**kw):
        stage='alpha' if 'alpha-gen' in graph['lora']['inputs']['lora_name'] else 'rgb';calls.append(stage)
        if stage=='rgb':
            rgb_started.set()
            assert release_rgb.wait(15),'Concurrent-submit stress never released its fixture worker'
        assert set(uploads)=={'control'} and '/native/' not in str(uploads['control'][1])
        g=graph['latent']['inputs'];images=[]
        for i in range(g['length']):
            im=np.zeros((g['height'],g['width'],3),np.uint8)
            im[25:39,50:68]=128 if stage=='alpha' else (20,45,70)
            images.append(im)
        out_dir.mkdir(parents=True,exist_ok=True);movie=out_dir/'result.mp4';elements._encode(movie,images)
        on_status('fixture-'+stage,'running');return [movie]
    real_run=elements.run
    def fixture_run(td,on_status=None):return real_run(td,executor=provider,catalog=catalog,on_status=on_status)
    def wait(pid,rid,kind='renders'):
        until=time.monotonic()+90
        while time.monotonic()<until:
            row=next(r for r in projects.load(pid)[kind] if r['id']==rid)
            if row['status'] in ('done','error'):
                assert row['status']=='done',row;return row
            time.sleep(.03)
        raise AssertionError('Timed out waiting for '+rid)
    def ok(response):assert response.status_code==200,response.text;return response.json()
    with patch('socket.socket.connect',loopback_only),patch('keyfx.comfy.client',deny),patch('keyfx.comfy.run_graph',deny),\
         patch('keyfx.comfy.run_partner_graph',deny),patch.object(connections,'current',lambda:cfg),\
         patch.object(connections,'_get_json',deny),patch.object(elements,'run',fixture_run),\
         TestClient(app,base_url='http://127.0.0.1') as client:
        assert ok(client.get('/api/health'))=={'ok':True};assert ok(client.get('/api/projects'))==[]
        html=client.get('/');assert html.status_code==200
        assets=re.findall(r'(?:src|href)="((?:\./|/)assets/[^\"]+)"',html.text);assert len(assets)>=2
        for asset in assets:assert client.get(asset).status_code==200
        assert client.get('/api/presets/explosion/peak.png').status_code==200
        assert client.get('/api/health',headers={'Host':'untrusted.invalid'}).status_code==403
        assert client.post('/api/projects',headers={'Origin':'https://untrusted.invalid'}).status_code==403
        p=ok(client.post('/api/projects',data={'name':'Installed release fixture','mode':'plate','preserve_native':'true','colour_profile':'rec709'},
            files={'file':('source.mkv',original,'video/x-matroska')}))
        pid=p['id'];base=f'/api/projects/{pid}';root=projects.pdir(pid)
        assert p['frames']==12 and not p['renders'] and p['settings']['pipeline']=='ltx_elements'
        assert (root/'native'/p['native_profile']['source_file']).read_bytes()==original
        native=[native_media.read_native_frame(root/'native',i) for i in range(12)]
        p=ok(client.post(base+'/effects',json={'preset':'smoke_grenade','frame':2,'x':.6,'y':.5}))
        effect=p['effects'][0];effect.setdefault('element',{})['duration_s']=.25
        ok(client.patch(base,json={'effects':[effect]}));eid=effect['id']
        plan=ok(client.get(base+'/elements/plan',params={'effect_id':eid}));files=[]
        for f in range(plan['active_frames']):
            rgb=np.zeros((plan['height'],plan['width'],3),np.uint8);rgb[25:39,50:68]=100
            vis=np.full(rgb.shape[:2],255,np.uint8)
            alpha=np.zeros(rgb.shape[:2],np.uint8);alpha[25:39,50:68]=128
            for name,im in [('guide',rgb),('visibility',vis),('guide_alpha',alpha)]:
                success,data=cv2.imencode('.png',im);assert success
                files.append((f'{name}_{f:04d}.png',(f'{name}.png',data.tobytes(),'image/png')))
        take=ok(client.post(base+f'/elements/{eid}/prepare',data={'metadata':json.dumps({'signature':plan['signature']})},files=files))
        tid=take['id'];prefix=base+f'/element-takes/{tid}'
        assert calls==[]
        ok(client.post(prefix+'/generate'))
        try:
            assert rgb_started.wait(10),'RGB fixture did not start'
            with ThreadPoolExecutor(max_workers=8) as pool:
                duplicate_statuses=list(pool.map(lambda _:client.post(prefix+'/generate').status_code,range(20)))
            assert duplicate_statuses==[409]*20,duplicate_statuses
        finally:
            release_rgb.set()
        wait(pid,tid,'element_takes');assert calls==['rgb','alpha']
        assert len([r for r in projects.load(pid)['renders'] if r['kind']=='element'])==1
        # Repeated Generate must reuse the completed immutable take.
        ok(client.post(prefix+'/generate'));assert calls==['rgb','alpha']
        review=ok(client.post(base+'/elements/review-composite',json={'take_ids':[tid]}));wait(pid,review['id'])
        assert not projects.load(pid).get('element_approvals')
        ok(client.post(prefix+'/approve'));assert projects.load(pid)['element_approvals']=={eid:tid}
        render=ok(client.post(base+'/elements/composite'));render=wait(pid,render['id']);rd=root/'renders'/render['id']
        profile=p['native_profile']
        for i in range(2):assert np.array_equal(native_media.read_native_frame(rd/'master_frames',i),native[i])
        assert any(not np.array_equal(native_media.read_native_frame(rd/'master_frames',i),native[i]) for i in range(2,8))
        master=rd/'master.mov';assert master.is_file()
        probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(master)]))
        video=next(s for s in probe['streams'] if s['codec_type']=='video')
        assert (video['width'],video['height'],int(video['nb_frames']))==(96,64,12)
        assert video['r_frame_rate']=='24000/1001'
        assert any(s['codec_type']=='audio' for s in probe['streams'])
        subprocess.run(['ffmpeg','-v','error','-i',str(master),'-f','null','-'],check=True)
        url=base+f'/renders/{render["id"]}/result.mp4'
        assert client.get(url,headers={'Range':'bytes=0-1023'}).status_code==206
        layer=ok(client.post(base+'/layer-exports',json={'include':['elements']}))
        deadline=time.monotonic()+90
        while layer['status'] not in ('done','error') and time.monotonic()<deadline:
            time.sleep(.03)
            layer=ok(client.get(base+'/layer-exports/'+layer['id']))
        assert layer['status']=='done',layer
        archive=client.get(layer['url']);assert archive.status_code==200
        with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
            assert z.testzip() is None;manifest=json.loads(z.read('manifest.json'))
            assert manifest['frames']==12 and manifest['timing']['timestamps']==profile['timestamps']
            assert sum(name.endswith('.exr') for name in z.namelist())>=12
        assert calls==['rgb','alpha'] and (root/'native'/profile['source_file']).read_bytes()==original
    report={'installed_module':str(Path(keyfx.__file__).resolve()),'work_directory':str(work),'synthetic_provider_stages':calls,
        'new_paid_jobs':0,'outbound_network_denied':True,'source_sha256':hashlib.sha256(original).hexdigest(),
        'native_frames':12,'native_size':[96,64],'fps':'24000/1001','source_byte_preserved':True,'pre_onset_pixels_identical':True,
        'effect_changes_pixels':True,'native_master_fully_decoded':True,'audio_stream_retained':True,'bundled_ui_and_presets':True,
        'duplicate_generate_reuses_take':True,'review_does_not_approve':True,'approved_cached_composite':True,'exr_archive_verified':True,
        'concurrent_duplicate_submissions':len(duplicate_statuses),'concurrent_duplicate_statuses':duplicate_statuses,'single_generation_worker':True,
        'host_and_origin_guards':True,'platform':'macOS, Python 3.12; separate machine/Windows/Linux not tested'}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
