---
title: Design — 2D UI language (inspired by krillion.io)
date: 2026-11-07
source: krillion.io, inspected 2026-11-07 (computed styles + screenshot). Inspiration only: we
  borrow the design *language* (font, palette pattern, effects), never their logo, name,
  copy or assets.
applies_to: the 2D parts — exchange board, brokers panel, London VIX ticker, controls.
  The 3D twin stays as its own look.
---

# 2D UI language

**Feel:** a retro pixel/CRT trading terminal. Bright sky at the top fading into a deep-navy
"ocean" where the data lives. Chunky glowing pixel borders, wide-tracked uppercase labels,
small tag badges, and colour-coded tiers.

## Tokens
```css
:root {
  /* type */
  --font-pixel: "VT323", ui-monospace, monospace;   /* Google Fonts, OFL licence */
  --font-ui: -apple-system, system-ui, "Segoe UI", Roboto, sans-serif;

  /* surfaces (deep navy) */
  --bg-abyss: #050a14;
  --bg-deep:  #0d1830;
  --panel:    #101d33;
  --panel-2:  #12233d;
  --panel-glass: rgba(11, 21, 38, 0.85);

  /* text */
  --ink:       #e8f1ff;
  --ink-muted: #a8bad2;
  --ink-dim:   #7d93b8;
  --hairline:  #7d93b838;

  /* accents */
  --cyan: #4de3ff;   /* primary glow, live */
  --pink: #ff5d8f;   /* chromatic-aberration partner, alerts */

  /* tiers (reuse for world/broker states) */
  --tier-common: #8fa3c4;   /* market-maker worlds */
  --tier-live:   #4de3ff;   /* live world */
  --tier-hot:    #ff9f43;   /* price jumped this frame */
  --tier-rare:   #9d7bff;   /* broker's high-conviction world */
  --tier-top:    #ffd166;   /* top-priced world / leading broker */
  --tier-dead:   #3d4f6e;   /* delisted */
  --tier-alert:  #ff5252;   /* VIX spike */
}
```

## Patterns
- **Pixel button / panel border:** stacked box-shadows instead of CSS borders:
  ```css
  box-shadow: 0 0 0 3px var(--bg-abyss), 0 0 0 6px var(--cyan),
              0 6px 0 6px #123048, 0 0 26px rgba(77,227,255,.3);
  ```
- **Labels:** VT323, uppercase, letter-spacing ~0.14em (e.g. 28px with ~3.9px tracking).
  Secondary labels use `--ink-dim`.
- **Chromatic-aberration headline** (for the wordmark or the VIX number):
  ```css
  text-shadow: -2px 0 var(--cyan), 2px 0 var(--pink);
  ```
- **Tag badge:** a small cyan-filled pill with dark text, sitting on a panel's top edge
  ("LIVE", "DELISTED", "+12%").
- **Scanlines:** a faint repeating-linear-gradient overlay (1px lines at about 4% opacity) on the
  board only.
- **Sky → ocean gradient** as the page background: light at the top (the "real" London), deep
  navy below where the worlds trade. It's a natural metaphor: worlds sink as they die.

## Applying it to our screens
| Element | Treatment |
|---|---|
| Exchange board rows | `--panel` rows with tier colour on the ticker id. Delisted rows are dimmed to `--tier-dead` and slide down ("sink"), with the reason in `--ink-dim` |
| Price change | A `--tier-hot` flash on change; sparklines in `--cyan` |
| Broker leaderboard | The leading broker in `--tier-top`; each data-source icon as a pixel glyph; P&L bars in cyan/pink |
| London VIX ticker | A big chromatic-aberration number, `--tier-alert` glow on spikes |
| Buttons (Spawn, Place event, Bet) | Pixel button pattern, uppercase VT323 |

**Do not:** copy their logo, wordmark, fish illustrations, copy text or assets.
