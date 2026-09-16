"""Config-editor rows (0.10.0): editor FX switches on the Performance tab."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location('cta_controller_under_test_ce', _ROOT / 'cta_controller.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load()


class _Editor:
    def __init__(self) -> None:
        self.applied: list[dict] = []

    def apply_fx_settings(self, **kw):
        self.applied.append(kw)


def _ctrl():
    c = object.__new__(_mod.CTAController)
    c._editor_fx = True
    c._editor_drip = True
    c._editor_sparkle = True
    c._editor_sparkle_density = 1.0
    c._editor = None
    return c


def test_rows_and_live_push_to_an_open_editor() -> None:
    c = _ctrl()
    rows = {r['name']: r for r in c.config_editor_settings()}
    assert [r['kind'] for r in rows.values() if 'kind' in r] == ['toggle'] * 3
    assert rows['editor_sparkle_density']['display'] == '1.00x'
    c.set_config_setting('editor_drip', 0.0)          # no editor open: just stored
    assert c._editor_drip is False
    c._editor = _Editor()
    c.set_config_setting('editor_sparkle_density', 5.0)
    assert c._editor_sparkle_density == 2.0
    assert c._editor.applied[-1] == {
        'fx_enabled': True, 'drip_enabled': False, 'sparkle_enabled': True,
        'sparkle_density': 2.0}
    c.set_config_setting('bogus', 1.0)
    assert len(c._editor.applied) == 1
