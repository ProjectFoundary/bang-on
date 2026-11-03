"""Element jobs tested with real tiny media and fake providers; never bills or accesses accounts."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from keyfx import connections, element_graphs as graphs, elements


PROVENANCE = {'kind': 'isolated_simulation_guide', 'contains_plate': False, 'renderer': 'test-only'}


def catalog(recipe=None):
    result = {}
    for _, _, graph, _ in graphs.requirements(recipe):
        for node in graph.values():
            required = result.setdefault(node['class_type'], {'input': {'required': {}}})['input']['required']
            for key, value in node['inputs'].items():
                if key in connections._MODEL_FIELDS and isinstance(value, str):
                    values = required.setdefault(key, [[], {}])[0]
                    if value not in values:
                        values.append(value)
    return result


def guide(tmp_path, *, n=5, width=64, height=64):
    frames = []
    for i in range(n):
        frame = np.zeros((height, width, 3), np.uint8)
        frame[15:35, 20 + i:40 + i] = (120, 120, 120)
        frames.append(frame)
    path = tmp_path / 'guide.mp4'
    elements._encode(path, frames)
    return path


def fake_provider(calls, *, broken_alpha=False):
    def execute(*, graph, uploads, out_node, out_dir, on_status, idempotency_key):
        stage = 'alpha' if 'alpha-gen' in graph['lora']['inputs']['lora_name'] else 'rgb'
        calls.append((stage, idempotency_key, uploads))
        assert set(uploads) == {'control'}, 'A photograph reached the plate-free graph'
        on_status('fake-job-' + stage, 'running')
        n = graph['latent']['inputs']['length']
        w, h = graph['latent']['inputs']['width'], graph['latent']['inputs']['height']
        colour = (128, 128, 128) if stage == 'alpha' else (30, 80, 180)
        frames = [np.full((h, w, 3), colour, np.uint8) for _ in range(n - int(broken_alpha and stage == 'alpha'))]
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / 'result.mp4'
        elements._encode(path, frames)
        return [path]
    return execute


def test_graphs_are_separate_native_ltx_passes_and_alpha_has_no_prompt():
    rgb, ru, _ = graphs.graph('rgb', Path('guide.mp4'), {'active_frames': 50}, width=1024, height=576)
    alpha, au, _ = graphs.graph('alpha', Path('beauty.mp4'), {'active_frames': 50}, width=1024, height=576)
    assert rgb['latent']['inputs']['length'] == alpha['latent']['inputs']['length'] == 57
    assert rgb['lora']['inputs']['lora_name'] == graphs.DEFAULT_MODELS['fluid']
    assert alpha['lora']['inputs']['lora_name'] == graphs.DEFAULT_MODELS['alpha']
    assert alpha['pos']['inputs']['text'] == alpha['neg']['inputs']['text'] == ''
    assert alpha['guide']['inputs']['latent_downscale_factor'] == 1.0
    assert rgb['guide']['inputs']['latent_downscale_factor'] == ['lora', 1]
    assert alpha['guider']['inputs']['cfg'] == 1
    assert len(alpha['sigmas']['inputs']['sigmas'].split(',')) == 9
    assert ru == {'control': ('file', Path('guide.mp4'))} and au == {'control': ('file', Path('beauty.mp4'))}
    assert not any('MiniMax' in n['class_type'] for n in alpha.values())


def test_full_bf16_alpha_profile_changes_only_matte_models_and_cache(tmp_path):
    original=graphs.normalize_recipe({'active_frames':9})
    precise=graphs.normalize_recipe({'active_frames':9,'models':graphs.ALPHA_BF16_MODELS})
    rgb,_,_=graphs.graph('rgb',Path('guide.mp4'),precise,width=64,height=64)
    alpha,_,_=graphs.graph('alpha',Path('beauty.mp4'),precise,width=64,height=64)
    for node,field,role in [('unet','unet_name','transformer'),('clip','clip_name','text_encoder'),
                            ('vae','vae_name','video_vae'),('avae','vae_name','audio_vae')]:
        assert rgb[node]['inputs'][field]==graphs.DEFAULT_MODELS[role]
        assert alpha[node]['inputs'][field]==graphs.ALPHA_BF16_MODELS[f'alpha_{role}']
    assert alpha['pos']['inputs']['text']==alpha['neg']['inputs']['text']==''
    assert elements._profile(original,'rgb')==elements._profile(precise,'rgb')
    assert elements._profile(original,'alpha')!=elements._profile(precise,'alpha')
    def path(recipe,stage):
        return elements._stage_path({'recipe':recipe,'geometry':{'width':64,'height':64},'takes_root':str(tmp_path)},stage,'same-input',{'identity':'same-backend'})
    assert path(original,'rgb')==path(precise,'rgb')
    assert path(original,'alpha')!=path(precise,'alpha')


def test_full_fake_chain_preserves_alpha_data_and_native_timing(tmp_path):
    path = guide(tmp_path, width=66, height=62)
    take = elements.prepare(tmp_path / 'elements', 'smoke1', path,
                            {'active_frames': 5, 'start_frame': 60, 'source_fps': '30000/1001'}, provenance=PROVENANCE)
    calls = []
    done = elements.run(take['take_dir'], executor=fake_provider(calls), catalog=catalog())
    assert done['status'] == 'done' and [c[0] for c in calls] == ['rgb', 'alpha']
    assert len({c[1] for c in calls}) == 2
    assert done['geometry']['content_rect'] == [31, 1, 66, 62]
    assert done['timing']['model_frames'] == 9
    assert done['timing']['timeline_time_seconds'][0] == pytest.approx(2.002)
    a = done['assets']['alpha']
    im = cv2.imread(str(Path(a['frames_dir']) / '00000.png'), cv2.IMREAD_UNCHANGED)
    assert im.ndim == 2 and np.all(im == 128), 'Alpha was gamma-converted or capped to simulation support'
    assert a['frames'] == 5 and a['model_frames'] == 9 and a['colour_space'] == 'linear_data'
    assert done['assets']['rgb']['colour_association'] == 'black_premultiplied'
    elements.run(take['take_dir'], executor=lambda **_: pytest.fail('Completed take spent again'))
    assert elements.find_matching(tmp_path / 'elements', 'smoke1', path,
                                  {'active_frames': 5, 'start_frame': 60, 'source_fps': '30000/1001'}, provenance=PROVENANCE)['status'] == 'done'


def test_missing_alpha_blocks_beauty_before_spending(tmp_path):
    take = elements.prepare(tmp_path / 'e', 'dust', guide(tmp_path), {'active_frames': 5}, provenance=PROVENANCE)
    nodes = catalog()
    nodes['LTXICLoRALoaderModelOnly']['input']['required']['lora_name'][0].remove(graphs.DEFAULT_MODELS['alpha'])
    with pytest.raises(elements.ElementError, match='dependencies are not ready'):
        elements.run(take['take_dir'], executor=lambda **_: pytest.fail('Spent before Alpha Gen readiness'), catalog=nodes)
    assert elements.inspect(take['take_dir'])['status'] == 'prepared'


def test_alpha_profile_change_reuses_rgb_but_placement_reuses_both(tmp_path):
    root, path, calls = tmp_path / 'e', guide(tmp_path), []
    first = elements.prepare(root, 'fx', path, {'active_frames': 5}, provenance=PROVENANCE)
    elements.run(first['take_dir'], executor=fake_provider(calls), catalog=catalog())
    moved = elements.prepare(root, 'fx', path, {'active_frames': 5, 'start_frame': 30, 'holdout_version': 'new'}, provenance=PROVENANCE)
    assert moved['id'] != first['id']
    elements.run(moved['take_dir'], executor=lambda **_: pytest.fail('Post-comp changes regenerated models'), catalog=catalog())
    revised = elements.prepare(root, 'fx', path, {'active_frames': 5, 'model_revisions': {'alpha': 'new-version'}}, provenance=PROVENANCE)
    result = elements.run(revised['take_dir'], executor=fake_provider(calls), catalog=catalog())
    assert [c[0] for c in calls] == ['rgb', 'alpha', 'alpha']
    assert result['stages']['rgb']['cached']


def test_independent_alpha_seed_reuses_rgb_and_preserves_legacy_recipes(tmp_path):
    legacy = graphs.normalize_recipe({'active_frames': 5, 'seed': 42})
    assert 'alpha_seed' not in legacy
    old_graph, _, _ = graphs.graph('alpha', Path('rgb.mp4'), legacy, width=64, height=64)
    revised = {**legacy, 'alpha_seed': 1234}
    new_graph, _, _ = graphs.graph('alpha', Path('rgb.mp4'), revised, width=64, height=64)
    assert old_graph['noise']['inputs']['noise_seed'] == 42
    assert new_graph['noise']['inputs']['noise_seed'] == 1234
    assert elements._profile(legacy, 'rgb') == elements._profile(revised, 'rgb')
    root, path, calls = tmp_path / 'e', guide(tmp_path), []
    first = elements.prepare(root, 'fx', path, legacy, provenance=PROVENANCE)
    elements.run(first['take_dir'], executor=fake_provider(calls), catalog=catalog())
    retry = elements.prepare(root, 'fx', path, revised, provenance=PROVENANCE)
    result = elements.run(retry['take_dir'], executor=fake_provider(calls), catalog=catalog(revised))
    assert result['status'] == 'done' and result['stages']['rgb']['cached']
    assert [c[0] for c in calls] == ['rgb', 'alpha', 'alpha']


@pytest.mark.parametrize('seed', [True, -1, 1.5, '1234', 2**63])
def test_invalid_alpha_seed_rejected_before_spend(seed):
    with pytest.raises(ValueError, match='AlphaGen seed'):
        graphs.normalize_recipe({'alpha_seed': seed})


def test_ambiguous_submission_is_never_automatically_retried(tmp_path):
    take = elements.prepare(tmp_path / 'e', 'fx', guide(tmp_path), {'active_frames': 5}, provenance=PROVENANCE)
    calls = []
    def lost(**kw):
        calls.append(kw['idempotency_key'])
        raise TimeoutError('The POST response disappeared')
    with pytest.raises(elements.ElementError):
        elements.run(take['take_dir'], executor=lost, catalog=catalog())
    assert elements.inspect(take['take_dir'])['status'] == 'uncertain'
    with pytest.raises(elements.AlreadySubmitted):
        elements.run(take['take_dir'], executor=lost, catalog=catalog())
    assert len(calls) == 1


def test_wrong_alpha_frame_count_and_mutated_cached_frames_are_rejected(tmp_path):
    path = guide(tmp_path)
    take = elements.prepare(tmp_path / 'bad', 'fx', path, {'active_frames': 5}, provenance=PROVENANCE)
    with pytest.raises(elements.ElementError):
        elements.run(take['take_dir'], executor=fake_provider([], broken_alpha=True), catalog=catalog())
    assert elements.inspect(take['take_dir'])['status'] == 'failed'
    with pytest.raises(elements.AlreadySubmitted):
        elements.run(take['take_dir'], executor=fake_provider([]), catalog=catalog())
    good = elements.prepare(tmp_path / 'good', 'fx', path, {'active_frames': 5}, provenance=PROVENANCE)
    done = elements.run(good['take_dir'], executor=fake_provider([]), catalog=catalog())
    alpha = Path(done['assets']['alpha']['frames_dir']) / '00000.png'
    cv2.imwrite(str(alpha), np.zeros((64, 64), np.uint8))
    with pytest.raises(elements.ElementError, match='cached decoded element frame changed'):
        elements.run(good['take_dir'], executor=lambda **_: pytest.fail('Corruption caused a paid retry'))


def test_prepare_rejects_plate_egress_and_long_guides(tmp_path):
    path = guide(tmp_path)
    with pytest.raises(ValueError, match='without photographed plate pixels'):
        elements.prepare(tmp_path / 'e', 'fx', path, {'active_frames': 5}, provenance={'kind': 'plate'})
    with pytest.raises(ValueError, match='121'):
        graphs.normalize_recipe({'active_frames': 122})
