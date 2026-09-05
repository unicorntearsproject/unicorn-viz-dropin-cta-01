"""cta-01's Control Room operator panel (P2 of
docs/planning/control-room-panel-registry-plan-2026-09-04.md).

Replaces the old hardcoded CR "DROP-INS" panel's CTA/LAST SONG buttons --
which called vj_api.trigger_streaming_cta()/trigger_streaming_song_cta(),
methods that never existed anywhere in the codebase, so every press
silently failed (caught by Control Room's own exception handler).
_operator_panel_action() now calls trigger_hype()/trigger_song() and
vj_api.trigger_cta_custom(), the same real path the existing MIDI
handlers already use. The stateful "press again for one more" toggle
Control Room kept on its own side is replaced by two independent
buttons (LAST SONG, ONE MORE).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cta_controller import CTAController  # noqa: E402

from unicornviz.operator_panels import OperatorPanel, PanelContent  # noqa: E402


class _FakeVJApi:
    def __init__(self) -> None:
        self.panels: dict[str, OperatorPanel] = {}
        self.custom_calls: list[tuple] = []

    def register_midi_actions(self, section, actions) -> None:
        pass

    def register_midi_action_handler(self, action, fn) -> None:
        pass

    def register_operator_panel(self, panel: OperatorPanel) -> None:
        self.panels[panel.name] = panel

    def trigger_cta_custom(self, *args) -> None:
        self.custom_calls.append(args)


def _controller(**cfg) -> CTAController:
    return CTAController(cfg)


def test_registration_call_registers_an_operator_panel() -> None:
    controller = _controller()
    vj = _FakeVJApi()
    controller.set_vj_api(vj)
    panel = vj.panels['cta']
    assert panel.title == 'CTA' and panel.page == 'main' and panel.size == 'small'
    assert panel.content == controller._operator_panel_content
    assert panel.on_action == controller._operator_panel_action
    assert panel.tooltips['last_song'] == 'Announce the last song'


def test_content_lists_the_three_buttons_and_reflects_enabled() -> None:
    controller = _controller(enabled=True)
    controller.set_vj_api(_FakeVJApi())
    content = controller._operator_panel_content()
    assert isinstance(content, PanelContent)
    assert content.status == 'ON'
    assert {b.action for b in content.buttons} == {'hype', 'last_song', 'one_more'}

    controller_off = _controller(enabled=False)
    controller_off.set_vj_api(_FakeVJApi())
    assert controller_off._operator_panel_content().status == 'OFF'


def test_action_hype_fires_and_reports_the_message() -> None:
    vj = _FakeVJApi()
    controller = _controller(cooldown_s=0.0)
    controller.set_vj_api(vj)
    message = controller._operator_panel_action('hype', None)
    assert message is not None and message.startswith('CTA: ')
    assert len(vj.custom_calls) == 1


def test_action_last_song_and_one_more_fire_independently() -> None:
    vj = _FakeVJApi()
    controller = _controller(cooldown_s=0.0)
    controller.set_vj_api(vj)
    assert controller._operator_panel_action('last_song', None) == 'CTA: Last song!'
    assert controller._operator_panel_action('one_more', None) == 'CTA: One more song!'
    assert len(vj.custom_calls) == 2


def test_action_reports_cooldown_without_calling_trigger_cta_custom() -> None:
    vj = _FakeVJApi()
    controller = _controller(cooldown_s=60.0)
    controller.set_vj_api(vj)
    controller._operator_panel_action('hype', None)  # primes _last_t
    message = controller._operator_panel_action('hype', None)  # still cooling down
    assert message == 'CTA: on cooldown or disabled'
    assert len(vj.custom_calls) == 1


def test_action_disabled_reports_without_calling_trigger_cta_custom() -> None:
    vj = _FakeVJApi()
    controller = _controller(enabled=False)
    controller.set_vj_api(vj)
    assert controller._operator_panel_action('hype', None) == 'CTA: on cooldown or disabled'
    assert vj.custom_calls == []


def test_action_unknown_returns_none() -> None:
    controller = _controller()
    controller.set_vj_api(_FakeVJApi())
    assert controller._operator_panel_action('bogus', None) is None
