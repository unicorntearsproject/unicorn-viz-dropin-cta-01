"""Regression tests for the cta-01 editor's box redesign.

Covers the three pieces the owner asked for on top of the old flat, single
colour rectangles:

* "drip"    -- ``_draw_drip_edge`` (organic bass/beat-driven edge sag).
* "sparkle" -- ``_init_sparkles`` / ``_draw_sparkles`` (treble/beat-driven
  glint accents scattered along the panel border).
* "sophistication" -- ``_grad_rounded_rect`` / ``_fill_box`` (gradient,
  rounded-corner backgrounds instead of a single flat fill colour), gated by
  the ``fx_enabled`` master switch so the drop-in still has a working flat
  fallback when FX are turned off.

Also covers the [cta] config plumbing (``editor_fx`` / ``editor_drip`` /
``editor_sparkle`` / ``editor_sparkle_density``) all the way from
``CTAController`` through to the ``CTAEditor`` instance it constructs.

``cta_editor.py`` is loaded via file-path ``importlib`` loading with a
synthetic parent package registered first (mirroring how the real app loads
drop-in files, and how ``unicornviz``'s own
``tests/test_dropin_relative_imports.py`` regression-tests this exact
loading path) so its ``from .text_field import TextField`` sibling import
resolves. This file does not import ``unicornviz`` itself -- cta-01's own
test suite must stand on its own.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

pytest.importorskip('PIL')
from PIL import Image, ImageDraw  # noqa: E402

_DROPIN_DIR = Path(__file__).resolve().parents[1]
_PKG_NAME = '_cta01_test_pkg'


def _load(stem: str) -> types.ModuleType:
    """Load ``<stem>.py`` from the drop-in directory as a submodule of a
    synthetic package, so `from .sibling import X` resolves."""
    if _PKG_NAME not in sys.modules:
        pkg = types.ModuleType(_PKG_NAME)
        pkg.__path__ = [str(_DROPIN_DIR)]
        sys.modules[_PKG_NAME] = pkg
    full_name = f'{_PKG_NAME}.{stem}'
    existing = sys.modules.get(full_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(full_name, _DROPIN_DIR / f'{stem}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


cta_editor = _load('cta_editor')
CTAEditor = cta_editor.CTAEditor

cta_controller = _load('cta_controller')
CTAController = cta_controller.CTAController


def _audio(bass: float = 0.0, treble: float = 0.0, beat: float = 0.0) -> types.SimpleNamespace:
    return types.SimpleNamespace(bass_n=bass, treble_n=treble, beat=beat)


def _new_editor(**kwargs) -> CTAEditor:
    ed = CTAEditor(MagicMock(), [('Push the buttons!', '\U0001f44d')], **kwargs)
    ed._ensure_fonts()
    return ed


# ── [cta] config plumbing ────────────────────────────────────────────────────

def test_fx_master_switch_forces_drip_and_sparkle_off() -> None:
    """fx_enabled=False must disable drip/sparkle regardless of their own
    flags -- it's the accessibility/perf escape hatch back to the flat
    1980s-boxy look."""
    ed = _new_editor(fx_enabled=False, drip_enabled=True, sparkle_enabled=True)
    assert ed._fx_enabled is False
    assert ed._drip_enabled is False
    assert ed._sparkle_enabled is False


def test_fx_enabled_respects_individual_toggles() -> None:
    ed = _new_editor(fx_enabled=True, drip_enabled=False, sparkle_enabled=True)
    assert ed._drip_enabled is False
    assert ed._sparkle_enabled is True


def test_controller_defaults_editor_fx_on() -> None:
    controller = CTAController({})
    controller.set_vj_api(MagicMock())
    controller._open_editor()
    editor = controller._editor
    assert editor is not None
    assert editor._fx_enabled is True
    assert editor._drip_enabled is True
    assert editor._sparkle_enabled is True
    assert editor._sparkle_density == pytest.approx(1.0)


def test_controller_parses_editor_fx_keys_and_forwards_to_editor() -> None:
    controller = CTAController({
        'editor_fx': True,
        'editor_drip': False,
        'editor_sparkle': True,
        'editor_sparkle_density': 2.5,
    })
    controller.set_vj_api(MagicMock())
    controller._open_editor()
    editor = controller._editor
    assert editor is not None
    assert editor._fx_enabled is True
    assert editor._drip_enabled is False
    assert editor._sparkle_enabled is True
    assert editor._sparkle_density == pytest.approx(2.5)


def test_controller_editor_fx_false_disables_everything_downstream() -> None:
    controller = CTAController({'editor_fx': False})
    controller.set_vj_api(MagicMock())
    controller._open_editor()
    editor = controller._editor
    assert editor is not None
    assert editor._fx_enabled is False
    assert editor._drip_enabled is False
    assert editor._sparkle_enabled is False


# ── _draw() renders cleanly in both modes ───────────────────────────────────

@pytest.mark.parametrize('fx_enabled', [True, False])
def test_draw_produces_full_size_rgba_image(fx_enabled: bool) -> None:
    ed = _new_editor(fx_enabled=fx_enabled)
    img = ed._draw(1280, 800)
    assert img.size == (1280, 800)
    assert img.mode == 'RGBA'


def test_draw_handles_multiple_rows_scroll_and_selection_without_error() -> None:
    ed = _new_editor()
    for _ in range(8):
        ed._add_row()
    ed._sel_row = 5
    ed._sel_col = 1
    ed._clamp_scroll()
    img = ed._draw(1280, 800)
    assert img.size == (1280, 800)


# ── Redesign building blocks: gradient/rounded backgrounds ─────────────────

def test_grad_rounded_rect_is_a_vertical_gradient_clipped_to_rounded_corners() -> None:
    ed = _new_editor()
    box = ed._grad_rounded_rect(60, 40, (200, 0, 0), (0, 0, 200), 255, radius=8)
    arr = np.array(box)
    top_rgb = tuple(int(c) for c in arr[2, 30][:3])
    bot_rgb = tuple(int(c) for c in arr[37, 30][:3])
    assert top_rgb != bot_rgb
    # A flat rounded corner clips the very corner pixel to (near) transparent.
    assert arr[0, 0][3] < 40
    # The centre sits fully within the rounded rect at full alpha.
    assert arr[20, 30][3] == 255


def test_fill_box_is_flat_when_fx_disabled() -> None:
    """The fx_enabled=False fallback must still be the original flat single
    colour fill -- disabling FX must not leave a half-drawn box."""
    ed = _new_editor(fx_enabled=False)
    img = Image.new('RGBA', (60, 40), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, 'RGBA')
    ed._fill_box(img, draw, 0, 0, 60, 40, (10, 20, 30), (200, 0, 0), (0, 0, 200), 255, 8)
    arr = np.array(img)
    top_rgb = tuple(int(c) for c in arr[2, 30][:3])
    bot_rgb = tuple(int(c) for c in arr[37, 30][:3])
    assert top_rgb == bot_rgb == (10, 20, 30)


def test_fill_box_is_gradient_when_fx_enabled() -> None:
    ed = _new_editor(fx_enabled=True)
    img = Image.new('RGBA', (60, 40), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, 'RGBA')
    ed._fill_box(img, draw, 0, 0, 60, 40, (10, 20, 30), (200, 0, 0), (0, 0, 200), 255, 8)
    arr = np.array(img)
    top_rgb = tuple(int(c) for c in arr[2, 30][:3])
    bot_rgb = tuple(int(c) for c in arr[37, 30][:3])
    assert top_rgb != bot_rgb


# ── Drip: organic bass/beat-driven edge sag ─────────────────────────────────

def test_draw_drip_edge_noop_when_disabled() -> None:
    ed = _new_editor(drip_enabled=False)
    img = Image.new('RGBA', (100, 40), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, 'RGBA')
    ed._draw_drip_edge(draw, 5, 95, 20, (255, 0, 255, 255), width=2, amp=1.0)
    assert np.array(img).sum() == 0


def test_draw_drip_edge_shape_reacts_to_bass_and_beat() -> None:
    ed = _new_editor(drip_enabled=True)
    ed._drip_phase = 0.7

    ed._bass, ed._beat = 0.0, 0.0
    img_quiet = Image.new('RGBA', (100, 40), (0, 0, 0, 0))
    ed._draw_drip_edge(ImageDraw.Draw(img_quiet, 'RGBA'), 5, 95, 20, (255, 0, 255, 255), width=2)

    ed._bass, ed._beat = 1.0, 1.0
    img_loud = Image.new('RGBA', (100, 40), (0, 0, 0, 0))
    ed._draw_drip_edge(ImageDraw.Draw(img_loud, 'RGBA'), 5, 95, 20, (255, 0, 255, 255), width=2)

    assert not np.array_equal(np.array(img_quiet), np.array(img_loud))


# ── Sparkle: treble/beat-driven glint accents ───────────────────────────────

def test_init_sparkles_count_scales_with_density() -> None:
    ed0 = _new_editor(sparkle_enabled=True, sparkle_density=0.0)
    ed0._init_sparkles()
    assert ed0._sparkle_anchors == []

    ed1 = _new_editor(sparkle_enabled=True, sparkle_density=1.0)
    ed1._init_sparkles()
    assert len(ed1._sparkle_anchors) == 18

    ed2 = _new_editor(sparkle_enabled=True, sparkle_density=2.0)
    ed2._init_sparkles()
    assert len(ed2._sparkle_anchors) == 36


def test_init_sparkles_empty_when_sparkle_disabled() -> None:
    ed = _new_editor(sparkle_enabled=False, sparkle_density=5.0)
    ed._init_sparkles()
    assert ed._sparkle_anchors == []


def test_draw_sparkles_noop_when_disabled() -> None:
    ed = _new_editor(sparkle_enabled=False)
    img = Image.new('RGBA', (200, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, 'RGBA')
    ed._fx_time, ed._treble, ed._beat = 1.0, 1.0, 1.0
    ed._draw_sparkles(draw, 0, 0, 200, 200)
    assert np.array(img).sum() == 0


def test_sparkle_brightness_reacts_to_treble_and_beat() -> None:
    ed = _new_editor(sparkle_enabled=True)
    # A single anchor at a known, deterministic position/phase so the
    # twinkle term itself doesn't add noise to the assertion.
    ed._sparkle_anchors = [
        {'u': 0.5, 'v': 0.0, 'phase': 0.0, 'speed': 1.0, 'size': 1.0, 'color': (255, 255, 255)},
    ]

    ed._fx_time, ed._treble, ed._beat = 0.0, 0.0, 0.0
    img_quiet = Image.new('RGBA', (200, 40), (0, 0, 0, 0))
    ed._draw_sparkles(ImageDraw.Draw(img_quiet, 'RGBA'), 0, 0, 200, 40)

    ed._treble, ed._beat = 1.0, 1.0
    img_loud = Image.new('RGBA', (200, 40), (0, 0, 0, 0))
    ed._draw_sparkles(ImageDraw.Draw(img_loud, 'RGBA'), 0, 0, 200, 40)

    assert int(np.array(img_loud).sum()) > int(np.array(img_quiet).sum())


# ── _update_fx: audio ingestion + animation clocks ──────────────────────────

def test_update_fx_reads_audio_and_advances_clocks() -> None:
    ed = _new_editor()
    ed._dirty = False
    ed._update_fx(0.5, _audio(bass=0.5, treble=0.3, beat=0.8))
    assert ed._bass == pytest.approx(0.5)
    assert ed._treble == pytest.approx(0.3)
    assert ed._beat == pytest.approx(0.8)
    assert ed._fx_time == pytest.approx(0.5)
    assert ed._drip_phase > 0.0
    assert ed._dirty is True


def test_update_fx_without_audio_keeps_previous_levels() -> None:
    ed = _new_editor()
    ed._bass = 0.4
    ed._update_fx(0.1, None)
    assert ed._bass == pytest.approx(0.4)


def test_update_fx_does_not_force_dirty_when_fx_fully_disabled() -> None:
    ed = _new_editor(fx_enabled=False)
    ed._dirty = False
    ed._update_fx(0.5, _audio(bass=1.0, treble=1.0, beat=1.0))
    assert ed._dirty is False


# ── End-to-end render_overlay() with a real (headless) GL context ──────────

@pytest.fixture
def gl_ctx():
    moderngl = pytest.importorskip('moderngl')
    try:
        ctx = moderngl.create_standalone_context()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f'no headless GL context: {exc}')
    yield ctx
    ctx.release()


def test_render_overlay_end_to_end_with_audio(gl_ctx) -> None:
    api = MagicMock()
    api.ctx = gl_ctx
    ed = CTAEditor(api, [('Push the buttons!', '\U0001f44d')])
    ed.open()
    audio = _audio(bass=0.6, treble=0.7, beat=1.0)
    ed.render_overlay(640, 480, 1 / 60, audio)
    ed.render_overlay(640, 480, 1 / 60, audio)
    assert ed._tex is not None
    ed.close(save=False)
