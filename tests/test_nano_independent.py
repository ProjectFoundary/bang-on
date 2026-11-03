"""The restored Nano path edits current frames independently, without history inputs."""
import cv2
import numpy as np

from keyfx import media, polish, presets, projects


def test_nano_keys_send_only_the_current_image(tmp_path, monkeypatch):
    frames=tmp_path/'frames';frames.mkdir()
    for f in range(30):cv2.imwrite(str(frames/f'{f:05d}.png'),np.full((540,960,3),f*3,np.uint8))
    effect=presets.new_effect('explosion',2,.15,.65,'blast',30)
    seen=[]
    def partner(recipe,node,source,outnode,out,name):
        assert recipe['35']['inputs']['images']==[node,0]
        assert not any('batch' in n or 'effect_ref' in n for n in recipe)
        f=recipe['35']['inputs']['seed']-42
        # The far-right source detail identifies this frame, not a previous output.
        assert np.array_equal(cv2.imread(str(source))[0,-1],np.full(3,f*3,np.uint8))
        out.mkdir(parents=True,exist_ok=True);result=out/'output.png'
        cv2.imwrite(str(result),cv2.imread(str(source)))
        seen.append(f);return [result]
    monkeypatch.setattr(projects.comfy,'run_partner_graph',partner)
    result=polish.polish_keys(frames,[effect],tmp_path/'keys',42,tmp_path/'cache',30,model='nano')
    assert len(seen)>=3 and len(set(seen))==len(seen)
    assert all(path.is_file() for f,path,hard in result)
    assert not (tmp_path/'keys/continuity.json').exists()


def test_effect_generation_does_not_require_key_approval(tmp_path,monkeypatch):
    monkeypatch.setattr(projects,'DATA',tmp_path)
    p=dict(id='restored',mode='plate',frames=3,source={},shot='A controlled explosion',renders=[],flashes=[],
           effects=[presets.new_effect('explosion',1,.2,.6,'blast',3)],settings=dict(seed=42,polish=True),
           image_models=dict(polish='nano'))
    d=projects.pdir(p['id']);fd=d/'frames/plate';fd.mkdir(parents=True)
    for f in range(3):cv2.imwrite(str(fd/f'{f:05d}.png'),np.zeros((540,960,3),np.uint8))
    projects.save(p)
    r=projects.start_render(p['id'],'effect',_background=False)
    assert r['kind']=='effect' and 'guide_review_required' not in r and 'approved_keys' not in r
