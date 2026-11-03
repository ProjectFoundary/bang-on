import pytest
from keyfx import lighting_integration as light


def test_native_landscape_reference_is_padded_to_h3_32_pixel_grid(monkeypatch):
    p={'effects':[{'id':'fx'}],'native_profile':{'width':2558,'height':1462,'timestamps':[i/24 for i in range(85)]}}
    monkeypatch.setattr(light,'_rows',lambda p:[(p['effects'][0],{'plan':{'start_time':0,'end_time':3.5,'active_frames':84}})])
    monkeypatch.setattr(light,'input_signature',lambda p:'source')
    result=light.plan(p)
    assert (result['width'],result['height'])==(1008,576)
    assert (result['canvas_width'],result['canvas_height'])==(1024,576)
    assert result['model_frames']==90


def test_live_node_numeric_steps_are_checked_before_submission():
    catalog={'EmptyMiniMaxH3LatentAV':{'input':{'required':{
        'width':['INT',{'min':32,'max':16384,'step':32}],
        'height':['INT',{'min':32,'max':16384,'step':32}],
        'length':['INT',{'min':5,'max':3600,'step':17}]}}}}
    spec={'canvas_width':1024,'canvas_height':576,'model_frames':90}
    light.validate_catalog_canvas(spec,catalog)
    for field,value in [('canvas_width',1008),('canvas_height',560),('model_frames',89)]:
        with pytest.raises(ValueError,match='numeric grid'):light.validate_catalog_canvas({**spec,field:value},catalog)
