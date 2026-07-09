# Mobile Order Builder UX Proposal

Analysis of the mobile chain → ticket flow (`MobileChainLadder` + `MobileOrderTicket`) against desktop `OrderBuilder` and industry mobile trading patterns (Robinhood Options, tastytrade, Thinkorswim mobile, IBKR mobile, Webull).

Scope: `/{TICKER}?tab=chain` at ≤640px. Not a redesign of desktop.

---

## Current flow (as shipped)

```
Chain ladder cell tap
  → Contract detail BottomSheet (Greeks + Last/BidAsk)
    → BUY or SELL
      → Pending strip ("N LEGS · Mid $x · Build order →")
        → Order ticket BottomSheet
          → Legs / qty / limit / TIF / risk / submit
```

Desktop is one-tap: bid/ask cell click adds a leg and the sticky `OrderBuilder` updates in place.

---

## What already works

- Bottom sheet + sticky footer thumb zone; 44–56px targets; 18px limit input (no iOS zoom).
- Credit/debit colored submit (`m-submit--credit` / `--debit`).
- Live Bid/Mid/Ask strip + ComboSkewPanel.
- Risk routed through `<OrderRiskGate surface="mobile-ticket">`.
- Combo payload preserves per-leg actions; envelope stays BUY.
- ATM auto-center; CALLS/PUTS/ALL + strikes window deep-linked.

---

## Pain points (severity-ranked)

### P0 — Safety / correctness gaps

| ID | Issue | Why it matters | Evidence |
|---|---|---|---|
| S1 | **No confirm step** | Desktop requires Place → Confirm. Mobile submits on first tap. Fat-finger risk on a live broker. | `MobileOrderTicket.handleSubmit` vs desktop `confirmStep` |
| S2 | **Submit ignores `okToSubmit`** | Gate can show pending / unbounded / Gate-1 fail while submit stays enabled (only checks price + legs). CLAUDE.md: parent MUST disable when `okToSubmit !== true`. | Submit `disabled={!isValidPrice \|\| submitting \|\| legs.length === 0}`; no `onState` |
| S3 | **Always labels "BUY/SELL TO OPEN"** | Closing a held long/short still reads as opening. Misleading direction is a classic mobile trading failure mode. | Hardcoded `directionLabel`; no `closeOut` / position detection |
| S4 | **Raw IB errors** | Desktop runs `formatOrderError`. Mobile dumps `json.error`. `<br>` and long margin text are unreadable on 393px. | `setError(json.error \|\| ...)` |

### P1 — Friction vs industry norms

| ID | Issue | Industry pattern | Current |
|---|---|---|---|
| F1 | **3 taps to add a leg** | Robinhood / tasty / TOS: tap bid or ask (or long-press) adds side immediately | Cell → sheet → BUY/SELL |
| F2 | **Pending strip is opaque** | Ticket preview shows structure name, debit/credit, notional | Count + mid only; no structure, no Clear |
| F3 | **No BID / MID / ASK quick-set** | Every major options app: one-tap fill from quote | ±$0.05 steppers only |
| F4 | **Cannot flip leg side in ticket** | Desktop toggles BUY↔SELL on the leg | Must remove + re-add via chain |
| F5 | **No Clear / discard** | Always offer abandon without removing legs one-by-one | Only per-leg X |
| F6 | **Risk always expanded in footer** | Confirm-step or collapsible "Review risk" keeps CTA visible | Risk + submit compete in ~82vh sheet; E2E already needs `tapJs` because footer can sit off-viewport |
| F7 | **No notional / total cost** | "You'll pay/receive $X" before submit | Desktop shows `$… notional`; mobile does not |
| F8 | **Missing desktop safety copy** | Coverage chip + bearish-RR routing warning | Absent on mobile |

### P2 — Polish

| ID | Issue |
|---|---|
| P1 | Leg row duplicates expiry/right (`BuySellRow` label + sub) |
| P2 | Fixed $0.05 tick; underlyings with $0.01 / $0.10 ticks feel wrong |
| P3 | No haptic / press feedback on add-leg |
| P4 | Success auto-closes in 800ms; easy to miss order id |
| P5 | No "legs selected" highlight on ladder rows |
| P6 | Qty only ±1; no quick 5/10 or "Max" for closes |

---

## Design principles (apply to Radon mobile ticket)

1. **Progressive disclosure** — Build on chain; review risk only when committing.
2. **Thumb-first** — Primary CTA and price controls in lower third; never bury submit under a tall risk block.
3. **One decision per screen** — Add legs vs size/price vs confirm.
4. **Quote → price in one gesture** — Bid/Mid/Ask are actions, not decoration.
5. **Direction honesty** — Open vs close, debit vs credit, must match portfolio.
6. **Parity of safety, not of chrome** — Mobile can look different; it cannot skip confirm, risk gating, or error hygiene.
7. **Stay in brand** — Tokens, ≤4px radius, mono numerics, no glass/pills-as-decoration.

---

## Proposed UX (target flow)

### A. Faster leg building (chain)

**Default (recommended): long-press / swipe affordance + keep detail sheet**

1. **Tap** cell → detail sheet (Greeks) with BUY | SELL (unchanged for discovery).
2. **Long-press** cell → haptic + add as BUY (call left / put right convention) **or** show a 2-button popover anchored to the cell: BUY / SELL, no full sheet.
3. **Optional power mode** (settings or "Quick add"): tap bid half → SELL, ask half → BUY (mirrors desktop bid/ask click semantics). Split the cell visually with a hairline so the affordance is learnable.

Also:

- Highlight selected strikes on the ladder (BUY tint / SELL tint border).
- Pending strip becomes a **mini ticket preview**:
  - Structure name (`detectStructure`)
  - `N legs · Net debit/credit Mid $x · ~$notional`
  - Secondary **Clear** (destructive slot) + primary **Review**

### B. Ticket as review + size (sheet)

Reorder body:

1. **Header**: `AAPL · Bull Call Spread` + Clear
2. **Legs** (editable): side toggle, qty stepper, remove; collapse duplicate subcopy
3. **Price**: Bid | Mid | Ask as **tappable chips** that set limit; ± tick beside input; show **notional** under input
4. **TIF**: DAY / GTC
5. **Warnings** (compact): coverage chip, bearish-RR note when applicable
6. **Footer**: primary button = `Review order` (not place yet)

Risk summary moves **out of the always-on footer** into the confirm step (or a single-line "Max loss $X · Max gain $Y" teaser that expands on Review).

### C. Confirm step (mandatory)

Second state in the same sheet (or a stacked sheet):

- Full `<OrderRiskGate>` summary
- Direction label from portfolio: `BUY TO OPEN` / `SELL TO CLOSE` / etc.
- Net debit/credit + notional + TIF
- **Back** | **Confirm & send**
- Confirm disabled unless `riskState.okToSubmit === true`
- Errors via `formatOrderError` + `.order-error-detail`

### D. Post-submit

- Keep success visible until user dismisses **or** 2.5s with order id / "View in Orders"
- Do not clear legs until success is acknowledged if placement failed partially

---

## Mapping to components

| Change | Primary files |
|---|---|
| Confirm + `okToSubmit` + close labeling | `MobileOrderTicket.tsx` |
| Bid/Mid/Ask chips, notional, Clear, side flip | `MobileOrderTicket.tsx` + `globals.css` |
| Error formatting | reuse `formatOrderError` / `OrderErrorBanner` |
| Pending strip preview + Clear | `MobileChainLadder.tsx` |
| Quick-add / long-press | `MobileChainLadder.tsx` `SideCell` |
| Coverage + RR warning | port copy from desktop `OrderBuilder` |
| Close-out risk input | mirror `OrderTab` / `positionTrade` close detection when held qty covers |
| Tests | extend `e2e/mobile-order-ticket.spec.ts`; unit for submit gating |

---

## Phased delivery

### Phase 1 — Safety parity (ship first)

- Confirm step
- Wire `OrderRiskGate onState` → disable submit when `!okToSubmit`
- `formatOrderError` on failures
- Honest open/close labels + `closeOut` when applicable
- Notional line + Clear all

**Success:** cannot place unbounded / pending-coverage order; closing a long never says TO OPEN; IB errors readable.

### Phase 2 — Speed parity

- Bid/Mid/Ask quick-set chips
- Leg side toggle in ticket
- Pending strip shows structure + debit/credit + Clear
- Ladder selection chrome for active legs

**Success:** quote → limit in one tap; strip answers "what am I building?" without opening the sheet.

### Phase 3 — Build-speed

- Long-press or split-cell quick add
- Optional Quick-add mode
- Adaptive tick size
- Qty presets / max close

**Success:** experienced operator adds a 2-leg vertical in ≤3 gestures end-to-end (vs ~6 today).

---

## Explicit non-goals

- Do not port the full desktop sticky builder into the ladder (vertical space is the constraint).
- Do not remove the detail sheet for first-time / discovery taps.
- Do not weaken Gate-1 / risk chokepoint for "simpler" mobile.
- Do not add structure wizards (Iron Condor templates, etc.) until the core ticket is safe and fast.

---

## Open decisions (need operator call before Phase 3)

1. **Quick-add default:** long-press popover vs split bid/ask cell vs settings toggle?
2. **Confirm UI:** in-sheet step (recommended, matches desktop) vs second full-height sheet?
3. **Risk teaser on Review screen:** one-line max loss/gain always visible, or only after Review?

Recommendation if unblocked: (1) long-press popover, (2) in-sheet confirm, (3) one-line teaser + full gate on confirm.

---

## Acceptance checks (when implementing)

- Vitest: submit disabled when `okToSubmit` false; close label when held long; error formatter used.
- Playwright mobile 393×852: confirm step required; Bid chip sets limit; pending strip shows structure; no click on live place in verification (far limit / cancel if unavoidable).
- Visual: brand tokens only; footer CTA remains in thumb zone with risk on confirm.
