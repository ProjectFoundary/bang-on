import copy
import io
import json
import subprocess
import wave

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from keyfx import media, projects, simulation_preview
from keyfx.server import app
from test_element_pipeline import pipeline


def capture(spec):
    fields=[('metadata',(None,json.dumps({k:spec[k] for k in ('signature','quality')})))]
    for i in range(spec['frames']):
        image=Image.new('RGB',(960,540),(30+i*15,60,130))
        data=io.BytesIO();image.save(data,format='JPEG',quality=95)
        fields.append((f'frame_{i:04d}.jpg',(f'{i}.jpg',data.getvalue(),'image/jpeg')))
    return fields


def test_preview_plan_resamples_native_timestamps_and_preserves_display_aspect(pipeline):
    create,_,_=pipeline;p,_,_=create(fps='60/1')
    spec=simulation_preview.plan(p)
    assert spec['source_frames']==[0,2,5,7] and spec['frames']==4
    assert spec['duration']==pytest.approx(8/60) and spec['fps']==24
    assert spec['height']==960 and abs(spec['width']/spec['height']-18/32)<.002
    assert spec['signature']!=simulation_preview.plan(p,'draft')['signature']
    moved=copy.deepcopy(p);moved['effects'][0]['angle']=20
    assert simulation_preview.plan(moved)['signature']!=spec['signature']


def test_preview_download_encodes_all_frames_without_project_or_model_changes(pipeline,monkeypatch):
    create,_,_=pipeline;p,root,originals=create(fps='24/1')
    before=(root/'project.json').read_bytes()
    monkeypatch.setattr(projects,'start_render',lambda *a,**kw:pytest.fail('Preview cannot generate models'))
    client=TestClient(app,base_url='http://127.0.0.1')
    spec=client.get('/api/projects/shot/simulation-preview/plan').json()
    response=client.post('/api/projects/shot/simulation-preview',files=capture(spec))
    assert response.status_code==200,response.text
    result=response.json();movie=root/'simulation_previews'/result['id']/'preview.mp4'
    actual=media.probe(movie)
    assert actual['frames']==p['frames'] and actual['fps']==24
    frames=media.read_video(movie)
    assert frames[-1][0,0,2]>frames[0][0,0,2]+70,'Capture order or final frame was lost'
    assert (root/'project.json').read_bytes()==before
    download=client.get(result['url'])
    assert download.status_code==200 and download.content==movie.read_bytes()
    assert 'attachment' in download.headers['content-disposition'] and 'video/mp4' in download.headers['content-type']
    metadata=json.loads((movie.parent/'preview.json').read_text())
    assert metadata['photographic_master'] is False and metadata['source_frames']==list(range(8))
    assert client.get('/api/projects/shot/simulation-preview/not-a-version/preview.mp4').status_code==404


@pytest.mark.parametrize('failure',['stale','missing','duplicate','bad_geometry','not_object'])
def test_preview_rejects_stale_or_incomplete_capture_before_encoding(pipeline,monkeypatch,failure):
    create,_,_=pipeline;p,root,_=create(fps='24/1');spec=simulation_preview.plan(p)
    files=capture(spec);before=(root/'project.json').read_bytes()
    if failure=='stale':
        files[0]=('metadata',(None,json.dumps({'signature':'outdated','quality':'high'})))
    elif failure=='missing':files.pop()
    elif failure=='duplicate':files.append(files[-1])
    elif failure=='not_object':files[0]=('metadata',(None,'[]'))
    else:
        blob=io.BytesIO();Image.new('RGB',(100,100)).save(blob,format='JPEG')
        files[-1]=(files[-1][0],('bad.jpg',blob.getvalue(),'image/jpeg'))
    monkeypatch.setattr(simulation_preview,'_encode',lambda *a:pytest.fail('Invalid capture reached encoding'))
    response=TestClient(app,base_url='http://127.0.0.1').post('/api/projects/shot/simulation-preview',files=files)
    assert response.status_code==400,response.text
    assert (root/'project.json').read_bytes()==before
    assert not list((root/'simulation_previews').glob('*/preview.mp4'))


def test_preview_refuses_photographic_changes_during_encoding(pipeline,monkeypatch,tmp_path):
    create,_,_=pipeline;p,root,_=create(fps='24/1');spec=simulation_preview.plan(p)
    changed=copy.deepcopy(p);changed['effects'][0]['size']=2;projects.save(changed)
    monkeypatch.setattr(simulation_preview.subprocess,'run',lambda *a,**kw:pytest.fail('Stale snapshot reached encoder'))
    with pytest.raises(ValueError,match='shot changed'):
        simulation_preview._encode(p,spec,tmp_path,root/'simulation_previews'/spec['signature'])


def test_preview_audio_uses_absolute_native_trim_without_doubling_source_origin(pipeline,tmp_path):
    create,_,_=pipeline;p,root,_=create(fps='24/1')
    samples=(np.sin(np.arange(48000)*2*np.pi*1200/48000)*12000).astype('<i2')
    wav=tmp_path/'input.wav'
    with wave.open(str(wav),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(48000);f.writeframes(samples.tobytes())
    audio=root/'native'/'source.mov'
    subprocess.run(['ffmpeg','-v','error','-y','-i',str(wav),'-af','asetpts=PTS+5/TB','-c:a','pcm_s16le',
                    '-copyts','-avoid_negative_ts','disabled','-f','matroska',str(audio)],check=True)
    p['native_profile']['trim_actual_start_s']=5.25;projects.save(p)
    client=TestClient(app,base_url='http://127.0.0.1');spec=simulation_preview.plan(p)
    response=client.post('/api/projects/shot/simulation-preview',files=capture(spec))
    assert response.status_code==200,response.text
    output=root/'simulation_previews'/response.json()['id']/'preview.mp4'
    data=json.loads(subprocess.run(['ffprobe','-v','error','-select_streams','a:0','-show_streams','-of','json',str(output)],capture_output=True,text=True,check=True).stdout)
    assert data['streams'][0]['codec_name']=='aac'
    assert float(data['streams'][0]['start_time'])==pytest.approx(0,abs=.001)
    assert float(data['streams'][0]['duration'])==pytest.approx(8/24,abs=.025)
