# Mobile order sheet — size-region scroll fix (2026-09-01)

Repro: on a 393x852 phone, sizing a structure in `MobileOrderTicket` (qty
steppers, +5/+10/+25/+50/+100 quick-add chips, leg rows) required hunting a
barely usable nested scroller while the RISK grid, payoff curve, and the
Review CTA held most of the viewport.

## Root cause

`MobileOrderTicket` passed the full `TicketRiskBlock` (RISK · ORDER TOTAL grid
plus the AT EXPIRY payoff curve) into `BottomSheet`'s `footer` slot. The
footer (`.m-sheet__footer`) is pinned and does not scroll, so on a two-leg
sheet it measured 405px — 48% of an 852px viewport — and the body scroller
(`.m-sheet__body-scroll`, the region that owns the size controls) was crushed
to 196px.

## Layout rule

**One sheet scroll.** Everything the operator sizes and prices the structure
with — legs, BUY/SELL flip, qty steppers, quick-add presets, quote chips,
limit stepper, TIF — AND the risk grid + payoff live in the body scroller, in
that order. The pinned footer is a compact thumb-zone: status, the two-line
teaser ("You'll pay … / Max loss … / Max gain …"), and the Clear / Review
row. Nothing that can grow (grids, curves, recaps) is ever pinned.

Measured at 393x852 (same harness, before → after):

| Region | Before | After |
| --- | --- | --- |
| Pinned footer height | 405px (48% of viewport) | 69px (8%) |
| Body scroller height | 196px | 532px |

## Artifacts

- `baseline/01-sheet-open.png` — legs crushed, RISK grid + payoff pinned.
- `baseline/02-size-region.png` — chips only reachable inside the nested gutter.
- `baseline/03-sheet-bottom.png` — body scrolled; footer still holds the instrument.
- `after/01-sheet-open.png` — both legs + steppers + all five chips tappable on open.
- `after/02-size-region.png` — size region at rest, no nested scroller.
- `after/03-sheet-bottom.png` — risk grid + payoff scrolled to, footer stays compact.

Captured with the `mobile-order-ticket.spec.ts` stub harness (AAPL 200/210
bull call spread, prices aborted), Playwright WebKit iPhone 15, light theme.

## Regression pins

- `web/tests/mobile-ticket-sheet-scroll-layout.test.tsx` — DOM contract: risk
  panel inside `.m-sheet__body-scroll` after the legs block, never inside
  `.m-sheet__footer`; footer keeps teaser + Clear/Review.
- `web/e2e/mobile-order-ticket.spec.ts` ("size controls own the sheet
  scroll") — geometry at 393x852: all five quick-add chips in-viewport on
  open, a real (not programmatic) click on +25, footer under 30% of the
  viewport.
