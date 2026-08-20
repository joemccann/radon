# 002 — Shorten the last-price tick flash from 2.5s

- **Status**: TODO
- **Commit**: 78bcf138
- **Severity**: MEDIUM
- **Category**: Purpose & frequency + Easing & duration
- **Estimated scope**: 1 CSS file, 2 lines. Trivial.

## Problem

The green/red flash shown when a price ticks runs for **2.5 seconds** and fires on **every tick**. On a fast-ticking symbol a new tick re-triggers the flash before the previous fade has finished, so the highlight never settles — the opposite of the "confidence-increasing" feel the brand asks for. The flash's *purpose* is valid (indicate the price moved), but 2.5s is far too long for something this frequent; feedback of this kind should decay in well under a second.

Current code (`web/app/globals.css`):

```css
/* :3113 */ .last-price-up   { animation: last-price-flash-up 2.5s ease-out; }
/* :3117 */ .last-price-down { animation: last-price-flash-down 2.5s ease-out; }
```

The keyframes themselves are fine (they fade a `background` tint to `transparent` — `background` is paint, cheap; no layout, no `scale(0)`):

```css
/* :3121 */ @keyframes last-price-flash-up   { 0% { background: color-mix(in srgb, var(--positive) 15%, transparent); } 100% { background: transparent; } }
/* :3130 */ @keyframes last-price-flash-down { 0% { background: color-mix(in srgb, var(--negative) 15%, transparent); } 100% { background: transparent; } }
```

## Target

Reduce the duration to **800ms**, keep `ease-out`, keep the keyframes unchanged:

```css
.last-price-up   { animation: last-price-flash-up 800ms ease-out; }
.last-price-down { animation: last-price-flash-down 800ms ease-out; }
```

800ms is a starting target; the exact value must be confirmed by the feel check below against a live fast ticker (acceptable range 600–900ms — pick the shortest value at which the flash still reads as a distinct pulse).

## Repo conventions to follow

- Durations in this file are written in `ms` for sub-second values (e.g. `200ms`, `320ms`) — use `800ms`, not `0.8s`.
- Keep the existing `ease-out` timing keyword (these keyframe animations use the bare keyword, not the `--ease-out` token — leave that as-is; do not swap it).

## Steps

1. In `web/app/globals.css:3113`, change `2.5s` to `800ms` in the `.last-price-up` animation shorthand.
2. In `web/app/globals.css:3117`, change `2.5s` to `800ms` in the `.last-price-down` animation shorthand.

## Boundaries

- Do NOT edit the `@keyframes last-price-flash-up`/`-down` bodies.
- Do NOT change the `ease-out` keyword or the colors.
- Do NOT touch how the `.last-price-up`/`.last-price-down` classes are applied in JS.
- If the duration is no longer `2.5s` (drift since `78bcf138`), STOP and report.

## Verification

- **Mechanical**: `grep -n "last-price-flash-\(up\|down\) " web/app/globals.css` shows `800ms`, not `2.5s`.
- **Feel check**: run the app during market hours (`scripts/cloud.sh`) on a liquid, fast-ticking symbol (e.g. SPY/NVDA in a positions or chain table):
  - Each tick's flash pulses and fades within ~0.8s — you can perceive a distinct pulse, not a long lingering wash.
  - On a rapidly ticking cell the flashes read as crisp repeated pulses rather than a permanently-tinted cell that never returns to transparent.
  - If flashes still feel like they smear together, drop to 600ms and re-check; if a single flash is hard to notice, raise toward 900ms.
- **Done when**: the tint fully returns to transparent between typical ticks, and the value is the shortest in 600–900ms that still reads as a pulse.
