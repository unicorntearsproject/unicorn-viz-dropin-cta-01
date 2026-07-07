"""CTA (Call-to-Action) hype overlay controller drop-in.

Owns the message list, cycling/cooldown state, and key dispatch for the
F9 / F10 / Shift+F10 CTA hotkeys.  Alt+F9 opens the live message editor.
Rendering is handled by the core CTAOverlay via VJApi.trigger_cta_custom().
The editor overlay is rendered via PIL blit (cta_editor.CTAEditor).

Config section: [cta]
  enabled      = true          # master enable/disable
  duration_s   = 4.5           # how long each CTA stays on screen
  cooldown_s   = 0.0           # minimum seconds between triggers
  mode         = "cycle"       # "cycle" or "random"
  seed         = 0             # RNG seed for random mode
  messages     = []            # list of {text, icon} dicts or "text|icon" strings;
                               # omit to use the built-in defaults
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any

log = logging.getLogger(__name__)

HELP_ENTRIES = [
    ('CTA', 'F9',        'CTA hype overlay (cycle messages)'),
    ('CTA', 'F10',       'CTA: Last song'),
    ('CTA', 'Shift+F10', 'CTA: One more song'),
    ('CTA', 'Alt+F9',    'Open CTA message editor'),
]

_DEFAULT_MESSAGES: list[tuple[str, str]] = [
    ('Push the buttons!', '\U0001f44d'),
    ('Do the thangs!', '\U0001f514'),
    ('Share the love!', '❤️ \U0001f4e4'),
]


class CTAController:
    """Manage CTA message cycling, the message editor, and hotkey dispatch.

    Instantiated at startup from the [cta] config section.  After construction,
    call set_vj_api() to inject the VJApi reference, then register handle_key()
    as a VJApi key handler.  The controller is registered as subsystem 'cta' so
    its render_overlay() is called every frame, which delegates to the editor
    when the editor is open.

    Thread safety: all methods called from the main GL thread only.
    """

    def __init__(self, cfg: dict) -> None:
        self._enabled   = bool(cfg.get('enabled', True))
        self._duration_s = max(0.2, float(cfg.get('duration_s', 4.5) or 4.5))
        self._cooldown_s = max(0.0, float(cfg.get('cooldown_s', 0.0) or 0.0))
        self._mode      = str(cfg.get('mode', 'cycle') or 'cycle').strip().lower()
        if self._mode not in {'cycle', 'random'}:
            self._mode = 'cycle'
        self._rng = random.Random(int(cfg.get('seed', 0) or 0))
        self._messages = self._parse_messages(cfg.get('messages', None))
        self._index = -1
        self._last_t = -1e9
        self._vj_api: Any = None
        self._editor: Any = None   # CTAEditor instance while open

    # ── Setup ──────────────────────────────────────────────────────────────────

    def set_vj_api(self, api: Any) -> None:
        """Inject the VJApi instance after construction."""
        self._vj_api = api
        try:
            api.register_midi_actions('CTA', [
                ('cta_hype',      'Trigger hype CTA'),
                ('cta_last_song', 'Trigger last-song CTA'),
                ('cta_one_more',  'Trigger one-more CTA'),
            ])
            api.register_midi_action_handler('cta_hype',      self._midi_cta_hype)
            api.register_midi_action_handler('cta_last_song', self._midi_cta_last_song)
            api.register_midi_action_handler('cta_one_more',  self._midi_cta_one_more)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger(__name__).debug('cta-01: MIDI registration failed: %s', exc)

    def _midi_cta_hype(self) -> None:
        """MIDI handler: fire the next hype CTA via vj_api."""
        payload = self.trigger_hype()
        if payload and self._vj_api is not None:
            self._vj_api.trigger_cta_custom(*payload)

    def _midi_cta_last_song(self) -> None:
        """MIDI handler: fire last-song CTA via vj_api."""
        payload = self.trigger_song(one_more=False)
        if payload and self._vj_api is not None:
            self._vj_api.trigger_cta_custom(*payload)

    def _midi_cta_one_more(self) -> None:
        """MIDI handler: fire one-more-song CTA via vj_api."""
        payload = self.trigger_song(one_more=True)
        if payload and self._vj_api is not None:
            self._vj_api.trigger_cta_custom(*payload)

    def _parse_messages(self, raw: object) -> list[tuple[str, str]]:
        """Parse message list from config, falling back to built-in defaults."""
        if not isinstance(raw, list):
            return list(_DEFAULT_MESSAGES)
        parsed: list[tuple[str, str]] = []
        for item in raw:
            text = ''
            icon = ''
            if isinstance(item, str):
                parts = item.split('|', 1)
                text = parts[0].strip()
                icon = parts[1].strip() if len(parts) > 1 else ''
            elif isinstance(item, dict):
                text = str(item.get('text', '') or '').strip()
                icon = str(item.get('icon', '') or '').strip()
            if text:
                parsed.append((text, icon))
        return parsed if parsed else list(_DEFAULT_MESSAGES)

    # ── Public state ───────────────────────────────────────────────────────────

    @property
    def editor_open(self) -> bool:
        """Return True while the CTA message editor modal is visible."""
        return self._editor is not None and bool(getattr(self._editor, 'is_open', False))

    # ── CTA triggering ─────────────────────────────────────────────────────────

    def trigger_hype(self) -> tuple[str, str, float] | None:
        """Return the next hype CTA payload (text, icon, duration_s), or None if blocked."""
        if not self._enabled or not self._messages:
            return None
        now = time.monotonic()
        if now - self._last_t < self._cooldown_s:
            return None
        if self._mode == 'random':
            idx = self._rng.randrange(len(self._messages))
        else:
            idx = (self._index + 1) % len(self._messages)
        self._index = idx
        self._last_t = now
        text, icon = self._messages[idx]
        return (text, icon, self._duration_s)

    def trigger_song(self, one_more: bool = False) -> tuple[str, str, float] | None:
        """Return a song-end CTA payload (text, icon, duration_s), or None if blocked."""
        if not self._enabled:
            return None
        now = time.monotonic()
        if now - self._last_t < self._cooldown_s:
            return None
        self._last_t = now
        text = 'One more song!' if one_more else 'Last song!'
        return (text, '\U0001f3b5', self._duration_s)

    # ── Editor lifecycle ───────────────────────────────────────────────────────

    def _open_editor(self) -> None:
        if self.editor_open:
            return
        try:
            from .cta_editor import CTAEditor  # noqa: PLC0415
        except Exception as exc:
            log.warning('CTAEditor unavailable: %s', exc)
            return
        self._editor = CTAEditor(self._vj_api, list(self._messages))
        self._editor.open()

    def _apply_editor_result(self) -> None:
        if self._editor is None:
            return
        result = self._editor.get_result()
        if result is not None:
            self._messages = result if result else list(_DEFAULT_MESSAGES)
            self._index = -1
            log.info('CTA messages updated (%d entries)', len(self._messages))
        self._editor = None

    # ── Key handler ────────────────────────────────────────────────────────────

    def handle_key(self, sym: int, mod: int) -> 'str | None | bool':
        """Dispatch CTA hotkeys.  When editor is open, route all keys to it."""
        import sdl2  # noqa: PLC0415
        ctrl  = bool(mod & sdl2.KMOD_CTRL)
        shift = bool(mod & sdl2.KMOD_SHIFT)
        alt   = bool(mod & sdl2.KMOD_ALT)

        # ── Editor mode: all keys go to the editor ─────────────────────────
        if self.editor_open:
            result = self._editor.handle_key(sym, mod)
            if not self._editor.is_open:
                self._apply_editor_result()
            return result

        # ── Alt+F9: open editor ────────────────────────────────────────────
        if sym == sdl2.SDLK_F9 and alt and not ctrl:
            self._open_editor()
            return None

        # ── F9 (no Ctrl/Alt): hype CTA ────────────────────────────────────
        if sym == sdl2.SDLK_F9 and not ctrl and not alt:
            payload = self.trigger_hype()
            if payload is None:
                return 'CTA: cooldown'
            text, icon, duration = payload
            if self._vj_api is not None:
                self._vj_api.trigger_cta_custom(text, icon, duration)
            return 'CTA triggered'

        # ── F10 (no Ctrl): song CTA ───────────────────────────────────────
        if sym == sdl2.SDLK_F10 and not ctrl:
            payload = self.trigger_song(one_more=shift)
            if payload is None:
                return 'CTA: cooldown'
            text, icon, duration = payload
            if self._vj_api is not None:
                self._vj_api.trigger_cta_custom(text, icon, duration)
            return 'CTA triggered'

        return False

    # ── render_overlay: delegate to editor when open ───────────────────────────

    def render_overlay(self, width: int, height: int, dt: float, *args: Any) -> None:
        """Render editor overlay; no-op when editor is closed."""
        if self._editor is not None:
            self._editor.render_overlay(width, height, dt, *args)
