"""Explicit unlimited preferences and finite budgets, without paid inference."""
import json
import pytest
from keyfx import flashes,projects


@pytest.mark.parametrize('value,expected',[('unlimited',None),(' UNLIMITED ',None),('0',0),('105',105),('-1',0),('invalid',0)])
def test_explicit_allowance_values(monkeypatch,value,expected):
    monkeypatch.setenv('KEYFX_FLASH_CREDITS',value)
    assert flashes.budget_credits()==expected


def test_saved_preference_and_explicit_environment_override(tmp_path,monkeypatch):
    monkeypatch.setattr(flashes,'DATA',tmp_path)
    monkeypatch.delenv('KEYFX_FLASH_CREDITS',raising=False)
    assert flashes.budget_credits()==0
    (tmp_path/'local-settings.json').write_text(json.dumps({'flash_credit_allowance':'unlimited'}))
    assert flashes.budget_credits() is None
    monkeypatch.setenv('KEYFX_FLASH_CREDITS','70')
    assert flashes.budget_credits()==70
    monkeypatch.setenv('KEYFX_FLASH_CREDITS','0')
    assert flashes.budget_credits()==0


def test_finite_allowance_never_reports_negative_remaining(monkeypatch):
    monkeypatch.setenv('KEYFX_FLASH_CREDITS','35')
    monkeypatch.setattr(projects,'_spent',70)
    assert projects.flash_cost(1)=={'per_flash':35,'requested':35,'allowance_left':0,'unlimited':False}
