# cta-01 — CTA Hype Overlay

Owner: Drop-in Maintainers
Version: 0.9.0
Status: active
Last updated: 2026-09-03

`cta-01` manages call-to-action (CTA) message cycling for the unicorn-viz
hype overlay.  It owns the message list, cooldown/cycle logic, and F9/F10
hotkeys.  The actual GL animation is rendered by the core `CTAOverlay` via
`VJApi.trigger_cta_custom()`.

## Hotkeys

| Key | Action |
|-----|--------|
| `F9` | Trigger next hype CTA (cycles configured messages) |
| `F10` | CTA: Last song |
| `Shift+F10` | CTA: One more song |

## Configuration

```toml
[cta]
enabled     = true      # master on/off
duration_s  = 4.5       # seconds each message stays on screen
cooldown_s  = 0.0       # minimum seconds between F9 triggers
mode        = "cycle"   # "cycle" (default) or "random"
seed        = 0         # RNG seed for random mode

# Custom message list — omit to use the built-in defaults.
# Each item is either "text|icon" or a {text, icon} table.
# messages = [
#   "Push the buttons!|👍",
#   "Follow for more!|🔔",
#   { text = "Share the love!", icon = "❤️" },
# ]

# Message editor (Alt+F9) visuals: gradient/rounded-corner panels, drip
# (bass/beat-driven organic edge sag on box borders) and sparkle
# (treble/beat-driven glinting accents along the panel border) instead of
# flat single-colour rectangles. editor_fx is the master switch — turning
# it off falls back to the original flat-rectangle look.
editor_fx              = true   # master enable for the editor's visual FX
editor_drip            = true   # animated organic edge sag on box borders
editor_sparkle         = true   # glinting accents along the panel border
editor_sparkle_density = 1.0    # multiplier on sparkle particle count
```

## Source

Private repository: `https://github.com/iDoMeteor/unicorn-viz-dropin-cta-01`

## Changelog

- **0.9.0** — **Control Room panel, P2 of
  `docs/planning/control-room-panel-registry-plan-2026-09-04.md`, and a real
  bug fix.** Registers a declarative `CTA` panel via
  `vj_api.register_operator_panel()` (guarded, degrades to no panel on an
  older core) with HYPE/LAST SONG/ONE MORE buttons wired to
  `trigger_hype()`/`trigger_song()` + `vj_api.trigger_cta_custom()` --
  the same real path the existing MIDI handlers already use. Replaces
  Control Room's hardcoded "DROP-INS" panel CTA/LAST SONG buttons, which
  called `vj_api.trigger_streaming_cta()`/`trigger_streaming_song_cta()` --
  **methods that never existed anywhere in the codebase**, so every press
  silently failed (caught by Control Room's own exception handler). The
  stateful "press again for one more" toggle Control Room kept on its own
  side is replaced by two independent buttons. 7 new tests.
- **0.8.0** — message editor panel/box redesign: gradient rounded-rect
  backgrounds (panel, fields, selected row, buttons) replace the old flat
  single-colour rectangles; drip (bass/beat-driven organic edge sag,
  mirroring banner-01's drippy-text pattern) and sparkle (treble/beat-driven
  glint accents along the panel border) added, both audio-reactive.
  Configurable via new `editor_fx` / `editor_drip` / `editor_sparkle` /
  `editor_sparkle_density` keys in `[cta]` (all default on; `editor_fx`
  falls back to the original flat look when disabled).
- **0.7.0** — initial versioned release.
