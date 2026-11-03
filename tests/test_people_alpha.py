"""Raw-plate AlphaGen lane tested with local videos and a fake executor only."""
import copy
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from keyfx import connections, element_graphs, elements, people_alpha, projects
from test_element_pipeline import catalogue, pipeline


def alpha_catalog(p):
    graph = people_alpha._graph(Path('source.mp4'), people_alpha._recipe(p), width=64, height=64)
    return catalogue([graph])


def execute(calls, *, colour=False, wrong_length=False):
    def provider(*, graph, uploads, out_dir, on_status, idempotency_key, **_):
        calls.append({'graph': copy.deepcopy(graph), 'uploads': uploads, 'key': idempotency_key})
        assert set(uploads) == {'control'}
        assert graph['pos']['inputs']['text'] == graph['neg']['inputs']['text'] == ''
        assert graph['lora']['inputs']['lora_name'] == element_graphs.DEFAULT_MODELS['alpha']
        assert graph['guide']['inputs']['latent_downscale_factor'] == 1
        assert graph['save']['class_type'] == 'SaveImage' and 'video' not in graph
        dims = graph['latent']['inputs']; count = dims['length']-int(wrong_length)
        out_dir.mkdir(parents=True, exist_ok=True); files = []
        for index in range(count):
            edge = np.array([1, 7, 17, 31, 63, 127, 191, 239, 254], np.uint8)
            values = edge[np.arange(dims['width']) % len(edge)]
            im = np.broadcast_to(values[None, :, None], (dims['height'], dims['width'], 3)).copy()
            if colour: im[..., 0] = 0
            path = out_dir/f'alpha_{index:05d}.png'; cv2.imwrite(str(path), im); files.append(path)
        on_status('offline-alpha-job', 'running')
        return files
    return provider


def test_people_alpha_accepts_raw_plate_without_weakening_fx_plate_free_guard(pipeline):
    create, _, cfg = pipeline; p, root, _ = create()
    p['settings']['element_models'] = {'fluid': 'irrelevant-missing-fluid.safetensors'}; projects.save(p)
    original = {path.name: path.read_bytes() for path in (root/'frames'/'plate').glob('*.png')}
    row = people_alpha.prepare('shot', connection=cfg)
    request = elements._load(root/row['take_dir']/'request.json')
    assert request['provenance']['contains_plate'] is True
    assert request['provenance']['kind'] == 'people_alpha_source'
    assert request['provenance']['uses_sam2_mask'] is False and request['provenance']['requires_review']
    assert people_alpha.capabilities(p, connection=cfg, catalog=alpha_catalog(p))['ready']
    graph = elements._load(root/row['take_dir']/'graph.json')
    assert 'irrelevant-missing-fluid.safetensors' not in json.dumps(graph)
    assert (root/row['take_dir']/'inputs'/'source.mp4').is_file()
    assert p['renders'] == [] and not projects.load('shot').get('geometry', {}).get('holdout')
    assert {path.name: path.read_bytes() for path in (root/'frames'/'plate').glob('*.png')} == original
    with pytest.raises(ValueError, match='isolated_simulation_guide'):
        elements.prepare(root/'not-fx', 'actor', root/row['take_dir']/'inputs'/'source.mp4', {},
                         provenance={'kind': 'people_alpha_source', 'contains_plate': True})


def test_alpha_only_run_preserves_source_and_maps_every_native_frame_without_sam2_cap(pipeline):
    create, _, cfg = pipeline; p, root, originals = create(fps='60/1')
    p['geometry']['mattes'] = [{'id': 'coarse', 'directory': 'must-not-be-read', 'kind': 'soft'}]
    projects.save(p)
    row = people_alpha.prepare('shot', connection=cfg); calls = []
    people_alpha.start('shot', row['id'], connection=cfg, catalog=alpha_catalog(p), executor=execute(calls))
    p = projects.load('shot'); saved = p['people_alpha_takes'][0]
    assert saved['status'] == 'done' and p['renders'][0]['status'] == 'done'
    assert len(calls) == 1 and 'alpha-gen' in calls[0]['graph']['lora']['inputs']['lora_name']
    asset = people_alpha.validated_asset('shot', row['id'])
    assert asset['frames'] == 8 and asset['width'] == 18 and asset['height'] == 32
    assert asset['source_signature'] == row['signature'] and asset['model_profile_hash']
    assert asset['requires_review'] and asset['selection'] == 'model_foreground'
    assert p['geometry']['mattes'] == [{'id': 'coarse', 'directory': 'must-not-be-read', 'kind': 'soft'}]
    mask_dir = root/asset['directory']; assert len(list(mask_dir.glob('*.png'))) == p['frames']
    for frame in range(p['frames']):
        image = cv2.imread(str(mask_dir/f'{frame:05d}.png'), 0)
        assert image.shape == (32, 18) and image.min() > 0, 'AlphaGen must not be clipped to the SAM2 silhouette'
        assert set(np.unique(image)) == {1, 7, 17, 31, 63, 127, 191, 239, 254}, 'Soft-edge opacity bytes changed after lossless model PNG decode'
        assert np.array_equal(originals[frame], cv2.cvtColor(cv2.imread(str(root/'native'/'frames'/f'{frame:05d}.png'), cv2.IMREAD_UNCHANGED), cv2.COLOR_BGR2RGB))
    assert row['plan']['native_to_model'][0] == 0
    assert len(set(row['plan']['native_to_model'])) < p['frames'], '60fps native output must declare repeated24fps alpha samples'
    assert people_alpha.asset_path('shot', row['id'], 'frame', 7) == mask_dir/'00007.png'
    assert people_alpha.asset_path('shot', row['id'], 'alpha').is_file()
    assert people_alpha.asset_path('shot', row['id'], 'source').is_file()
    with pytest.raises(ValueError, match='outside'):
        people_alpha.asset_path('shot', row['id'], 'frame', 8)


def test_full_shot_window_preserves_vfr_timestamps_and_never_truncates_long_shots(pipeline):
    create, _, _ = pipeline; p, _, _ = create()
    p['native_profile'].update(timestamps=[0., .01, .04, .09, .10, .15, .18, .22], duration=.26, is_vfr=True)
    spec = people_alpha.plan(p)
    assert spec['start_frame'] == 0 and spec['end_frame'] == 7
    assert spec['source_times'] == p['native_profile']['timestamps']
    assert spec['source_pts'] == p['native_profile']['source_pts']
    assert spec['active_frames'] == 7 and spec['model_frames'] == 9
    assert spec['native_to_model'] == [0, 0, 1, 2, 2, 4, 4, 5]
    p['native_profile']['duration'] = 5.01
    with pytest.raises(ValueError, match='no frames were silently truncated'):
        people_alpha.plan(p)


def test_missing_alpha_stops_before_any_upload_and_does_not_require_fluid(pipeline):
    create, _, cfg = pipeline; p, _, _ = create()
    row = people_alpha.prepare('shot', connection=cfg)
    nodes = alpha_catalog(p); nodes['LTXICLoRALoaderModelOnly']['input']['required']['lora_name'][0].clear()
    assert people_alpha.capabilities(p, connection=cfg, catalog=nodes)['ready'] is False
    calls = []
    people_alpha.start('shot', row['id'], connection=cfg, catalog=nodes, executor=execute(calls))
    current = projects.load('shot')
    assert calls == [] and current['renders'][0]['status'] == 'error'
    assert current['people_alpha_takes'][0]['status'] == 'prepared'
    assert 'No upload or generation' in current['people_alpha_takes'][0]['error']
    assert not (projects.pdir('shot')/row['take_dir']/'submission.json').exists()
    people_alpha.start('shot', row['id'], connection=cfg, catalog=alpha_catalog(p), executor=execute(calls))
    completed = projects.load('shot')['people_alpha_takes'][0]
    assert completed['status'] == 'done' and 'error' not in completed and len(calls) == 1


def test_frozen_request_input_backend_and_completed_masks_are_verified(pipeline):
    create, _, cfg = pipeline; p, root, _ = create()
    row = people_alpha.prepare('shot', connection=cfg)
    assert people_alpha.prepare('shot', connection=cfg)['id'] == row['id']
    other = connections.Connection(mode='local', local_url='http://127.0.0.1:9999')
    with pytest.raises(ValueError, match='account/target changed'):
        people_alpha.start('shot', row['id'], connection=other, catalog=alpha_catalog(p), executor=execute([]))
    td = root/row['take_dir']; original = (td/'inputs'/'source.mp4').read_bytes()
    (td/'inputs'/'source.mp4').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='input changed'):
        people_alpha.start('shot', row['id'], connection=cfg, catalog=alpha_catalog(p), executor=execute([]))
    (td/'inputs'/'source.mp4').write_bytes(original)
    calls = []; people_alpha.start('shot', row['id'], connection=cfg, catalog=alpha_catalog(p), executor=execute(calls))
    assert people_alpha.start('shot', row['id'])['status'] == 'done' and len(calls) == 1
    path = people_alpha.asset_path('shot', row['id'], 'frame', 0)
    image = cv2.imread(str(path), 0); image[0, 0] = 255; cv2.imwrite(str(path), image)
    with pytest.raises(ValueError, match='matte frame changed'):
        people_alpha.validated_asset('shot', row['id'])
    with pytest.raises(ValueError, match='matte frame changed'):
        people_alpha.run(td)
    with pytest.raises(ValueError, match='review asset changed'):
        people_alpha.asset_path('shot', row['id'], 'frame', 0)


def test_all_native_source_frames_are_frozen_including_unsampled_high_fps_frames(pipeline):
    create, _, cfg = pipeline; p, root, _ = create(fps='60/1')
    row = people_alpha.prepare('shot', connection=cfg)
    request = elements._load(root/row['take_dir']/'request.json')
    assert set(request['provenance']['source_frames_sha256']) == {str(f) for f in range(p['frames'])}
    unsampled = next(f for f in range(p['frames']) if f not in row['plan']['source_frames'])
    path = root/'frames'/'plate'/f'{unsampled:05d}.png'; original = path.read_bytes()
    image = cv2.imread(str(path)); image[0, 0] = 255; cv2.imwrite(str(path), image)
    with pytest.raises(ValueError, match='Source photography changed'):
        people_alpha.start('shot', row['id'], connection=cfg, catalog=alpha_catalog(p), executor=execute([]))
    path.write_bytes(original)
    people_alpha.start('shot', row['id'], connection=cfg, catalog=alpha_catalog(p), executor=execute([]))
    image = cv2.imread(str(path)); image[0, 0] = 255; cv2.imwrite(str(path), image)
    with pytest.raises(ValueError, match='Source photography changed'):
        people_alpha.validated_asset('shot', row['id'])
    path.write_bytes(original)
    path = root/'native'/'frames'/f'{unsampled:05d}.png'
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED); image[0, 0] = 65535; cv2.imwrite(str(path), image)
    with pytest.raises(ValueError, match='Native source photography changed'):
        people_alpha.validated_asset('shot', row['id'])
    fresh = people_alpha.prepare('shot', connection=cfg)
    assert fresh['id'] != row['id'], 'A native-source edit must not reuse a prepared version from different photography'


def test_uncertain_submission_keeps_receipt_and_never_retries_or_exposes_provider_error(pipeline):
    create, _, cfg = pipeline; p, root, _ = create()
    row = people_alpha.prepare('shot', connection=cfg); td = root/row['take_dir']; calls = []
    def provider(**kw):
        calls.append(kw['idempotency_key'])
        assert elements._load(td/'submission.json')['idempotency_key'] == calls[-1]
        assert (td/'claim').is_file()
        raise RuntimeError('private-provider-response-must-not-be-public')
    people_alpha.start('shot', row['id'], connection=cfg, catalog=alpha_catalog(p), executor=provider)
    assert projects.load('shot')['people_alpha_takes'][0]['status'] == 'uncertain'
    assert elements._load(td/'submission.json')['status'] == 'uncertain'
    assert 'private-provider-response' not in json.dumps(projects.load('shot'))
    with pytest.raises(elements.AlreadySubmitted):
        people_alpha.run(td, connection=cfg, catalog=alpha_catalog(p), executor=provider)
    assert len(calls) == 1


@pytest.mark.parametrize('directory', ['raw_model', 'model_masks'])
def test_completed_raw_and_decoded_lossless_png_provenance_cannot_change(pipeline, directory):
    create, _, cfg = pipeline; p, root, _ = create(); row = people_alpha.prepare('shot', connection=cfg)
    people_alpha.start('shot', row['id'], connection=cfg, catalog=alpha_catalog(p), executor=execute([]))
    path = root/row['take_dir']/directory/'00000.png'; path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='PNG changed'):
        people_alpha.validated_asset('shot', row['id'])


@pytest.mark.parametrize('colour,wrong_length', [(True, False), (False, True)])
def test_bad_alpha_output_is_retained_and_never_automatically_retried(pipeline, colour, wrong_length):
    create, _, cfg = pipeline; p, root, _ = create(); row = people_alpha.prepare('shot', connection=cfg)
    calls = []; people_alpha.start('shot', row['id'], connection=cfg, catalog=alpha_catalog(p), executor=execute(calls, colour=colour, wrong_length=wrong_length))
    saved = projects.load('shot')['people_alpha_takes'][0]
    assert saved['status'] == 'failed' and len(calls) == 1
    assert (root/row['take_dir']/'submission.json').is_file()
    with pytest.raises(ValueError, match='already ran'):
        people_alpha.start('shot', row['id'], connection=cfg, catalog=alpha_catalog(p), executor=execute(calls))
    with pytest.raises(elements.AlreadySubmitted):
        people_alpha.run(root/row['take_dir'], connection=cfg, catalog=alpha_catalog(p), executor=execute(calls))
    assert len(calls) == 1
    fresh = people_alpha.prepare('shot', seed=43, connection=cfg)
    assert fresh['id'] != row['id'] and len(projects.load('shot')['people_alpha_takes']) == 2
