"""CTA message list editor modal overlay.

Full-screen modal rendered via PIL→GL texture blit.  Each row holds a text
field and an icon/emoji field with full web-textarea keyboard semantics
(arrow keys, Home/End, Ctrl+Arrow word-jump, Shift selection, Backspace/
Delete, Ctrl+Backspace, Ctrl+A, Tab/Shift-Tab focus).

Lifecycle:
    editor = CTAEditor(vj_api, messages)
    editor.open()
    # render loop + key handling happen automatically via registered subsystem
    # and text-input handler; when closed:
    result = editor.get_result()   # list[(text, icon)] on save, None on cancel
"""
from __future__ import annotations

import logging
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
_ROW_SEL      = (40,   0,  85)          # selected-row background
_FIELD_BG     = (8,    0,  20)          # input field background
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

_BLINK_S   = 0.50   # cursor blink half-period
_MAX_ROWS  = 20
_PAD       = 16
_ROW_H     = 52
_FIELD_H   = 38
_ICON_W    = 96
_NUM_W     = 36
_FONT_SZ   = 21
_TITLE_SZ  = 28

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

    def __init__(self, vj_api: Any, messages: list[tuple[str, str]]) -> None:
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

    def render_overlay(self, width: int, height: int, dt: float, *_: Any) -> None:
        if not self._is_open or not _PIL_OK or not _MGL_OK or not _NP_OK:
            return

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

        # Panel gradient fill
        for y in range(ph):
            t = y / max(1, ph - 1)
            r = int(_PANEL_TOP[0] * (1 - t) + _PANEL_BOT[0] * t)
            g = int(_PANEL_TOP[1] * (1 - t) + _PANEL_BOT[1] * t)
            b = int(_PANEL_TOP[2] * (1 - t) + _PANEL_BOT[2] * t)
            draw.line([(px, py + y), (px + pw - 1, py + y)], fill=(r, g, b, 255))

        # Glow outline (3 passes from diffuse to crisp)
        for ofs, a in ((6, 35), (3, 80), (1, 200)):
            draw.rectangle(
                [(px - ofs, py - ofs), (px + pw + ofs - 1, py + ph + ofs - 1)],
                outline=(*_BORDER, a), width=1,
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
                # Selected row glow
                for ofs, a in ((3, 25), (1, 100)):
                    draw.rectangle(
                        [(px + 3 - ofs, ry - 3 - ofs), (px + pw - 3 + ofs, ry + _FIELD_H + 3 + ofs)],
                        outline=(*_BORDER, a), width=1,
                    )
                draw.rectangle(
                    [(px + 3, ry - 3), (px + pw - 3, ry + _FIELD_H + 3)],
                    fill=_ROW_SEL,
                )

            # Row number
            num_col = _LABEL if sel else _DIM
            draw.text((px + _PAD + 6, ry + (_FIELD_H - _FONT_SZ) // 2), str(i + 1),
                      font=self._font, fill=num_col)

            # Text field
            tf_active = sel and self._sel_col == 0
            self._draw_field(draw, tf, txt_x,  ry, txt_w,  _FIELD_H, active=tf_active,
                             show_cursor=tf_active and self._cursor_vis)

            # Icon field
            if_active = sel and self._sel_col == 1
            self._draw_field(draw, if_, icon_x, ry, _ICON_W, _FIELD_H, active=if_active,
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
            # Button background + border
            draw.rectangle([(bx, btn_y), (bx + btn_w, btn_y + 34)],
                           fill=(color[0] // 5, color[1] // 5, color[2] // 5, 220))
            draw.rectangle([(bx, btn_y), (bx + btn_w, btn_y + 34)],
                           outline=(*color, 200), width=1)
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

        return img

    # ── Field renderer ──────────────────────────────────────────────────────────

    def _draw_field(
        self,
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
        draw.rectangle([(fx, fy), (fx + fw, fy + fh)], fill=_FIELD_BG)
        draw.rectangle([(fx, fy), (fx + fw, fy + fh)], outline=(*border_c, 220), width=1)
        if active:
            # Subtle inner glow
            draw.rectangle([(fx + 1, fy + 1), (fx + fw - 1, fy + fh - 1)],
                           outline=(*_FIELD_ACTIVE, 50), width=1)

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
