"""CTA message list editor modal overlay.

Full-screen modal rendered via PIL→GL texture blit.  Each row holds a text
field and an icon/emoji field with full web-textarea keyboard semantics
(arrow keys, Home/End, Ctrl+Arrow word-jump, Shift selection, Backspace/
Delete, Ctrl+Backspace, Ctrl+A, Tab/Shift-Tab focus).

Panel, field, row and button backgrounds are gradient rounded rectangles
(``_grad_rounded_rect`` / ``_fill_box``) rather than flat single-colour
fills, with an organic "drip" edge sag driven by bass/beat
(``_draw_drip_edge`` -- the same wave-plus-sag shape as banner-01's
``_draw_drippy_text``, applied to box geometry instead of glyphs) and
treble/beat-reactive "sparkle" glint accents along the panel border
(``_draw_sparkles``). All three are gated by the ``fx_enabled`` /
``drip_enabled`` / ``sparkle_enabled`` constructor flags -- sourced from the
``[cta]`` config's ``editor_fx`` / ``editor_drip`` / ``editor_sparkle`` /
``editor_sparkle_density`` keys via ``CTAController`` -- so disabling
``editor_fx`` falls back to the original flat-rectangle rendering.

Lifecycle:
    editor = CTAEditor(vj_api, messages)
    editor.open()
    # render loop + key handling happen automatically via registered subsystem
    # and text-input handler; when closed:
    result = editor.get_result()   # list[(text, icon)] on save, None on cancel
"""
from __future__ import annotations

import logging
import math
import random
from pathlib import Path
from typing import Any

from .text_field import TextField

log = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_OK = True
except Exception:
    _PIL_OK = False

try:
    import moderngl
    _MGL_OK = True
except Exception:
    _MGL_OK = False

try:
    import numpy as np
    _NP_OK = True
except Exception:
    _NP_OK = False

# ── Colour palette ────────────────────────────────────────────────────────────
_BG_OVERLAY   = (0,    0,   0,  205)   # full-screen scrim
_PANEL_TOP    = (14,   0,  34)          # panel gradient top
_PANEL_BOT    = (4,    0,  14)          # panel gradient bottom
_BORDER       = (220,  0, 175)          # neon pink border
_TITLE        = (255, 50, 220)          # hot pink title
_SUBTITLE     = (95,  75, 150)          # dim purple sub-line
_LABEL        = (155, 75, 255)          # column header purple
_TEXT         = (225, 215, 255)         # near-white body text
_DIM          = (100,  90, 135)         # inactive / dim
_ROW_SEL      = (40,   0,  85)          # selected-row background (flat fallback)
_ROW_SEL_TOP  = (58,   4, 118)          # selected-row gradient top
_ROW_SEL_BOT  = (20,   0,  46)          # selected-row gradient bottom
_FIELD_BG     = (8,    0,  20)          # input field background (flat fallback)
_FIELD_GRAD_TOP     = (22,   6,  42)    # idle field gradient top
_FIELD_GRAD_BOT     = (5,    0,  14)    # idle field gradient bottom
_FIELD_GRAD_TOP_ACT = (6,   30,  36)    # active field gradient top (cyan tint)
_FIELD_GRAD_BOT_ACT = (2,   10,  14)    # active field gradient bottom
_FIELD_ACTIVE = (0,  235, 255)          # active field border (cyan)
_FIELD_IDLE   = (75,   0, 150)          # idle field border (dark purple)
_CURSOR       = (0,  255, 255)          # insertion cursor (cyan)
_SEL_FILL     = (0,  100, 200, 110)    # selection highlight
_HELP_BAR     = (55,  45,  80)          # footer help-bar background
_HELP_TEXT    = (120, 100, 170)         # footer help text
_BTN_ADD      = (0,  135,  55)          # add button accent
_BTN_DEL      = (135,  0,  40)          # delete button accent
_BTN_SAVE     = (0,  165,  75)          # save button accent
_BTN_CANCEL   = (155,  25,  0)          # cancel button accent
_SPARKLE_A    = (255, 140, 235)         # pink glint accent
_SPARKLE_B    = (150, 245, 255)         # cyan glint accent

_BLINK_S   = 0.50   # cursor blink half-period
_MAX_ROWS  = 20
_PAD       = 16
_ROW_H     = 52
_FIELD_H   = 38
_ICON_W    = 96
_NUM_W     = 36
_FONT_SZ   = 21
_TITLE_SZ  = 28

# Corner radii for the "not just flat rectangles" panel/box redesign.
_RADIUS_PANEL = 18
_RADIUS_ROW   = 8
_RADIUS_FIELD = 8
_RADIUS_BTN   = 10

# Sparkle anchor count at density == 1.0.
_SPARKLE_BASE_COUNT = 18

# ── GLSL pass-through (blit PIL texture to screen) ───────────────────────────
_VERT = """
#version 330
in vec2 in_pos;
in vec2 in_uv;
out vec2 v_uv;
void main() { v_uv = in_uv; gl_Position = vec4(in_pos, 0.0, 1.0); }
"""
_FRAG = """
#version 330
uniform sampler2D iChannel0;
in vec2 v_uv;
out vec4 fragColor;
void main() { fragColor = texture(iChannel0, v_uv); }
"""
# Full-screen quad: each vertex is (x, y, u, v)
_QUAD = [
    (-1.0, -1.0, 0.0, 1.0),
    ( 1.0, -1.0, 1.0, 1.0),
    (-1.0,  1.0, 0.0, 0.0),
    ( 1.0, -1.0, 1.0, 1.0),
    ( 1.0,  1.0, 1.0, 0.0),
    (-1.0,  1.0, 0.0, 0.0),
]


def _rgb(t: tuple) -> tuple:
    """Return (r, g, b, 255) or (r, g, b, a) from a 3- or 4-tuple."""
    return t if len(t) == 4 else (*t, 255)


class CTAEditor:
    """Modal CTA message list editor using PIL blit + SDL text input.

    Registers itself as the subsystem ``'cta_editor'`` while open so
    ``render_overlay()`` is called each frame.  Registers a text input handler
    for SDL_TEXTINPUT character events.  Both registrations are cleaned up
    automatically on close.
    """

    def __init__(
        self,
        vj_api: Any,
        messages: list[tuple[str, str]],
        *,
        fx_enabled: bool = True,
        drip_enabled: bool = True,
        sparkle_enabled: bool = True,
        sparkle_density: float = 1.0,
    ) -> None:
        self._api = vj_api

        # Editable rows: (text_field, icon_field)
        self._rows: list[tuple[TextField, TextField]] = [
            (TextField(t, max_len=120), TextField(i, max_len=24))
            for t, i in (messages or [])
        ]
        if not self._rows:
            self._rows = [(TextField(max_len=120), TextField(max_len=24))]

        self._sel_row = 0
        self._sel_col = 0      # 0 = text, 1 = icon
        self._scroll  = 0      # first visible row index
        self._vis     = 6      # visible rows; updated each draw cycle

        self._result: list[tuple[str, str]] | None = None
        self._is_open = False
        self._dirty   = True

        # Cursor blink
        self._blink_acc  = 0.0
        self._cursor_vis = True

        # GL resources (lazy)
        self._prog: Any = None
        self._vbo:  Any = None
        self._vao:  Any = None
        self._tex:  Any = None
        self._tex_w = 0
        self._tex_h = 0

        # PIL fonts (lazy)
        self._font:  Any = None
        self._bfont: Any = None   # bold / title font

        # ── Visual FX (drip / sparkle / gradient panels) ────────────────────
        # ``fx_enabled`` is the master switch; drip/sparkle are only ever on
        # when it is. Sourced from [cta] config via CTAController -- see
        # that module's docstring for the editor_fx / editor_drip /
        # editor_sparkle / editor_sparkle_density keys.
        self._fx_enabled     = bool(fx_enabled)
        self._drip_enabled   = self._fx_enabled and bool(drip_enabled)
        self._sparkle_enabled = self._fx_enabled and bool(sparkle_enabled)
        self._sparkle_density = max(0.0, float(sparkle_density))

        # Audio-reactive state, refreshed each frame from the AudioData
        # passed into render_overlay() (mirrors banner-01's _draw_drippy_text
        # bass/beat pattern: bass widens the wave, beat adds an extra sag).
        self._bass   = 0.0
        self._treble = 0.0
        self._beat   = 0.0
        self._fx_time    = 0.0
        self._drip_phase = 0.0

        # Sparkle anchors: generated lazily (once per open()) along the
        # panel border in panel-local fractional coordinates so they track
        # panel geometry without needing to be recomputed on resize.
        self._sparkle_anchors: list[dict] | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def is_open(self) -> bool:
        return self._is_open

    def get_result(self) -> list[tuple[str, str]] | None:
        return self._result

    def open(self) -> None:
        """Open the editor and register hooks."""
        self._is_open   = True
        self._dirty     = True
        self._blink_acc = 0.0
        self._cursor_vis = True
        # Fresh sparkle placement + FX clock each time the editor opens, so
        # the panel doesn't look identical session to session.
        self._sparkle_anchors = None
        self._fx_time    = 0.0
        self._drip_phase = 0.0
        self._api.register_text_input_handler('cta_editor', self._on_text_input)
        self._api.start_text_input()
        self._api.register_subsystem('cta_editor', self)
        self._api.set_runtime_state('cta.editor_open', True)

    def close(self, *, save: bool) -> None:
        """Close the editor, optionally saving the result."""
        if save:
            self._result = [
                (r[0].value.strip(), r[1].value.strip())
                for r in self._rows
                if r[0].value.strip()
            ]
        else:
            self._result = None
        self._is_open = False
        self._api.unregister_text_input_handler('cta_editor')
        self._api.stop_text_input()
        self._api.unregister_subsystem('cta_editor')
        self._api.set_runtime_state('cta.editor_open', False)
        self._destroy_gl()

    # ── Key / text input ───────────────────────────────────────────────────────

    def handle_key(self, sym: int, mod: int) -> 'str | None | bool':
        """Handle all keyboard input while editor is open."""
        if not self._is_open:
            return False
        import sdl2  # noqa: PLC0415
        ctrl  = bool(mod & sdl2.KMOD_CTRL)
        shift = bool(mod & sdl2.KMOD_SHIFT)
        field = self._active_field()

        # Global editor controls
        if sym == sdl2.SDLK_ESCAPE and not ctrl:
            self.close(save=False)
            return None
        if sym == sdl2.SDLK_s and ctrl:
            self.close(save=True)
            return 'CTA: messages saved'
        if sym == sdl2.SDLK_n and ctrl:
            self._add_row()
            return None
        if sym == sdl2.SDLK_DELETE and ctrl:
            self._del_row()
            return None

        # Row navigation
        if sym == sdl2.SDLK_UP and not ctrl:
            self._move_row(-1)
            return None
        if sym == sdl2.SDLK_DOWN and not ctrl:
            self._move_row(1)
            return None
        if sym == sdl2.SDLK_RETURN or sym == sdl2.SDLK_KP_ENTER:
            if not ctrl:
                self._move_row(1)
                self._sel_col = 0
                self._dirty = True
                return None

        # Tab: cycle field within row, spill to next/prev row
        if sym == sdl2.SDLK_TAB:
            if shift:
                if self._sel_col > 0:
                    self._sel_col = 0
                elif self._sel_row > 0:
                    self._sel_row -= 1
                    self._sel_col = 1
                    self._clamp_scroll()
            else:
                if self._sel_col == 0:
                    self._sel_col = 1
                elif self._sel_row < len(self._rows) - 1:
                    self._sel_row += 1
                    self._sel_col = 0
                    self._clamp_scroll()
            self._dirty = True
            return None

        # Field-level cursor / editing
        if field is not None:
            changed = False
            if sym == sdl2.SDLK_LEFT:
                field.move_word_left(select=shift) if ctrl else field.move_left(select=shift)
                changed = True
            elif sym == sdl2.SDLK_RIGHT:
                field.move_word_right(select=shift) if ctrl else field.move_right(select=shift)
                changed = True
            elif sym == sdl2.SDLK_HOME:
                field.move_home(select=shift)
                changed = True
            elif sym == sdl2.SDLK_END:
                field.move_end(select=shift)
                changed = True
            elif sym == sdl2.SDLK_BACKSPACE:
                field.delete_word_before() if ctrl else field.delete_before()
                changed = True
            elif sym == sdl2.SDLK_DELETE and not ctrl:
                field.delete_after()
                changed = True
            elif sym == sdl2.SDLK_a and ctrl:
                field.select_all()
                changed = True
            if changed:
                self._dirty = True
                return None

        return None  # consume all keys while open

    def _on_text_input(self, text: str) -> None:
        if not self._is_open:
            return
        field = self._active_field()
        if field is not None and field.insert(text):
            self._dirty = True

    # ── Row management ─────────────────────────────────────────────────────────

    def _active_field(self) -> TextField | None:
        if 0 <= self._sel_row < len(self._rows):
            return self._rows[self._sel_row][self._sel_col]
        return None

    def _move_row(self, delta: int) -> None:
        self._sel_row = max(0, min(len(self._rows) - 1, self._sel_row + delta))
        self._clamp_scroll()
        self._dirty = True

    def _add_row(self) -> None:
        if len(self._rows) >= _MAX_ROWS:
            return
        pos = self._sel_row + 1
        self._rows.insert(pos, (TextField(max_len=120), TextField(max_len=24)))
        self._sel_row = pos
        self._sel_col = 0
        self._clamp_scroll()
        self._dirty = True

    def _del_row(self) -> None:
        if len(self._rows) <= 1:
            self._rows = [(TextField(max_len=120), TextField(max_len=24))]
        else:
            self._rows.pop(self._sel_row)
            self._sel_row = min(self._sel_row, len(self._rows) - 1)
        self._clamp_scroll()
        self._dirty = True

    def _clamp_scroll(self) -> None:
        if self._sel_row < self._scroll:
            self._scroll = self._sel_row
        elif self._sel_row >= self._scroll + self._vis:
            self._scroll = self._sel_row - self._vis + 1
        self._scroll = max(0, self._scroll)

    # ── render_overlay (called each frame as registered subsystem) ─────────────

    def render_overlay(self, width: int, height: int, dt: float, audio: Any = None, *_: Any) -> None:
        if not self._is_open or not _PIL_OK or not _MGL_OK or not _NP_OK:
            return

        self._update_fx(dt, audio)

        self._blink_acc += dt
        if self._blink_acc >= _BLINK_S:
            self._blink_acc -= _BLINK_S
            self._cursor_vis = not self._cursor_vis
            self._dirty = True

        if not self._dirty and self._tex is not None:
            self._blit()
            return

        self._dirty = False
        self._ensure_fonts()
        img  = self._draw(width, height)
        data = np.ascontiguousarray(np.array(img, dtype=np.uint8)).tobytes()

        ctx = self._api.ctx
        if ctx is None:
            return

        if self._tex is None or self._tex_w != width or self._tex_h != height:
            if self._tex is not None:
                self._tex.release()
            self._tex = ctx.texture((width, height), 4, data=data)
            self._tex.filter = moderngl.LINEAR, moderngl.LINEAR
            self._tex_w, self._tex_h = width, height
        else:
            self._tex.write(data)

        self._ensure_gl(ctx)
        self._blit()

    # ── Visual FX state ──────────────────────────────────────────────────────

    def _update_fx(self, dt: float, audio: Any) -> None:
        """Refresh audio-reactive drip/sparkle state from the current frame's
        AudioData (bass_n/treble_n/beat, same normalised signals banner-01's
        drip pattern uses). Marks the frame dirty so the animation actually
        advances every frame instead of only redrawing on interaction."""
        if audio is not None:
            self._bass   = float(getattr(audio, 'bass_n', 0.0) or 0.0)
            self._treble = float(getattr(audio, 'treble_n', 0.0) or 0.0)
            self._beat   = float(getattr(audio, 'beat', 0.0) or 0.0)

        dt = max(0.0, float(dt))
        self._fx_time += dt
        # Bass widens the drip wave, beat adds an extra sag pulse -- mirrors
        # banner-01's _draw_drippy_text drip_phase advance.
        self._drip_phase += dt * (1.15 + self._bass * 2.4 + self._beat * 1.6)

        if self._drip_enabled or self._sparkle_enabled:
            self._dirty = True

    def _init_sparkles(self) -> None:
        """Lazily scatter sparkle anchors along the panel border in
        panel-local fractional (u, v) coordinates, so they track panel
        geometry without needing to be regenerated on resize."""
        self._sparkle_anchors = []
        if not self._sparkle_enabled:
            return
        count = max(0, int(round(_SPARKLE_BASE_COUNT * self._sparkle_density)))
        rng = random.Random()
        anchors: list[dict] = []
        for _ in range(count):
            edge = rng.randrange(4)
            jitter = rng.uniform(-0.015, 0.015)
            if edge == 0:      # top edge
                u, v = rng.uniform(0.03, 0.97), 0.0 + jitter
            elif edge == 1:    # bottom edge
                u, v = rng.uniform(0.03, 0.97), 1.0 + jitter
            elif edge == 2:    # left edge
                u, v = 0.0 + jitter, rng.uniform(0.05, 0.95)
            else:              # right edge
                u, v = 1.0 + jitter, rng.uniform(0.05, 0.95)
            anchors.append({
                'u': u, 'v': v,
                'phase': rng.uniform(0.0, math.tau),
                'speed': rng.uniform(0.6, 1.6),
                'size': rng.uniform(0.7, 1.3),
                'color': _SPARKLE_A if rng.random() < 0.5 else _SPARKLE_B,
            })
        self._sparkle_anchors = anchors

    def _draw_sparkles(
        self, draw: 'ImageDraw.ImageDraw', px: int, py: int, pw: int, ph: int,
    ) -> None:
        """Draw glinting accent particles along the panel border, brightness
        driven by treble (steady twinkle) and beat (a flash on every hit)."""
        if not self._sparkle_enabled:
            return
        if self._sparkle_anchors is None:
            self._init_sparkles()
        if not self._sparkle_anchors:
            return

        treble_boost = 0.35 + 0.65 * self._treble
        beat_boost = self._beat
        for a in self._sparkle_anchors:
            twinkle = 0.5 + 0.5 * math.sin(self._fx_time * a['speed'] * 3.0 + a['phase'])
            bright = twinkle * treble_boost + beat_boost * 0.6
            if bright < 0.18:
                continue
            bright = min(1.0, bright)
            x = px + a['u'] * pw
            y = py + a['v'] * ph
            size = (2.0 + 3.5 * bright) * a['size']
            alpha = int(60 + 195 * bright)
            col = (*a['color'], alpha)
            draw.line([(x - size, y), (x + size, y)], fill=col, width=1)
            draw.line([(x, y - size), (x, y + size)], fill=col, width=1)
            if bright > 0.55:
                d = size * 0.55
                draw.line([(x - d, y - d), (x + d, y + d)], fill=col, width=1)
                draw.line([(x - d, y + d), (x + d, y - d)], fill=col, width=1)

    def _draw_drip_edge(
        self,
        draw: 'ImageDraw.ImageDraw',
        x0: float, x1: float, y: float,
        color: tuple,
        width: int = 1,
        amp: float = 1.0,
        segments: int = 12,
    ) -> None:
        """Draw a horizontal edge with an organic bass/beat-driven sag -- the
        "drip". Same wave-plus-sag shape as banner-01's _draw_drippy_text,
        applied to box-edge geometry instead of glyph baselines so the box
        itself looks like it's gently oozing rather than a static rectangle."""
        if not self._drip_enabled or x1 <= x0:
            return
        wave_amp = (1.1 + self._bass * 3.2) * amp
        sag_amp = (self._beat * 4.5) * amp
        pts = []
        for i in range(segments + 1):
            t = i / segments
            x = x0 + (x1 - x0) * t
            wave = math.sin(self._drip_phase + t * 5.4) * wave_amp
            sag = sag_amp * (0.5 + 0.5 * math.sin(self._drip_phase * 0.33 + t * 4.1))
            pts.append((x, y + wave + sag))
        a = color[3] if len(color) == 4 else 255
        col = (color[0], color[1], color[2], a)
        draw.line(pts, fill=col, width=width, joint='curve')

    def _grad_rounded_rect(
        self, w: int, h: int, top_rgb: tuple, bot_rgb: tuple, alpha: int, radius: int,
    ) -> 'Image.Image':
        """Return an RGBA image: a vertical-gradient fill clipped to a
        rounded rectangle. Used for panel/field/row/button backgrounds --
        the "more sophistication" upgrade over a single flat fill colour."""
        w = max(1, int(w))
        h = max(1, int(h))
        radius = max(0, min(int(radius), w // 2, h // 2))
        t = np.linspace(0.0, 1.0, h, dtype=np.float32).reshape(h, 1, 1)
        top = np.array(top_rgb[:3], dtype=np.float32).reshape(1, 1, 3)
        bot = np.array(bot_rgb[:3], dtype=np.float32).reshape(1, 1, 3)
        grad = top * (1.0 - t) + bot * t
        grad = np.broadcast_to(grad, (h, w, 3)).astype(np.uint8)
        img = Image.fromarray(grad, 'RGB').convert('RGBA')
        mask = Image.new('L', (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [(0, 0), (w - 1, h - 1)], radius=radius, fill=int(max(0, min(255, alpha))),
        )
        img.putalpha(mask)
        return img

    def _fill_box(
        self,
        img: 'Image.Image', draw: 'ImageDraw.ImageDraw',
        x: float, y: float, w: float, h: float,
        flat_color: tuple, top_rgb: tuple, bot_rgb: tuple, alpha: int, radius: int,
    ) -> None:
        """Fill a box background: a flat rectangle when FX are disabled
        (the original 1980s-boxy look, kept as a working fallback), or a
        gradient rounded rect when enabled."""
        if self._fx_enabled:
            grad = self._grad_rounded_rect(w, h, top_rgb, bot_rgb, alpha, radius)
            img.alpha_composite(grad, (int(x), int(y)))
        else:
            draw.rectangle([(x, y), (x + w, y + h)], fill=flat_color)

    def _stroke_box(
        self,
        draw: 'ImageDraw.ImageDraw',
        x: float, y: float, w: float, h: float,
        color: tuple, width: int, radius: int,
    ) -> None:
        """Outline a box: sharp rectangle when FX are disabled, rounded
        rectangle when enabled."""
        if self._fx_enabled:
            draw.rounded_rectangle(
                [(x, y), (x + w, y + h)], outline=color, width=width, radius=max(0, radius),
            )
        else:
            draw.rectangle([(x, y), (x + w, y + h)], outline=color, width=width)

    # ── GL helpers ─────────────────────────────────────────────────────────────

    def _ensure_gl(self, ctx: Any) -> None:
        if self._prog is not None:
            return
        self._prog = ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG)
        self._vbo  = ctx.buffer(reserve=6 * 4 * 4)
        self._vao  = ctx.vertex_array(self._prog, [(self._vbo, '2f 2f', 'in_pos', 'in_uv')])

    def _blit(self) -> None:
        if None in (self._prog, self._vao, self._vbo, self._tex):
            return
        import numpy as np  # noqa: PLC0415 (guard: already checked _NP_OK above)
        data = np.array(_QUAD, dtype=np.float32).tobytes()
        self._vbo.write(data)
        self._tex.use(location=0)
        self._prog['iChannel0'].value = 0
        ctx = self._api.ctx
        ctx.enable(moderngl.BLEND)
        self._vao.render(moderngl.TRIANGLES, vertices=6)

    def _destroy_gl(self) -> None:
        for attr in ('_vao', '_vbo', '_prog', '_tex'):
            obj = getattr(self, attr, None)
            if obj is not None:
                obj.release()
                setattr(self, attr, None)

    # ── PIL font loading ────────────────────────────────────────────────────────

    def _ensure_fonts(self) -> None:
        if self._font is not None:
            return
        _candidates = [
            '/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf',
            '/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf',
            '/usr/share/fonts/google-noto/NotoSans-Regular.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
            '/usr/share/fonts/TTF/DejaVuSans.ttf',
            '/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf',
            '/usr/share/fonts/dejavu/DejaVuSans.ttf',
        ]
        font_path = next((p for p in _candidates if Path(p).exists()), None)
        try:
            if font_path:
                self._font  = ImageFont.truetype(font_path, size=_FONT_SZ)
                self._bfont = ImageFont.truetype(font_path, size=_TITLE_SZ)
            else:
                self._font  = ImageFont.load_default()
                self._bfont = self._font
        except Exception:
            self._font  = ImageFont.load_default()
            self._bfont = self._font

    # ── PIL drawing ─────────────────────────────────────────────────────────────

    def _draw(self, vw: int, vh: int) -> 'Image.Image':
        img  = Image.new('RGBA', (vw, vh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img, 'RGBA')

        # Full-screen scrim
        draw.rectangle([(0, 0), (vw - 1, vh - 1)], fill=_BG_OVERLAY)

        # Panel geometry
        pw = max(540, int(vw * 0.58))
        ph = max(440, int(vh * 0.76))
        px = (vw - pw) // 2
        py = (vh - ph) // 2

        # Recompute visible row count from available pixel height
        header_h = _TITLE_SZ + _FONT_SZ + _PAD * 4 + 2   # title + subtitle + dividers
        footer_h = _FONT_SZ + _PAD * 4                     # buttons + help bar
        self._vis = max(3, (ph - header_h - footer_h) // _ROW_H)

        # Panel gradient fill, clipped to rounded corners when FX are on --
        # replaces the old flat-topped rectangle with a softer silhouette.
        self._fill_box(
            img, draw, px, py, pw, ph,
            _PANEL_TOP, _PANEL_TOP, _PANEL_BOT, 255, _RADIUS_PANEL,
        )

        # Glow outline (3 passes from diffuse to crisp)
        for ofs, a in ((6, 35), (3, 80), (1, 200)):
            self._stroke_box(
                draw, px - ofs, py - ofs, pw + ofs * 2 - 1, ph + ofs * 2 - 1,
                (*_BORDER, a), 1, _RADIUS_PANEL + ofs,
            )

        # Drip accent beneath the panel's bottom edge -- the box looks like
        # it's gently oozing rather than a static rectangle.
        self._draw_drip_edge(
            draw, px + _RADIUS_PANEL, px + pw - _RADIUS_PANEL, py + ph,
            _BORDER, width=2, amp=1.1,
        )

        # ── Header ───────────────────────────────────────────────────────────
        title = '✦  CTA Messages Editor  ✦'
        try:
            tb = draw.textbbox((0, 0), title, font=self._bfont)
            tx = px + (pw - (tb[2] - tb[0])) // 2
            ty = py + _PAD
        except Exception:
            tx, ty = px + _PAD, py + _PAD
        draw.text((tx, ty), title, font=self._bfont, fill=_TITLE)

        sub = 'F9 cycles  •  F10 last song  •  Shift+F10 one more  •  Alt+F9 edit'
        try:
            sb = draw.textbbox((0, 0), sub, font=self._font)
            sx = px + (pw - (sb[2] - sb[0])) // 2
            sy = ty + _TITLE_SZ + 4
        except Exception:
            sx, sy = px + _PAD, ty + _TITLE_SZ + 4
        draw.text((sx, sy), sub, font=self._font, fill=_SUBTITLE)

        div_y = sy + _FONT_SZ + _PAD
        draw.line([(px + 2, div_y), (px + pw - 2, div_y)], fill=(*_BORDER, 160), width=1)

        # ── Column headers ────────────────────────────────────────────────────
        hdr_y  = div_y + 6
        txt_x  = px + _PAD + _NUM_W + 6
        icon_x = px + pw - _ICON_W - _PAD
        txt_w  = icon_x - txt_x - _PAD
        draw.text((px + _PAD + 6, hdr_y), '#',       font=self._font, fill=_LABEL)
        draw.text((txt_x,         hdr_y), 'Message', font=self._font, fill=_LABEL)
        draw.text((icon_x,        hdr_y), 'Icon',    font=self._font, fill=_LABEL)

        # ── Message rows ──────────────────────────────────────────────────────
        rows_y = hdr_y + _FONT_SZ + 6
        end_i  = min(len(self._rows), self._scroll + self._vis)

        for i in range(self._scroll, end_i):
            tf, if_ = self._rows[i]
            ry = rows_y + (i - self._scroll) * _ROW_H
            sel = (i == self._sel_row)

            if sel:
                row_x, row_y = px + 3, ry - 3
                row_w, row_h = pw - 6, _FIELD_H + 6
                self._fill_box(
                    img, draw, row_x, row_y, row_w, row_h,
                    _ROW_SEL, _ROW_SEL_TOP, _ROW_SEL_BOT, 235, _RADIUS_ROW,
                )
                # Selected row glow
                for ofs, a in ((3, 25), (1, 100)):
                    self._stroke_box(
                        draw, row_x - ofs, row_y - ofs, row_w + ofs * 2, row_h + ofs * 2,
                        (*_BORDER, a), 1, _RADIUS_ROW + ofs,
                    )

            # Row number
            num_col = _LABEL if sel else _DIM
            draw.text((px + _PAD + 6, ry + (_FIELD_H - _FONT_SZ) // 2), str(i + 1),
                      font=self._font, fill=num_col)

            # Text field
            tf_active = sel and self._sel_col == 0
            self._draw_field(img, draw, tf, txt_x,  ry, txt_w,  _FIELD_H, active=tf_active,
                             show_cursor=tf_active and self._cursor_vis)

            # Icon field
            if_active = sel and self._sel_col == 1
            self._draw_field(img, draw, if_, icon_x, ry, _ICON_W, _FIELD_H, active=if_active,
                             show_cursor=if_active and self._cursor_vis)

        # Scroll indicator
        total = len(self._rows)
        if total > self._vis:
            bar_h = self._vis * _ROW_H
            bar_x = px + pw - 8
            draw.rectangle([(bar_x, rows_y), (bar_x + 4, rows_y + bar_h)],
                           fill=(30, 0, 60, 220))
            thumb_h = max(18, bar_h * self._vis // total)
            thumb_y = rows_y + (bar_h - thumb_h) * self._scroll // max(1, total - self._vis)
            draw.rectangle([(bar_x, thumb_y), (bar_x + 4, thumb_y + thumb_h)],
                           fill=(*_BORDER, 210))

        # ── Footer divider ────────────────────────────────────────────────────
        btn_y = py + ph - _FONT_SZ - _PAD * 4
        draw.line([(px, btn_y - 8), (px + pw, btn_y - 8)], fill=(*_BORDER, 80), width=1)

        # ── Action buttons ────────────────────────────────────────────────────
        btns = [
            ('＋ Add',   _BTN_ADD,    'Ctrl+N'),
            ('✕ Del',    _BTN_DEL,    'Ctrl+Del'),
            ('✔ Save',   _BTN_SAVE,   'Ctrl+S'),
            ('✕ Cancel', _BTN_CANCEL, 'Esc'),
        ]
        btn_w = 114
        btn_gap = 10
        total_btn_w = len(btns) * btn_w + (len(btns) - 1) * btn_gap
        bx = px + (pw - total_btn_w) // 2

        for label, color, shortcut in btns:
            btn_h = 34
            flat_fill = (color[0] // 5, color[1] // 5, color[2] // 5, 220)
            top_c = (min(255, color[0] // 2 + 45), min(255, color[1] // 2 + 45), min(255, color[2] // 2 + 45))
            bot_c = (color[0] // 7, color[1] // 7, color[2] // 7)
            # Button glow halo, then gradient fill, then crisp border.
            for ofs, a in ((5, 18), (3, 35), (1, 70)):
                self._stroke_box(
                    draw, bx - ofs, btn_y - ofs, btn_w + ofs * 2, btn_h + ofs * 2,
                    (*color, a), 1, _RADIUS_BTN + ofs,
                )
            self._fill_box(
                img, draw, bx, btn_y, btn_w, btn_h,
                flat_fill, top_c, bot_c, 225, _RADIUS_BTN,
            )
            self._stroke_box(draw, bx, btn_y, btn_w, btn_h, (*color, 200), 1, _RADIUS_BTN)
            self._draw_drip_edge(
                draw, bx + _RADIUS_BTN, bx + btn_w - _RADIUS_BTN, btn_y + btn_h,
                color, width=1, amp=0.65,
            )
            # Label
            try:
                lb = draw.textbbox((0, 0), label, font=self._font)
                lx = bx + (btn_w - (lb[2] - lb[0])) // 2
                ly = btn_y + (34 - (lb[3] - lb[1])) // 2
            except Exception:
                lx, ly = bx + 8, btn_y + 6
            draw.text((lx, ly), label, font=self._font, fill=(*color, 230))
            # Shortcut hint below button
            try:
                shb = draw.textbbox((0, 0), shortcut, font=self._font)
                shx = bx + (btn_w - (shb[2] - shb[0])) // 2
            except Exception:
                shx = bx + 6
            draw.text((shx, btn_y + 38), shortcut, font=self._font, fill=_HELP_TEXT)
            bx += btn_w + btn_gap

        # ── Help bar ──────────────────────────────────────────────────────────
        help_y = py + ph - _PAD - 2
        help_text = '↑↓ row  •  Tab/Shift-Tab field  •  ←→ Home/End cursor  •  Ctrl+← word'
        try:
            hb = draw.textbbox((0, 0), help_text, font=self._font)
            hx = px + (pw - (hb[2] - hb[0])) // 2
        except Exception:
            hx = px + _PAD
        draw.text((hx, help_y), help_text, font=self._font, fill=_HELP_TEXT)

        # Sparkle accents drawn last so glints sit on top of the whole panel.
        self._draw_sparkles(draw, px, py, pw, ph)

        return img

    # ── Field renderer ──────────────────────────────────────────────────────────

    def _draw_field(
        self,
        img: 'Image.Image',
        draw: 'ImageDraw.ImageDraw',
        field: TextField,
        fx: int, fy: int, fw: int, fh: int,
        active: bool,
        show_cursor: bool,
    ) -> None:
        pad   = 5
        inner = fw - pad * 2
        font  = self._font

        # Background + border
        border_c = _FIELD_ACTIVE if active else _FIELD_IDLE
        top_c, bot_c = (
            (_FIELD_GRAD_TOP_ACT, _FIELD_GRAD_BOT_ACT) if active
            else (_FIELD_GRAD_TOP, _FIELD_GRAD_BOT)
        )
        self._fill_box(img, draw, fx, fy, fw, fh, _FIELD_BG, top_c, bot_c, 235, _RADIUS_FIELD)
        self._stroke_box(draw, fx, fy, fw, fh, (*border_c, 220), 1, _RADIUS_FIELD)
        if active:
            # Subtle inner glow
            self._stroke_box(
                draw, fx + 1, fy + 1, fw - 2, fh - 2,
                (*_FIELD_ACTIVE, 50), 1, max(0, _RADIUS_FIELD - 1),
            )
            # Focused field drips a little more visibly than idle ones --
            # the box you're editing is the one that visibly "oozes".
            self._draw_drip_edge(
                draw, fx + _RADIUS_FIELD, fx + fw - _RADIUS_FIELD, fy + fh,
                border_c, width=1, amp=0.8,
            )

        # Compute view_start so cursor stays visible
        vs = getattr(field, 'view_start', 0)
        cursor = field.cursor
        try:
            pre  = field.value[vs:cursor]
            prbb = draw.textbbox((0, 0), pre, font=font)
            pre_w = prbb[2] - prbb[0]

            # Scroll right: cursor off right edge
            while pre_w > inner - 4 and vs < cursor:
                vs += 1
                pre  = field.value[vs:cursor]
                prbb = draw.textbbox((0, 0), pre, font=font)
                pre_w = prbb[2] - prbb[0]

            # Scroll left: cursor past left edge
            while vs > 0:
                ns = vs - 1
                pre2 = field.value[ns:cursor]
                bb2  = draw.textbbox((0, 0), pre2, font=font)
                if bb2[2] - bb2[0] <= inner - 4:
                    vs = ns
                else:
                    break
        except Exception:
            pre_w = 0

        field.view_start = vs

        tx = fx + pad
        ty = fy + (fh - _FONT_SZ) // 2

        # Selection highlight
        sr = field.sel_range()
        if sr is not None and active:
            lo, hi = sr
            vis_lo = max(lo, vs)
            vis_hi = min(hi, len(field.value))
            if vis_lo < vis_hi:
                try:
                    before_sel = field.value[vs:vis_lo]
                    sel_text   = field.value[vis_lo:vis_hi]
                    bbs = draw.textbbox((0, 0), before_sel, font=font)
                    bbt = draw.textbbox((0, 0), sel_text,   font=font)
                    sx0 = tx + (bbs[2] - bbs[0])
                    sx1 = sx0 + (bbt[2] - bbt[0])
                    draw.rectangle(
                        [(max(fx + 1, sx0), fy + 2), (min(fx + fw - 1, sx1), fy + fh - 2)],
                        fill=_SEL_FILL,
                    )
                except Exception:
                    pass

        # Visible text (clipped to field width)
        vis_text = field.value[vs:]
        try:
            while vis_text:
                bb = draw.textbbox((0, 0), vis_text, font=font)
                if (bb[2] - bb[0]) <= inner:
                    break
                vis_text = vis_text[:-1]
        except Exception:
            pass
        draw.text((tx, ty), vis_text, font=font, fill=_TEXT)

        # Overflow indicators
        if vs > 0:
            draw.text((fx + 1, ty), '‹', font=font, fill=_DIM)
        try:
            full_bb = draw.textbbox((0, 0), field.value[vs:], font=font)
            if (full_bb[2] - full_bb[0]) > inner:
                draw.text((fx + fw - _FONT_SZ + 2, ty), '›', font=font, fill=_DIM)
        except Exception:
            pass

        # Cursor bar
        if show_cursor and active:
            try:
                cbb = draw.textbbox((0, 0), field.value[vs:cursor], font=font)
                cx  = tx + (cbb[2] - cbb[0])
                cx  = min(cx, fx + fw - pad - 2)
                draw.line([(cx, fy + 3), (cx, fy + fh - 3)], fill=_CURSOR, width=2)
            except Exception:
                draw.line([(tx, fy + 3), (tx, fy + fh - 3)], fill=_CURSOR, width=2)
