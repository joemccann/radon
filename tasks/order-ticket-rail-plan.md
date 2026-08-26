# Order entry redesign — 1a Docked Ticket Rail

Source: Claude Design project `77c08f17-d6a4-4b10-b2f5-f511bdb3ef9a`, `Canvas.dc.html`.
(The project is titled "News article dashboard redesign" — that title is stale. The canvas is
order entry: Turn 1 offers three explorations, Turn 2 develops 1a to mobile.)

Operator decisions (2026-08-25):
- Build **1a Docked Ticket Rail**, desktop first, then the Turn 2 mobile bottom sheet.
- Build the transmit gate **exactly as designed**: two-step verify-then-transmit, and an explicit
  unbounded-risk acknowledgement that arms the transmit button.

1b (Execution Dock) and 1c (Staged Review) are alternatives to 1a, not additional work. Do not build them.

## What the design changes

The chain's order builder moves from a block **below** the chain to a persistent **384px right dock**
beside it, so legs, price, risk, gate and CTA are all visible without scrolling while the chain stays
interactive. It also adds a verify step that did not exist.

Dock contents, top to bottom (this order is the design's information hierarchy — keep it):
1. `ORDER · <structure>` header + CLEAR
2. `PREFILLED FROM …` pill (existing behaviour)
3. LEGS list — SELL/BUY pill, `1× $245 Put`, expiry, per-leg price, remove ✕
4. BID / MID / ASK / SPRD quad, selected peg marked with ✓
5. LIMIT (NET CREDIT) stepper (− value +) and TIF DAY/GTC
6. RISK · PER 1× COMBO + confidence: MAX GAIN, MAX LOSS, BREAKEVENS, P(PROFIT), MARGIN REQ,
   NET Θ/DAY, then TOTAL / FUNDS AFTER, then a P&L-at-expiry sparkline with breakeven markers
7. CORRELATION RISK BUDGET · GATE 3 block with held-ticker chips
8. `VERIFY ORDER →` CTA, then `STEP 1 OF 2 · TRANSMIT FOLLOWS REVIEW` + `⏎ VERIFY · B/M/A PEG`

Risk renders **above** the CTA on purpose, so unbounded risk is read before the button.

## What already exists and must be reused, not rebuilt

- `useOrderRisk` / `<OrderRiskGate>` — max loss/gain, margin, coverage. Chokepoint; every order
  surface must route through it (`web/CLAUDE.md`).
- `<OrderConfirmSummary>` — only accepts the branded `AugmentedOrderSummary` from `useOrderRisk`.
- `<OrderPriceStrip>` — BID/MID/ASK tap-to-fill pegging.
- `<OrderQuoteTelemetry>` — the nine-field quote block.
- `CorrelationRiskBanner` (`.crb-*`) — Gate 3 block.
- `OptionsChainTab`'s existing `OrderBuilder` (line ~246) already does legs, prefill, clear,
  bearish-RR routing warning, and net-quote math. The rail is a re-layout of this, not a new engine.

## Tokens

The design system's token names are already Radon's (`--bg-canvas`, `--bg-panel`, `--line-grid`,
`--text-{primary,secondary,muted}`, `--signal-{core,deep,strong}`, `--warn`, `--fault`). Only the
three translucent washes are missing. Add them as `color-mix`, NOT the design's raw rgba, so they
shift correctly between themes (repo rule):

    --wash-signal: color-mix(in srgb, var(--signal-core) 8%, transparent);
    --wash-warn:   color-mix(in srgb, var(--warn) 9%, transparent);
    --wash-fault:  color-mix(in srgb, var(--fault) 10%, transparent);

## Status — all stages shipped 2026-08-26

- **S1 — dock layout (desktop).** DONE `f6e20160`. Chain 908px, dock 384px, side by side.
- **S2 — risk block + payoff curve.** DONE `bb61845b`. Exact intrinsic payoff in `lib/order/payoff.ts`.
- **S3 — verify → transmit gate.** DONE `1649746c`. Also fixed a LocateFeeChip crash it surfaced.
- **S4 — chain ↔ ticket highlight.** DONE `65937d83`. Per side, not per row.
- **S5 — mobile bottom sheet.** DONE `c572f5cb`. Same risk panel and same gate, 44px ack row.

Each stage was red/green with a live browser check before landing.

## Deliberately not built

- **P(PROFIT)** renders `---`. It needs a volatility model the order pipeline does not produce.
  A plausible-looking probability beside a transmit button is worse than an honest blank, because
  an operator may size against it. The repo has a Python-parity Black-Scholes lib
  (`lib/blackScholes.ts`), so a proper POP is a well-scoped follow-up — it just should not be
  guessed at inside a layout change.
- **CONFIDENCE 0.84** from the canvas. Same reason: no producer for it.
- **1b Execution Dock** and **1c Staged Review**. These are alternatives to 1a, not further work.
