"""Native source precision/timing/colour and actual ffmpeg round-trip evidence."""
from fractions import Fraction
from pathlib import Path
import hashlib
import json
import subprocess
import wave

import cv2
import numpy as np
import pytest

from keyfx import native_media as media
from keyfx import native_colour as colour


@pytest.mark.parametrize('bits',[8,16])
@pytest.mark.parametrize('transfer',['bt709','srgb','linear','gamma22','gamma28','smpte2084'])
def test_display_lookup_matches_full_transform_for_every_integer_code(bits,transfer):
    dtype=np.uint8 if bits==8 else np.uint16
    codes=np.arange(1<<bits,dtype=np.uint32).astype(dtype)
    image=np.stack([codes,np.roll(codes,71),np.roll(codes,137)],axis=-1).reshape(-1,256,3)
    profile={'colour':{'transfer':transfer,'primaries':'bt709','linear_reference_nits':100}}
    reference=colour.to_linear(image,profile)
    if transfer=='smpte2084':reference=np.maximum(reference,0)/(1+np.maximum(reference,0))
    expected=colour.from_linear(reference,{'transfer':'srgb','primaries':'bt709'},np.uint8)
    assert np.array_equal(colour.display_frame(image,profile),expected)


def test_hlg_display_retains_rgb_coupled_luminance_transform():
    image=np.array([[[28000,3000,1000],[28000,60000,62000]]],np.uint16)
    profile={'colour':{'transfer':'arib-std-b67','primaries':'bt709','hlg_peak_nits':1000}}
    reference=colour.to_linear(image,profile);reference=np.maximum(reference,0)/(1+np.maximum(reference,0))
    expected=colour.from_linear(reference,{'transfer':'srgb','primaries':'bt709'},np.uint8)
    assert expected[0,0,0]!=expected[0,1,0], 'HLG source luminance must couple the RGB channels'
    assert np.array_equal(colour.display_frame(image,profile),expected)


def ffmpeg(*args):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *map(str, args)], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def pattern(folder: Path, n=4, size=(259, 35)):
    folder.mkdir()
    width, height = size
    ramp = np.linspace(1000, 64000, width).astype(np.uint16)
    rgb = np.stack([np.tile(ramp, (height, 1)), np.tile(ramp[::-1], (height, 1)),
                    np.full((height, width), 33000, np.uint16)], axis=-1)
    for i in range(n): media.write_native_frame(folder, i, rgb + i * 15)
    return folder


def rgb10_source(tmp_path):
    images = pattern(tmp_path / 'input')
    source = tmp_path / 'source.nut'
    ffmpeg('-framerate', '24000/1001', '-i', images / '%05d.png', '-frames:v', '4',
           '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'gbrp10le', source)
    return source


def yuv10_source(tmp_path, *, y_values=(64, 512, 940), u=512, v=512, range_='tv', transfer='bt709'):
    """Independent source-code fixture; declare input and output tags identically.

    Output-only range flags can make FFmpeg convert/clamp the fixture itself,
    concealing the very headroom the native importer must reject.
    """
    width, height = len(y_values), 4
    y = np.tile(np.array(y_values, dtype='<u2'), (height, 1))
    uv = [np.full(y.shape, value, dtype='<u2') for value in (u, v)]
    raw = tmp_path / 'source.yuv'; raw.write_bytes(y.tobytes() + uv[0].tobytes() + uv[1].tobytes())
    source = tmp_path / 'source.mkv'
    hdr = transfer in ('smpte2084', 'arib-std-b67')
    tags = ['-color_primaries', 'bt2020' if hdr else 'bt709', '-color_trc', transfer,
            '-colorspace', 'bt2020nc' if hdr else 'bt709', '-color_range', range_]
    ffmpeg('-f', 'rawvideo', '-pixel_format', 'yuv444p10le', '-video_size', f'{width}x{height}',
           '-framerate', '24', *tags, '-i', raw, '-frames:v', '1', '-c:v', 'ffv1', *tags, source)
    decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(source), '-frames:v', '1',
                              '-f', 'rawvideo', '-pix_fmt', 'yuv444p10le', 'pipe:1'],
                             capture_output=True, check=True).stdout
    assert decoded == raw.read_bytes()  # independently verify no fixture clipping
    return source


def test_native_23976_10bit_odd_geometry_precision_and_master_roundtrip(tmp_path):
    source = rgb10_source(tmp_path)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    # This fixture's NUT/RGB stream has no colour tags. It must not silently
    # assume a transfer function, including a possibly Log-encoded source.
    unknown = media.inspect_source(source)
    assert unknown['unsupported_reasons']
    with pytest.raises(ValueError, match='Assign the source colour profile'):
        media.ingest_native(source, tmp_path / 'unassigned')
    p = media.ingest_native(source, tmp_path / 'native',
                            colour_overrides={'transfer': 'bt709', 'primaries': 'bt709'})
    assert p['width'] == 259 and p['height'] == 35 and p['fps_rational'] == '24000/1001'
    assert p['frames'] == 4 and p['bit_depth'] == 10 and p['storage_bit_depth'] == 16
    assert p['colour_interpretation'] == 'assigned' and p['source_colour']['transfer'] == 'unknown'
    frame = media.read_native_frame(tmp_path / 'native', 0)
    assert frame.dtype == np.uint16 and len(np.unique(frame[..., 0])) > 256
    assert hashlib.sha256((tmp_path / 'native' / p['source_file']).read_bytes()).hexdigest() == source_hash
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    result = media.export_native(tmp_path / 'native', p, tmp_path / 'output')
    master = media.inspect_source(tmp_path / 'output' / result['master'])
    assert (master['width'], master['height'], master['frames']) == (259, 35, 4)
    assert master['fps_rational'] == p['fps_rational']
    assert master['source_pts'] == p['source_pts'] and master['time_base'] == p['time_base']
    assert master['colour']['transfer'] == 'bt709' and master['colour']['primaries'] == 'bt709'
    assert master['bit_depth'] == 16 and result['master_pixels_verified_exact']
    preview = media.inspect_source(tmp_path / 'output' / result['preview'])
    assert (preview['width'], preview['height']) == (260, 36)
    assert preview['frames'] == 4  # even padding is browser preview only
    assert preview['colour']['transfer'] == 'iec61966-2-1' and preview['colour']['primaries'] == 'bt709'


@pytest.mark.parametrize('transfer', ['bt709', 'smpte2084', 'arib-std-b67'])
@pytest.mark.parametrize('range_,values', [('tv', (64, 512, 940)), ('pc', (0, 512, 1023))])
def test_tagged_legal_yuv_decodes_black_white_without_hidden_clipping(tmp_path, transfer, range_, values):
    source = yuv10_source(tmp_path, y_values=values, range_=range_, transfer=transfer)
    p = media.ingest_native(source, tmp_path / 'native')
    rgb = media.read_native_frame(tmp_path / 'native', 0)
    assert rgb.dtype == np.uint16 and p['storage_bit_depth'] == 16
    assert np.array_equal(rgb[:, 0], np.zeros((4, 3), dtype=np.uint16))
    assert np.array_equal(rgb[:, -1], np.full((4, 3), 65535, dtype=np.uint16))
    expected_middle = round(((512 - 64) / 876 if range_ == 'tv' else 512 / 1023) * 65535)
    assert np.max(np.abs(rgb[:, 1].astype(int) - expected_middle)) <= 1
    validation = p['rgb_headroom_validation']
    assert validation['frames_checked'] == 1 and validation['encoded_rgb_max'] == pytest.approx(1)
    assert p['colour']['transfer'] == transfer and p['colour_interpretation'] == 'source_metadata'


@pytest.mark.parametrize('y,u,v,range_', [
    (0, 512, 512, 'tv'), (1023, 512, 512, 'tv'),  # superblack / superwhite
    (512, 512, 960, 'tv'), (512, 64, 64, 'tv'),  # legal Y,U,V; out-of-gamut RGB
    (512, 0, 0, 'pc'),  # full-range metadata does not ensure representable RGB
])
def test_signal_headroom_and_out_of_gamut_rgb_are_rejected_before_integer_master(tmp_path, y, u, v, range_):
    source = yuv10_source(tmp_path, y_values=(y,) * 3, u=u, v=v, range_=range_)
    original = source.read_bytes()
    with pytest.raises(ValueError, match='integer RGB master would clip.*float/EXR-capable'):
        media.ingest_native(source, tmp_path / 'native')
    assert source.read_bytes() == original
    assert (tmp_path / 'native/source.mkv').read_bytes() == original
    assert not (tmp_path / 'native/profile.json').exists()
    assert not list((tmp_path / 'native/frames').glob('*.png'))


def test_component_descriptors_do_not_guess_packed10bit_or16bit_precision():
    assert media._depth({'pix_fmt': 'x2rgb10le'}) == 10
    assert media._format({'pix_fmt': 'x2rgb10le'})['flags']['rgb']
    assert media._depth({'pix_fmt': 'p016le'}) == 16
    with pytest.raises(ValueError, match='not guessed'):
        media._depth({'pix_fmt': 'unknown-native-format'})
    with pytest.raises(ValueError, match='Floating-point/XYZ'):
        media._depth({'pix_fmt': 'gbrpf32le'})


@pytest.mark.parametrize('y,u,v,range_', [(0,512,512,'tv'),(1023,512,512,'tv'),(512,64,64,'tv'),(512,0,0,'pc')])
def test_explicit_legal_range_copy_records_clipping_and_retains_source_timing(tmp_path,y,u,v,range_):
    source=yuv10_source(tmp_path,y_values=(y,)*3,u=u,v=v,range_=range_)
    original=source.read_bytes();before=media.inspect_source(source)
    p=media.ingest_native(source,tmp_path/'legal',rgb_range='legalize')
    assert p['rgb_range_policy']=='legalize' and 'clipped' in p['working_copy_warning']
    assert p['width']==before['width'] and p['height']==before['height']
    assert p['fps_rational']==before['fps_rational'] and p['source_pts']==before['source_pts']
    assert p['colour']==before['colour'] and p['storage_bit_depth']==16
    assert source.read_bytes()==original==(tmp_path/'legal'/p['source_file']).read_bytes()
    stats=p['rgb_headroom_validation']
    assert stats['clipped_source_frames']==[0] and stats['clipped_components']>0
    assert stats['encoded_rgb_min']<0 or stats['encoded_rgb_max']>1
    frame=media.read_native_frame(tmp_path/'legal',0)
    assert frame.min()==0 or frame.max()==65535
    result=media.export_native(tmp_path/'legal',p,tmp_path/'export')
    assert result['master_pixels_verified_exact']
    assert json.loads((tmp_path/'export/native_profile.json').read_text())['rgb_range_policy']=='legalize'


def test_colour_preset_does_not_silently_replace_a_known_unsupported_yuv_matrix():
    profile = {'pix_fmt': 'yuv444p10le', 'source_is_rgb': False,
               'source_colour': {'matrix': 'bt2020c', 'range': 'tv'}}
    assigned = media.colour_profile_overrides('pq2020', profile)
    assert assigned['matrix'] == 'bt2020c'  # constant luminance cannot become nonconstant without a transform
    profile['source_colour']['matrix'] = 'unknown'
    assert media.colour_profile_overrides('pq2020', profile)['matrix'] == 'bt2020nc'


@pytest.mark.parametrize('transfer', ['smpte2084', 'arib-std-b67'])
def test_hdr_tags_native_highlight_values_and_separate_sdr_proxy(tmp_path, transfer):
    images = pattern(tmp_path / 'input', size=(96, 64))
    source = tmp_path / 'hdr.mp4'
    # Independent real10-bit encoded source with native VUI/container HDR tags.
    ffmpeg('-framerate', '30000/1001', '-i', images / '%05d.png', '-frames:v', '4',
           '-c:v', 'libx265', '-preset', 'ultrafast', '-x265-params',
           f'log-level=error:pools=1:frame-threads=1:colorprim=9:transfer={16 if transfer == "smpte2084" else 18}:colormatrix=9',
           '-pix_fmt', 'yuv444p10le', '-color_primaries', 'bt2020', '-color_trc', transfer,
           '-colorspace', 'bt2020nc', '-color_range', 'tv', '-tag:v', 'hvc1', source)
    p = media.ingest_native(source, tmp_path / 'native')
    assert p['colour_interpretation'] == 'source_metadata'
    assert p['colour']['transfer'] == transfer and p['colour']['primaries'] == 'bt2020'
    assert p['fps_rational'] == '30000/1001' and p['bit_depth'] == 10
    frame = media.read_native_frame(tmp_path / 'native', 0)
    assert frame.dtype == np.uint16 and colour.to_linear(frame, p).max() > 1
    proxy = colour.display_frame(frame, p)
    assert proxy.dtype == np.uint8 and proxy.shape == frame.shape
    assert not np.array_equal(proxy, np.rint(frame / 257).astype(np.uint8))
    result = media.export_native(tmp_path / 'native', p, tmp_path / 'out')
    master = media.inspect_source(tmp_path / 'out/master.mov')
    assert master['colour']['transfer'] == transfer and master['colour']['primaries'] == 'bt2020'
    assert master['bit_depth'] == 16 and result['master_pixels_verified_exact']
    assert media.read_native_frame(tmp_path / 'native', 0).dtype == np.uint16


def test_true_vfr_pts_and_frame_order_survive_native_export(tmp_path):
    images = pattern(tmp_path / 'input', size=(71, 53))
    source = tmp_path / 'vfr.mov'
    ffmpeg('-framerate', '25', '-i', images / '%05d.png', '-frames:v', '4', '-vf',
           "settb=1/1000,setpts='N*N*40'", '-fps_mode', 'passthrough', '-enc_time_base', '1:1000',
           '-c:v', 'png', '-pix_fmt', 'rgb48be', '-video_track_timescale', '1000', source)
    p = media.ingest_native(source, tmp_path / 'native',
                            colour_overrides={'transfer': 'iec61966-2-1', 'primaries': 'bt709'})
    assert p['is_vfr'] and p['timestamps'] == [0, .04, .16, .36]
    result = media.export_native(tmp_path / 'native', p, tmp_path / 'out')
    output = media.inspect_source(tmp_path / 'out' / result['master'])
    assert output['frames'] == 4 and output['is_vfr']
    source_pts = [Fraction(x - p['source_pts'][0]) * Fraction(p['time_base']) for x in p['source_pts']]
    output_pts = [Fraction(x - output['source_pts'][0]) * Fraction(output['time_base']) for x in output['source_pts']]
    assert source_pts == output_pts
    assert output['duration'] == pytest.approx(p['duration'], abs=1e-9)
    assert result['master_pixels_verified_exact']
    preview = media.inspect_source(tmp_path / 'out/result.mp4')
    assert preview['frames'] == p['frames']
    preview_pts = [Fraction(x - preview['source_pts'][0]) * Fraction(preview['time_base']) for x in preview['source_pts']]
    assert source_pts == preview_pts


@pytest.mark.parametrize('trim_start', [0, .25])
def test_nonzero_source_pts_audio_is_sought_absolutely_and_midtrim_keeps_samples(tmp_path, trim_start):
    images = pattern(tmp_path / 'input', size=(16, 16))
    sample_rate = 48000
    # Unique changing PCM makes an incorrect source interval detectable, unlike
    # a constant or periodically repeating tone.
    samples = (((np.arange(sample_rate, dtype=np.int64) * 37) % 30001) - 15000).astype('<i2')
    audio = tmp_path / 'audio.wav'
    with wave.open(str(audio), 'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(sample_rate)
        output.writeframes(samples.tobytes())
    source = tmp_path / 'offset.mkv'
    ffmpeg('-framerate', '4', '-i', images / '%05d.png', '-i', audio,
           '-vf', 'setpts=PTS+5/TB', '-af', 'asetpts=PTS+5/TB',
           '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', '-c:a', 'pcm_s16le',
           '-copyts', '-avoid_negative_ts', 'disabled', source)
    p = media.ingest_native(source, tmp_path / 'native', start_s=trim_start,
                            colour_overrides={'transfer': 'bt709', 'primaries': 'bt709'})
    assert float(p['source_format_metadata']['start_time']) == 5
    assert p['trim_actual_start_s'] == 5 + trim_start
    result = media.export_native(tmp_path / 'native', p, tmp_path / 'output', source_audio=source)
    master = tmp_path / 'output' / result['master']
    metadata = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_streams',
                               '-of', 'json', str(master)], capture_output=True, check=True, text=True)
    audio_stream = json.loads(metadata.stdout)['streams'][0]
    assert audio_stream['codec_name'] == 'pcm_s24le'
    assert float(audio_stream['start_time']) == 0
    decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(master), '-map', '0:a:0',
                              '-c:a', 'pcm_s32le', '-f', 's32le', 'pipe:1'], capture_output=True, check=True)
    actual = np.frombuffer(decoded.stdout, dtype='<i4')
    expected = samples[round(trim_start * sample_rate):].astype(np.int32) * 65536
    assert np.array_equal(actual, expected)
    assert media.inspect_source(master)['source_pts'][0] == 0
    assert media.inspect_source(tmp_path / 'output' / result['preview'])['source_pts'][0] == 0
    # Browser preview keeps a playable AAC track from the same absolute seek.
    preview_audio = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_streams',
                                    '-of', 'json', str(tmp_path / 'output' / result['preview'])],
                                   capture_output=True, check=True, text=True)
    preview_stream = json.loads(preview_audio.stdout)['streams'][0]
    assert preview_stream['codec_name'] == 'aac'
    assert float(preview_stream['duration']) == pytest.approx(p['duration'], abs=1 / sample_rate)


def test_standard_transfer_anchors_hlg_luminance_gamma_and_gamut():
    pq = {'transfer': 'smpte2084', 'primaries': 'bt2020'}
    assert colour.decode(np.array([[[.5080784215] * 3]]), pq)[0, 0, 0] == pytest.approx(1, rel=1e-6)
    hlg = {'transfer': 'arib-std-b67', 'primaries': 'bt2020'}
    decoded = colour.decode(np.array([[[.8, .5, .25]]]), hlg)
    assert decoded[0, 0, 1] / decoded[0, 0, 2] == pytest.approx(4)
    # System gamma belongs on luminance; applying it per channel gives5.278.
    for profile in [pq, hlg, {'transfer': 'bt709', 'primaries': 'bt709'},
                    {'transfer': 'iec61966-2-1', 'primaries': 'smpte432'}]:
        rgb = np.array([[[.05, .37, .9], [.1, .75, .5]]])
        assert np.allclose(colour.encode(colour.decode(rgb, profile), profile), rgb, atol=3e-7)
    white = colour.convert_primaries(np.ones((1, 1, 3)), 'bt709', 'bt2020')
    assert np.allclose(white, 1, atol=1e-12)
    red = colour.convert_primaries(np.array([1., 0, 0]), 'bt709', 'bt2020')
    assert np.allclose(red, [.6274039, .0690973, .0163914], atol=1e-6)


def test_native_composite_and_muzzle_delta_preserve_unaffected_hdr_pixels():
    profile = {'colour': {'transfer': 'smpte2084', 'primaries': 'bt2020'}}
    base = np.full((5, 7, 3), 59000, np.uint16)
    alpha = np.zeros((5, 7), np.float32); alpha[2, 3] = .5
    fg = np.full(base.shape, 255, np.uint8)
    result = colour.composite_native(base, profile, fg, alpha)
    assert np.array_equal(result[alpha == 0], base[alpha == 0])
    assert not np.array_equal(result[2, 3], base[2, 3])
    assert colour.to_linear(result, profile)[0, 0].min() > 10  # native HDR,not SDR-clipped
    before = colour.display_frame(base, profile); after = before.copy(); after[2, 3] = 255
    mask = np.zeros((5, 7), np.float32); mask[2, 3] = 1
    lifted = media.lift_display_edit(base, before, after, profile, mask)
    assert np.array_equal(lifted[mask == 0], base[mask == 0])
    assert colour.to_linear(lifted, profile)[2, 3].min() > 10
    assert np.all(lifted[2, 3] > base[2, 3])


def test_native_muzzle_adapter_identity_keeps_portrait_source_pixels(monkeypatch, tmp_path):
    from keyfx import flashes
    row = np.where(np.arange(317) % 2, 217, 31).astype(np.uint8)
    rgb = np.repeat(np.tile(row, (509, 1))[..., None], 3, axis=2)
    captured = {}
    def identity(fitted, flash, seed, out, model='nano'):
        captured['size'] = fitted.shape
        captured['flash'] = flash
        return fitted.copy()
    monkeypatch.setattr(flashes, 'generate', identity)
    flash = {'origin': {'x': .5, 'y': .4}, 'tip': {'x': .7, 'y': .5}}
    result = flashes.generate_native_display(rgb, flash, 42, tmp_path)
    assert np.array_equal(result, rgb)  # no global down/upscale replacement
    assert captured['size'] == (1080, 1920, 3)
    assert .5 < captured['flash']['tip']['x'] < .7  # fitted portrait coordinates
    assert flash['tip']['x'] == .7  # caller geometry unchanged
