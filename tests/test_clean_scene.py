"""Observed clean-scene failures: source-stage captions, inferred FX and green-screen leakage."""
import json

import cv2
import numpy as np

from keyfx import analysis, media, prompts, quality


FAILED_DRAFT=('A soldier in full gear kneels on a green screen, aiming an assault rifle forward. '
              'He shifts slightly, adjusting his stance while maintaining aim. '
              'No significant camera movement occurs. '
              'The soldier remains focused, weapon steady, as if anticipating movement or threat from unseen directions.')


def test_failed_caption_does_not_reintroduce_stage_camera_or_speculative_intent():
    motion=prompts.source_motion(FAILED_DRAFT,clean_scene=True)
    assert 'kneels, aiming an assault rifle forward' in motion
    assert 'adjusting his stance' in motion
    assert not any(s in motion.lower() for s in ['green screen','camera movement','anticipating','threat'])
    # Preservation of a wardrobe colour and observable look is intentional.
    assert prompts.source_motion('A person in a green jacket looks towards the camera.')=='A person in a green jacket looks towards the camera.'


def test_clean_environment_uses_partial_roles_and_omits_action_narration():
    text=prompts.environment_prompt(FAILED_DRAFT+' He fires three rounds.','Neon city',opening_guided=True)
    assert 'kneels on a green screen' not in text and 'No significant camera movement' not in text
    assert 'He fires three rounds' not in text
    assert '<Picture 1>: partially_preserved' in text and 'foreground, ground and both side edges' in text
    assert '<Video 1>: partially_preserved' in text and 'performance and camera reference' in text
    assert 'Newly revealed areas extend this same environment' in text
    assert 'green screen' not in text and 'muzzle flashes' not in text and 'kneels' not in text
    assert text==prompts.environment_prompt('A completely different performance.','Neon city',opening_guided=True)
    assert 'keyframe completion' in text
    assert 'keyframe completion' not in prompts.environment_prompt('He turns.','Room',opening_guided=False)


def test_effect_pass_keeps_explicit_fx_action_but_omits_source_stage():
    shot='The actor stands against a green screen. He fires the rifle. The camera stays static.'
    text=prompts.effect_prompt(shot,[])
    assert 'against a green screen' not in text and 'camera stays static' not in text
    assert 'He fires the rifle' in text


def test_analysis_discourages_stage_descriptions_and_keeps_raw_evidence(tmp_path,monkeypatch):
    instruction=analysis.graph(77,'test')['analyse']['inputs']['custom_prompt']
    assert 'Omit the source background' in instruction and 'Omit camera descriptions' in instruction
    monkeypatch.setattr(analysis,'preview',lambda *a:tmp_path/'small.mp4')
    raw=tmp_path/'raw.txt';raw.write_text(FAILED_DRAFT)
    monkeypatch.setattr(analysis.comfy,'run_graph',lambda *a,**kw:[raw])
    draft=analysis.draft(tmp_path,77,tmp_path)
    assert 'green screen' not in draft and 'camera movement' not in draft
    assert (tmp_path/'action_raw.txt').read_text()==FAILED_DRAFT


def test_review_flags_reappearing_green_but_not_a_naturally_green_reference(tmp_path):
    opening=np.full((540,960,3),(60,30,30),np.uint8)
    green=opening.copy();green[250:,:]=(20,160,25)
    image=tmp_path/'opening.png';cv2.imwrite(str(image),opening)
    video=tmp_path/'test.mp4';writer=media._writer(video,960,540,True)
    for frame in [opening,green,green]:writer.stdin.write(frame.tobytes())
    writer.stdin.close();assert writer.wait()==0
    original_bytes=video.read_bytes()
    result=quality.review_scene(video,image,tmp_path/'review.json')
    assert result['frames_checked']==3 and result['suspect_frames']==[1,2] and result['warnings']
    assert 'frame 1' in result['warnings'][0]
    assert json.loads((tmp_path/'review.json').read_text())==result
    assert video.read_bytes()==original_bytes  # review does not touch output pixels/encoding
    cv2.imwrite(str(image),green)
    assert quality.review_scene(video,image,tmp_path/'green_scene.json')['warnings']==[]
