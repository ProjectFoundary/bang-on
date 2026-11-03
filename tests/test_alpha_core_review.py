"""Real local review encoding with frozen synthetic alpha; no provider calls."""
import copy
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from keyfx import alpha_core, alpha_core_review, elements, media


@pytest.fixture
def frozen(tmp_path):
    root = tmp_path/'project'; root.mkdir()
    rd = root/'element_inputs'/'input1'; gd = rd/'guide_alpha'; gd.mkdir(parents=True)
    fd = root/'elements'/'frames'; fd.mkdir(parents=True)
    width, height, n = 56, 60, 5
    hashes = {}; model_hashes = {}; videos = []
    for i in range(n):
        guide = np.zeros((height,width),np.uint8)
        guide[10:50,10+i:45+i] = 255
        path = gd/f'{i:05d}.png'; assert cv2.imwrite(str(path),guide)
        hashes[f'guide_alpha/{i:05d}.png'] = elements._sha(path)
        model = np.zeros((64,64),np.uint8)
        model[12:52,14+i:49+i] = 128
        model[17:47,19+i:44+i] = 0  # dense interior wrongly absent from AlphaGen
        path = fd/f'{i:05d}.png'; assert cv2.imwrite(str(path),model)
        model_hashes[str(path.resolve())] = elements._sha(path)
        videos.append(np.repeat(model[...,None],3,axis=2))
    manifest = rd/'manifest.json'; manifest.write_text(json.dumps({'files':hashes}))
    fm = fd.parent/'frames.json'; fm.write_text(json.dumps(model_hashes))
    raw = fd.parent/'raw.mp4'; elements._encode(raw,videos)
    mh = elements._sha(manifest)
    asset = {'path':str(raw),'sha256':elements._sha(raw),'frames_dir':str(fd),
             'frame_manifest':str(fm),'frame_manifest_sha256':elements._sha(fm)}
    row = {'id':'take1','request_sha256':'a'*64,'inputs':'element_inputs/input1','post_manifest_sha256':mh,
           'plan':{'active_frames':n,'width':width,'height':height,'alpha_core':alpha_core.policy()}}
    take = {'geometry':{'width':64,'height':64,'content_rect':[4,2,width,height]},
            'assets':{'alpha':asset},'provenance':{'post_manifest_sha256':mh}}
    return root,row,take


def test_effective_movie_has_exact_active_timing_geometry_and_retains_raw(frozen):
    root,row,take = frozen
    original = {str(p):elements._sha(p) for p in root.rglob('*') if p.is_file()}
    review = alpha_core_review.build(root,row,take)
    assert review['frames']==5 and review['fps']==24 and review['width']==review['height']==64
    assert review['view_only'] and review['model_alpha_unchanged'] and not review['cached']
    path = alpha_core_review.validate_movie(root,row,take,review['signature'])
    actual = media.probe(path)
    assert actual['frames']==5 and actual['fps']==24 and actual['width']==actual['height']==64
    frames,_ = elements._read_video(path)
    assert frames[0][30,30,0]>250, 'Viewing copy failed to show the same recovered dense core'
    assert frames[0][5,5,0]<=1, 'Guide created alpha outside the element'
    assert all(elements._sha(Path(p))==sha for p,sha in original.items())
    again = alpha_core_review.build(root,row,take)
    assert again['cached'] and again['signature']==review['signature']
    assert again['video_sha256']==review['video_sha256']


def test_corrupt_cached_viewing_copy_is_rejected(frozen):
    root,row,take = frozen
    review = alpha_core_review.build(root,row,take)
    Path(review['path']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='review changed'):
        alpha_core_review.validate_movie(root,row,take,review['signature'])


def test_frozen_guide_tamper_is_rejected_before_encode(frozen,monkeypatch):
    root,row,take = frozen
    path = root/row['inputs']/'guide_alpha'/'00001.png'
    path.write_bytes(b'changed')
    monkeypatch.setattr(alpha_core_review.subprocess,'run',lambda *a,**k:pytest.fail('Tampered alpha reached encoder'))
    with pytest.raises(ValueError,match='simulation-alpha frame changed'):
        alpha_core_review.build(root,row,take)
    assert not list((root/'element_alpha_reviews').rglob('effective-alpha.mp4'))


def test_decoded_model_tamper_is_rejected_before_encode(frozen,monkeypatch):
    root,row,take = frozen
    (Path(take['assets']['alpha']['frames_dir'])/'00002.png').write_bytes(b'changed')
    monkeypatch.setattr(alpha_core_review.subprocess,'run',lambda *a,**k:pytest.fail('Tampered model reached encoder'))
    with pytest.raises(ValueError,match='decoded AlphaGen frame changed'):
        alpha_core_review.build(root,row,take)


def test_old_take_without_simulation_alpha_is_never_keyed_from_rgb(frozen):
    root,row,take = frozen
    row = copy.deepcopy(row); row['plan'].pop('alpha_core')
    with pytest.raises(ValueError,match='no frozen explosion-core guide'):
        alpha_core_review.build(root,row,take)


def test_wrong_version_and_changed_manifest_are_rejected(frozen):
    root,row,take = frozen
    with pytest.raises(ValueError,match='different inputs'):
        alpha_core_review.validate_movie(root,row,take,'0'*64)
    (root/row['inputs']/'manifest.json').write_text('{}')
    with pytest.raises(ValueError,match='manifest changed'):
        alpha_core_review.build(root,row,take)
