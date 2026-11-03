"""Unit transmittance stays bounded/neutral through fractional native scaling."""
import numpy as np
import pytest

from keyfx import element_jobs, native_colour


def test_shadow_resampling_at_actual_native_ratio_preserves_neutral_and_darkens():
    # The real shot failed at1008×576→2558×1462. Float64 bilinear interpolation
    # of neutral1 produced1.0000000596 outside support on the tested OpenCV.
    source=np.ones((576,1008),np.float64);source[200:350,400:550]=.9
    shadow=element_jobs._resize_shadow(source,(2558,1462))
    assert shadow.shape==(1462,2558) and np.isfinite(shadow).all()
    assert shadow.min()>=0 and shadow.max()<=1
    assert np.all(shadow[:100,:100]==1),'No false attenuation in a neutral region'
    assert np.any(shadow<.91),'The fix must retain real shadow attenuation'
    profile={'colour':{'transfer':'bt709','primaries':'bt709','matrix':'gbr','range':'pc'}}
    base=np.full((32,32,3),128,np.uint8)
    x,y=round(475/1008*2558),round(275/576*1462)
    core=shadow[y-16:y+16,x-16:x+16]
    dark=native_colour.composite_native(base,profile,shadow=core)
    assert np.any(dark<base) and np.all(dark<=base)
    neutral=native_colour.composite_native(base,profile,shadow=shadow[:32,:32])
    assert np.array_equal(neutral,base),'Neutral photographic pixels must remain exact'


def test_entire_neutral_shadow_field_remains_exact_one_at_actual_native_ratio():
    resized=element_jobs._resize_shadow(np.ones((576,1008),np.float64),(2558,1462))
    assert np.array_equal(resized,np.ones_like(resized))


@pytest.mark.parametrize('value',[-.0001,1.0001,np.nan,np.inf])
def test_shadow_source_validation_stays_strict(value):
    with pytest.raises(native_colour.NativeColourError,match='finite transmittance'):
        element_jobs._resize_shadow(np.array([[value]],np.float64),(47,35))


def test_native_colour_still_rejects_even_small_out_of_range_transmittance():
    base=np.full((2,2,3),128,np.uint8)
    profile={'colour':{'transfer':'bt709','primaries':'bt709','matrix':'gbr','range':'pc'}}
    with pytest.raises(native_colour.NativeColourError,match='finite transmittance'):
        native_colour.composite_native(base,profile,shadow=np.full((2,2),np.nextafter(1.,np.inf)))
