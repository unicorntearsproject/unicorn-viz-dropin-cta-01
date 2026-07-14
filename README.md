# cta-01 — CTA Hype Overlay

Owner: Drop-in Maintainers
Version: 0.7.0
Status: active
Last updated: 2026-07-06

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
```

## Source

Private repository: `https://github.com/iDoMeteor/unicorn-viz-dropin-cta-01`

## Changelog

- **0.7.0** — initial versioned release.
