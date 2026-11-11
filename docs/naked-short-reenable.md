# Gate 4 re-enable runbook (no naked shorts)

Status: **DISABLED since 2026-04-30** (commit `27c320dba`, "disable naked-short
protection per operator request"). This runbook is the only sanctioned path back.
Every statement below is taken from the code on `main`. Items marked
**Verify before enabling** are decisions or facts the code does not settle.

## 1. What Gate 4 enforced

The specification, as it stood in `CLAUDE.md` before the disable
(`git show 27c320dba:CLAUDE.md`, section "Naked Short Protection (Gate 4)"):

| Scenario | Action |
|----------|--------|
| SELL stock, no long shares | BLOCK |
| SELL N shares, fewer than N held | BLOCK |
| SELL call, no covering long calls or shares | BLOCK |
| SELL call, long calls at the same expiry | ALLOW (vertical) |
| SELL N calls, shares < N x 100 | BLOCK |
| SELL put (cash-secured) | ALLOW |
| Vertical spread (BUY C + SELL C) | ALLOW |
| Short risk reversal (SELL C + BUY P) | BLOCK (put does not cover call) |
| 1x2 ratio (BUY 1C + SELL 2C) | BLOCK unless stock covers |
| Jade Lizard (BUY C + SELL C + SELL P) | ALLOW |
| Combo closing (envelope action=SELL) | ALLOW |
| BUY anything | ALLOW |

Combo rule: IB BAG orders carry `action=BUY` on the envelope, so the guard reads
leg `right`/`action`. Uncovered = `sellCallRatio - buyCallRatio`; any remainder
must be covered by long shares (100 per contract) after existing short calls.

Short puts and futures are not checked by the preserved logic. The old
one-line rule also named futures and bonds, but no `_*Impl` code handles them.

### The three original enforcement layers

| Layer | Where | State today |
|-------|-------|-------------|
| 1. UI pre-flight | `web/lib/nakedShortGuard.ts:checkNakedShortRisk`, called by `web/components/ticker-detail/OrderTab.tsx` (`NewOrderForm`, `ComboOrderForm`) and `web/components/ticker-detail/PositionTradeTicket.tsx` | Short-circuited. Stock SELL returns `allowed: true` plus a warning reason (SPX-05); option and combo always allow. All three callers read the reason only when `allowed` is false, so the SPX-05 warning never renders today. |
| 2. API 403 | `web/app/api/orders/place/route.ts` | **Removed, not preserved.** Deleted in `381518418` (2026-06-23, Turso migration) because it read `data/portfolio.json`. |
| 3. Post-sync audit and auto-cancel | `scripts/naked_short_audit.py:find_naked_short_violations`, called from `scripts/ib_sync.py` after the portfolio save (unless `--skip-audit`) | Short-circuited to `[]`. |

## 2. Code points to change (file:symbol)

### 2a. `web/lib/nakedShortGuard.ts`

- `checkNakedShortRisk`: today it routes only `type === "stock" && action === "SELL"`
  through `warnFromImpl(_checkNakedShortRiskImpl(...))` and returns
  `{ allowed: true }` for everything else. Re-enable: return
  `_checkNakedShortRiskImpl(order, portfolio)` for every order.
- `warnFromImpl`: becomes unused once the line above changes. Delete it.
- `auditOpenOrders`: today returns `[]`. Re-enable: return
  `_auditOpenOrdersImpl(orders, portfolio)` (rename the `_orders` / `_portfolio`
  parameters).
- Keep `_checkNakedShortRiskImpl` and `_auditOpenOrdersImpl` exported. They are
  the tripwire seam the tests call (TEST_AUDIT T-052). Folding them back into the
  public functions is optional; if you do, repoint the tests in section 3.
- Update the file-top banner. It points at a `CLAUDE.md` "How to re-enable"
  section that no longer exists; point it at this file or drop the DISABLED text.

`auditOpenOrders` has no production caller today (only tests import it).
**Verify before enabling** whether any surface should call it.

### 2b. `scripts/naked_short_audit.py`

- `find_naked_short_violations`: today `return []`. Re-enable: return
  `_find_naked_short_violations_impl(orders, positions)`, or move the body back.
- Update the module docstring banner (it also points at the missing `CLAUDE.md`
  section).

Consequence to accept first: `scripts/ib_sync.py` calls
`cancel_violations(client, violations)` whenever the audit finds a violation
and the IB client is still connected. Re-enabling the audit **re-arms automatic
order cancellation** on every portfolio sync. Production sync runs through
FastAPI `/portfolio/sync` (`radon-portfolio-sync.service`) without
`--skip-audit`. `scripts/free_trade_analyzer.py` passes `--skip-audit`.
**Verify before enabling**: that auto-cancel is wanted, and that the Python
impl matches the TS rules. It does not today. In
`_find_naked_short_violations_impl`, a SELL call is covered only by long stock
(no long-call vertical coverage, no sell-to-close matching), so a single-leg
SELL call against a held long call would be flagged and cancelled by Python
while the TS guard allows it. In the other direction, Python skips every BAG
order, so a naked combo the TS guard blocks passes the audit.

### 2c. API layer (must be rebuilt)

There is no server-side Gate 4 check anywhere today: not in
`web/app/api/orders/place/route.ts`, not in FastAPI `scripts/api/server.py`,
not in `scripts/order_limits.py` (fat-finger caps only) and not in
`scripts/kelly_guard.py` (its docstring says it "does not re-enable Gate 4").
Re-enabling only the UI leaves every surface that does not call
`checkNakedShortRisk` unguarded, including the chain ticket
(`OptionsChainTab.tsx`), `MobileOrderTicket.tsx`, `SingleLegOrderTicket.tsx`,
`ListedContractOrderForm.tsx`, `FuturesOrderForm.tsx`,
`IndexOptionOrderForm.tsx`, `BookTab.tsx` and the assistant order path
(`web/lib/assistant`).

To restore layer 2, call `checkNakedShortRisk(body, portfolio)` in the place
route before forwarding to FastAPI and return 403 `VALIDATION_ERROR`
`"Naked short blocked: <reason>"` when `allowed` is false (the shape removed in
`381518418`). The portfolio must come from the Turso `portfolio_snapshots`
read the route layer already uses (`web/CLAUDE.md`), never `data/*.json`.
**Verify before enabling**: the exact Turso reader to use, whether a missing
snapshot fails closed (403) or open (the old code logged and allowed), and
whether `/api/orders/modify` needs the same check.

### 2d. Order-risk gate (owner decision)

`web/lib/order/risk/useOrderRisk.ts` `okToSubmit` gates on coverage resolution
only (`web/CLAUDE.md`, change dated 2026-07-21): an UNBOUNDED verdict stays
submittable with the Gate 1 warning. This is separate from the guard above.
**Verify before enabling** whether Gate 4 should also make an unbounded verdict
block `okToSubmit`. If yes, the tests in section 3b flip too.

### 2e. Instruction files

Change the Gate 4 row or line in all of these to the enabled wording
(original: "Never naked short stock, calls, futures, or bonds. Every short call
fully covered. Violation = immediate cancel."):

- `CLAUDE.md` (Four Gates table)
- `.pi/AGENTS.md` (Four gates list)
- `docs/evaluation.md` (gates table)
- `docs/mobile/iphone-app-direction.md` (gate list)
- `docs/prompt.md` (Hard Constraints 5 and 6)
- `web/CLAUDE.md` (Pending UX rule, if 2d changes)
- `docs/quant-strategies.md`, `docs/risk/kelly-fortunes-formula.md`
  (both say Gate 4 is disabled)
- `site/lib/pages/defined-risk-options-structures.ts` FAQ "Why is Gate 4 marked
  disabled?" (public site; `site/CLAUDE.md` deploy rules apply)

## 3. Tests that pin the disabled state today

### 3a. Must flip

| Test | Pins | After re-enable |
|------|------|-----------------|
| `web/tests/naked-short-guard.test.ts` "SPX-05-1", "SPX-05-2" | stock SELL returns `allowed: true` with a reason | `allowed: false` |
| `web/tests/naked-short-guard.test.ts` "SPX-05-4" | SELL call on an empty portfolio returns `allowed: true`, no reason | `allowed: false`, reason contains "Naked short call" |
| `scripts/tests/test_naked_short_audit.py` `TestDryRun.test_dry_run_reads_db_sources_and_does_not_cancel` | `violations_found == 0` for a SELL 100 AAPL stock order with no position | `violations_found == 1`, `cancelled == 0`, `IBClient` still not called |

### 3b. Flip only if 2d changes

- `web/tests/order-risk-chokepoint.test.tsx` "keeps okToSubmit true on a resolved UNBOUNDED verdict (Gate 4 disabled; Gate 1 advisory)"
- `web/tests/mobile-order-ticket.test.tsx` "allows submitting an unbounded naked short once acknowledged, and still renders the Gate 1 warning"
- `web/tests/covered-call-order-summary.test.tsx` and `web/tests/order-risk-linear.test.tsx` (comments "Unbounded but coverage-resolved -> submittable (Gate 4 disabled)")

### 3c. Already armed (must stay green, no change)

- `web/tests/naked-short-guard.test.ts` `_checkNakedShortRiskImpl` (22 cases) and `_auditOpenOrdersImpl` (4 cases) tripwire suites.
- `scripts/tests/test_naked_short_audit.py` `TestFindNakedShortViolations` (13 cases, routed to the impl by an autouse fixture) and `TestCancelViolations` (2).
- `web/e2e/wulf-close-order-naked-short.spec.ts` (curated CI e2e): a closing SELL of held calls must show no naked-short warning. This is the false-positive regression and must still pass.

## 4. Tests to add

1. Public-boundary cases in `web/tests/naked-short-guard.test.ts`: call
   `checkNakedShortRisk` and `auditOpenOrders` directly (not the impls) with one
   blocking case each (naked SELL call, short risk reversal combo, 1x2 ratio)
   and one allowing case each (vertical, SELL put, closing combo).
2. Python public boundary: `find_naked_short_violations` on a violating order
   returns one violation without the autouse fixture.
3. Wire test for layer 2 (root `CLAUDE.md` "gated action is tested at the wire"):
   stub `fetch`, POST a naked SELL call to the place route handler, assert 403
   and that nothing was forwarded to FastAPI; paired case where a vertical is
   forwarded with the exact `/orders/place` request.
4. UI gate tests: render `OrderTab` and `PositionTradeTicket`, stage a naked
   SELL call, assert the warning renders, the transmit button is disabled, and
   no `/api/orders/place` request fires; then a covered case that fires exactly
   one request.
5. `ib_sync` audit: with the audit enabled and a connected mock client, a
   violating open order calls `cancel_violations`; with `--skip-audit` it does
   not.

## 5. Verify

```bash
cd web && npx vitest run tests/naked-short-guard.test.ts tests/order-risk-chokepoint.test.tsx
cd web && command npx tsc --noEmit -p .
.venv/bin/python -m pytest scripts/tests/test_naked_short_audit.py -q
.venv/bin/python scripts/naked_short_audit.py --dry-run   # read-only, never cancels
```

Then the full web and Python suites. Browser check (chrome-cdp, or Playwright
with mocked routes): on a ticker with no position, stage SELL 1 call in the
Order tab and confirm the naked-short banner renders and transmit stays
disabled; stage a vertical and confirm it is submittable; run
`web/e2e/wulf-close-order-naked-short.spec.ts`. Never place a live order to
test the guard.

## 6. Rollback

Revert the re-enable commit. The disable is a set of early returns, so
reverting restores today's behaviour exactly. If the audit cancelled orders in
error, the cancellations are not undone by a revert; re-place them by hand.
To stop auto-cancel immediately without a deploy, the only switch the code has
is `ib_sync.py --skip-audit`, which production sync does not pass; a code
revert is the real kill path.
