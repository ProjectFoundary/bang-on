"""Only baked 3D optical guides depend on the corrected visibility recipe."""
import pytest
from keyfx import element_jobs, geometry


@pytest.mark.parametrize('mode,role,assigned,post,changes', [
    ('depth3d','both',True,False,True),
    ('depth3d','holdout',True,False,True),
    ('depth3d','collide',True,False,False),
    ('depth3d','both',False,False,False),
    ('matte2d','both',True,False,False),
    ('depth3d','holdout',True,True,False),
    ('depth3d','both',True,True,False),
])
def test_visibility_recipe_invalidates_only_baked_depth_holdouts(monkeypatch,mode,role,assigned,post,changes):
    effect={'id':'blast','preset':'explosion','keys':[{'phase':'onset','frame':0,'x':.8,'y':.5}],
            'element':{'colliders':['pillar'] if assigned else []}}
    matte={'id':'pillar','role':role,'kind':'hard','static':True,'frames':1,
           'width':16,'height':9,'thickness':.2,'holdoutPost':post}
    project={'frames':24,'settings':{},'geometry':{**geometry.defaults(),'collision_mode':mode,'mattes':[matte]}}
    before=element_jobs.generation_signature(project,effect)
    monkeypatch.setattr(element_jobs,'DEPTH_HOLDOUT_VISIBILITY_PROFILE','changed-visibility-recipe')
    after=element_jobs.generation_signature(project,effect)
    assert (before!=after) is changes
