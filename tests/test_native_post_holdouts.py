"""Native optical masks stay separate from immutable RGB/alpha and simulation."""
import copy
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from keyfx import element_jobs, elements, geometry, native_colour, native_media, people_alpha, post_holdouts, projects
from test_element_pipeline import generate, pipeline, png


def mask_object(root, images, *, mid='actor', role='holdout', post=True):
    directory = root/'test_masks'/mid; directory.mkdir(parents=True)
    for index, image in enumerate(images): assert cv2.imwrite(str(directory/f'{index:05d}.png'), image)
    h, w = images[0].shape
    return {'id': mid, 'name': mid, 'directory': str(directory.relative_to(root)), 'frames': len(images),
            'width': w, 'height': h, 'static': False, 'kind': 'soft', 'role': role, 'colliderBlur': 8,
            'thickness': .2, 'softRadius': 8, 'drag': 24, 'depthOffset': 0,
            'sha256': hashlib.sha256(b''.join((directory/f'{f:05d}.png').read_bytes() for f in range(len(images)))).hexdigest(),
            'holdoutPost': post}


def test_hq_fringe_is_read_at_native_resolution_and_never_clipped_to_coarse_collider(pipeline, monkeypatch):
    create, _, _ = pipeline; p, root, _ = create()
    h, w = 17, 303; p['native_profile'].update(width=w, height=h)
    coarse = np.zeros((32, 18), np.uint8); coarse[8:24, 7:10] = 255
    matte = mask_object(root, [coarse]*p['frames'], role='both')
    fine = np.zeros((h, w), np.uint16); fine[3, 299] = 65535; fine[4, 300] = 32768; fine[5, 301] = 1
    directory = root/'fine'; directory.mkdir(); path = directory/'00000.png'; cv2.imwrite(str(path), fine)
    matte['holdout'] = {'take_id': 'reviewed-hq'}
    p['geometry']['mattes'] = [matte]; p['effects'][0]['element']['colliders'] = [matte['id']]
    calls = []
    def asset(pid, tid, kind, frame):
        calls.append((pid, tid, kind, frame)); return path
    monkeypatch.setattr(people_alpha, 'asset_path', asset)
    monkeypatch.setattr(post_holdouts, 'camera_matrices', lambda *a: pytest.fail('A tracked native HQ mask must not receive a second camera transform'))
    holdouts = post_holdouts.NativePostHoldouts(p, p['effects'][0], p['native_profile'])
    visibility = holdouts.visibility(5)
    assert calls == [('shot', 'reviewed-hq', 'frame', 5)] and visibility.shape == (h, w)
    assert visibility[3, 299] == 0 and visibility[3, 298] == 1
    assert visibility[4, 300] == pytest.approx(1-32768/65535)
    assert visibility[5, 301] == pytest.approx(1-1/65535, abs=1e-7), 'Native opacity precision was reduced to an 8-bit/model-grid mask'
    assert np.count_nonzero(visibility != 1) == 3, 'A single-pixel fringe was spread or lost by a low-resolution grid'


@pytest.mark.parametrize('bits,transfer,primaries', [(8, 'bt709', 'bt709'), (16, 'smpte2084', 'bt2020')])
def test_native_post_alpha_is_applied_once_reuses_black_backed_model_cache_and_preserves_pixels(pipeline, monkeypatch, bits, transfer, primaries):
    create, exports, _ = pipeline; p, root, originals = create(fps='60/1', transfer=transfer, primaries=primaries)
    if bits == 8:
        originals = [np.uint8(np.floor(im/257+.5)) for im in originals]
        p['native_profile'].update(storage_bit_depth=8, bit_depth=8)
        for frame, image in enumerate(originals): native_media.write_native_frame(root/'native/frames', frame, image)
        projects.save(p)
    row, calls = generate(p, monkeypatch)
    frozen = copy.deepcopy(projects.load('shot')['element_takes'])
    before = {f.name: f.read_bytes() for f in (root/'native/frames').glob('*.png')}
    masks = []
    fractions = [0, .1, .3, .6, .9, .2, .7, 1]
    for value in fractions:
        mask = np.zeros((32, 18), np.uint16); mask[15, 6] = round(value*65535); mask[15, 7] = 65535; masks.append(mask)
    matte = mask_object(root, masks)
    current = projects.load('shot'); current['geometry']['mattes'] = [matte]
    current['effects'][0]['element']['colliders'] = [matte['id']]
    assert element_jobs.generation_signature(current, current['effects'][0]) == row['generation_signature']
    projects.save(current)
    assert element_jobs.start('shot', row['id'])['status'] == 'done'
    result = element_jobs.composite('shot'); saved = projects.load('shot')['renders'][0]
    assert saved['status'] == 'done', saved
    assert calls == ['rgb', 'alpha'] and projects.load('shot')['element_takes'] == frozen
    assert exports[-1]['profile'] == current['native_profile']
    frames = exports[-1]['frames']; take = elements.inspect(row['take_dir']); x, y, w, h = take['geometry']['content_rect']
    for frame in (2, 3):
        k = element_jobs.model_index(row['plan'], current['native_profile']['timestamps'][frame])
        assert k == 0, 'The two native frames should reuse one 24fps model frame'
        rgb = cv2.cvtColor(cv2.imread(str(Path(take['assets']['rgb']['frames_dir'])/f'{k:05d}.png'))[y:y+h, x:x+w], cv2.COLOR_BGR2RGB)/255
        alpha = cv2.imread(str(Path(take['assets']['alpha']['frames_dir'])/f'{k:05d}.png'), 0)[y:y+h, x:x+w]/255
        geometric = cv2.imread(str(root/row['inputs']/'visibility'/f'{k:05d}.png'), 0)/255
        optical = 1-masks[frame].astype(np.float64)/65535
        radiance = native_colour.convert_primaries(native_colour.srgb_to_linear(rgb)*geometric[..., None], 'bt709', primaries)
        original_linear = native_colour.to_linear(originals[frame], current['native_profile'])
        expected = native_colour.from_linear(original_linear*(1-alpha*geometric*optical)[..., None]+radiance*optical[..., None], current['native_profile'], originals[frame].dtype)
        assert np.max(np.abs(frames[frame].astype(int)-expected.astype(int))) <= 1
        squared = native_colour.from_linear(original_linear*(1-alpha*geometric*optical*optical)[..., None]+radiance*(optical*optical)[..., None], current['native_profile'], originals[frame].dtype)
        assert np.any(np.abs(frames[frame][15, 6].astype(int)-squared[15, 6].astype(int)) > 1), 'The fixture must detect accidental squaring of the optical alpha'
        assert np.array_equal(frames[frame][15, 7], originals[frame][15, 7]), 'Full native holdout must preserve exact photographic pixels'
    assert np.array_equal(frames[0], originals[0]) and np.array_equal(frames[1], originals[1])
    assert {f.name: f.read_bytes() for f in (root/'native/frames').glob('*.png')} == before


def test_only_physical_changes_invalidate_generation_and_hq_replacement_is_post_only(pipeline):
    create, _, _ = pipeline; p, root, _ = create(); effect = p['effects'][0]
    image = np.zeros((32, 18), np.uint8); image[8:24, 7:10] = 255
    physical = mask_object(root, [image]*p['frames'], role='both', post=False)
    physical.pop('holdoutPost'); p['geometry']['mattes'] = [physical]; effect['element']['colliders'] = [physical['id']]
    before = element_jobs.generation_signature(p, effect)
    physical.update(holdoutPost=True, holdoutBaseline='original', holdout={'take_id': 'hq-one', 'sha256': 'one'})
    converted = element_jobs.generation_signature(p, effect)
    assert converted != before, 'Moving an old combined optical/collider mask into post requires one guide refresh'
    physical['holdout'] = {'take_id': 'hq-two', 'sha256': 'two', 'source_signature': 'new-fine-alpha'}
    assert element_jobs.generation_signature(p, effect) == converted
    physical.pop('holdout')
    assert element_jobs.generation_signature(p, effect) == converted, 'Reverting post to the original optical mask must reuse model RGB/alpha'
    optical = mask_object(root, [image]*p['frames'], mid='new-optical')
    optical.update(holdoutBaseline='none', holdout={'take_id': 'hq-three'})
    p['geometry']['mattes'].append(optical); effect['element']['colliders'].append(optical['id'])
    assert element_jobs.generation_signature(p, effect) == converted
    physical['colliderBlur'] += 1
    assert element_jobs.generation_signature(p, effect) != converted


def test_captured_source_depth_overrides_legacy_estimate_for_native_3d_gate(pipeline, monkeypatch):
    create, _, _ = pipeline; p, root, _ = create()
    original = np.zeros((32, 18), np.uint8); original[8:24, 7:10] = 255
    matte = mask_object(root, [original]*p['frames'], role='both')
    depth = np.full((32, 18), 65535, np.uint16); depth[8:24, 7:10] = 16384
    p['geometry'].update(collision_mode='depth3d', depth=geometry.import_depth(root, png(depth), kind='distance'), depth_scale=4, depth_bias=0, mattes=[matte])
    effect = p['effects'][0]; effect['element']['colliders'] = [matte['id']]
    for key in effect['keys']:
        if key['phase'] == 'onset': key.update(x=.85, y=.5)
    track = root/'native_camera.json'; track.write_text(json.dumps({'w': 18, 'h': 32, 'H': [np.eye(3).ravel().tolist()]*p['frames']}))
    monkeypatch.setattr(projects, 'track_file', lambda _: track)
    legacy = post_holdouts.NativePostHoldouts(p, effect, p['native_profile'])
    captured = post_holdouts.NativePostHoldouts(p, effect, p['native_profile'], source_depth=.4)
    assert legacy.depths[matte['id']] == pytest.approx(1, abs=.001) and legacy.source_depth > 1
    assert legacy.visibility(2)[15, 8] == 0
    assert captured.source_depth == .4 and captured.visibility(2)[15, 8] == 1, 'Foreground FX must not be incorrectly erased by the actor behind it'
