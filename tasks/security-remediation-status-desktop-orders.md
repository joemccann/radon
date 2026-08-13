# Desktop order remediation status

Scope: T4-C2 desktop order cluster plus coverage-audit reconciliation. Reconciliation: **19 assigned, 16 fixed, 3 duplicates, 0 deferred, 0 actionable**.

| ID | Disposition | Implementation evidence | Regression |
|---|---|---|---|
| HB-065 | fixed | Instrument modal and shared single-leg ticket preserve STP/STP LMT type, stop price, risk price, payload, and success copy. | `web/tests/stop-order-ticket.test.tsx`; `web/e2e/stop-order-desktop.spec.ts` |
| HB-066 | fixed | Instrument modal preserves `undefined` portfolio coverage; ticket derives and requires the canonical risk permit. | `web/tests/order-risk-linear.test.tsx::instrument modal blocks pending and null coverage` |
| HB-067 | fixed | Modify risk legs apply the SELL envelope reversal while replacement payload legs remain structural. | `web/tests/modify-order-negative-risk-reversal.test.tsx::sell combo review models submitted direction` |
| HB-068 | fixed | Edited legs and integer ratios drive natural quote, implied reference, risk quantities, and replacement payload; structure edits invalidate the manual price. | `web/tests/open-order-combo-modify.test.ts::edited ratio legs drive reference and payload price` |
| HB-069 | fixed | BAG, option, and stock modifications build canonical risk inputs and require resolved `okToSubmit` in both button and handler. | `web/tests/modify-order-close-pnl.test.tsx::modify is disabled until canonical risk permits` |
| HB-070 | fixed | Position table stores stable position/contract identity, re-resolves after refresh, and closes when identity disappears. | `web/tests/position-table-leg-row-runtime.test.ts::portfolio refresh invalidates stale trade modal identity` |
| HB-072 | fixed | `SingleLegOrderTicket` accepts canonical input/portfolio rather than an arbitrary risk node; live submission requires the derived permit. | `web/tests/order-risk-linear.test.tsx::single leg ticket cannot submit when gate blocks` |
| HB-073 | fixed | Deep-linked position IDs are accepted only when the canonical position ticker matches the route ticker. | `web/tests/ticker-chain-position-focus.test.tsx::cross ticker posid cannot reach order builder` |
| HB-074 | duplicate of HB-073 (fixed) | `resolveTickerPosition` rejects a `posId` unless its canonical portfolio row matches the route ticker; the rejected row never reaches the order surfaces. | `web/tests/ticker-chain-position-focus.test.tsx::cross ticker posid cannot reach order builder` |
| HB-084 | fixed | Chain builder preserves pending coverage and gates both review/submission on resolved portfolio risk. | `web/tests/options-chain-implied.test.tsx::chain submit requires resolved portfolio risk` |
| HB-085 | fixed | Combo entry preserves signed natural quotes and blocks placement until the debit/credit sign resolves. | `web/tests/options-chain-implied.test.tsx::unresolved combo sign blocks submission` |
| HB-086 | fixed | OrderTab single and combo confirmation handlers/buttons require the canonical risk permit. | `web/tests/order-tab-risk-gate.test.tsx` |
| HB-087 | fixed | A held short option defaults BUY and display, risk leg, and payload use the same action. | `web/tests/order-tab-close-position-sign.test.ts::short option default is buy to close everywhere` |
| HB-088 | fixed | OrderTab emits the canonical `type: "linear"` stock risk input with share quantity, side, price, multiplier, and held coverage instead of an empty option-leg input. | `web/tests/order-risk-linear.test.tsx::stock order uses stock risk variant not empty option legs` |
| HB-089 | fixed | Held combo GCD defines BAG units and exact per-leg ratios for risk and placement. | `web/tests/order-tab-close-position-sign.test.ts::ratio combo close preserves held leg ratios`; `web/tests/position-trade.test.ts` |
| HB-090 | duplicate of HB-109 (fixed) | Shared `isPureComboClose` compares requested quantity to held BAG units; OrderTab uses it, scales partial-close basis, and routes oversized SELL legs through normal risk without `closeOut`. | `web/tests/order-tab-close-position-sign.test.ts::oversized combo sell is not closeout`; `web/tests/position-trade.test.ts::does not classify an oversized combo SELL as a riskless close` |
| HB-093 | duplicate of HB-109 (fixed) | PositionTradeTicket derives an over-close guard from held BAG units, disables review, shows a bounded error, and rechecks validity in the handler before risk or placement. | `web/tests/position-trade.test.ts::overclose is blocked before risk and placement` |
| HB-094 | fixed | Position trade stop orders use `riskPriceForOrderType` for the builder, confirmation, and notification; NaN limit is never handed to risk. | `web/tests/position-trade.test.ts`; `web/tests/stop-order-ticket.test.tsx` |
| BUG-076 | fixed | Modify quantity and leg ratios require digit-only safe positive integers; fractional/exponent forms are rejected rather than truncated. | `web/tests/modify-order-close-pnl.test.tsx::rejects fractional and exponent quantities instead of truncating them` |

## Verification

- `npx tsc --noEmit --pretty false`: passed.
- Focused Vitest: **11 files, 67 tests passed**.
- `git diff --check` on C2 components/tests: passed.
- Playwright browser run could not start in this worktree: Turbopack rejects the out-of-root `web/node_modules` symlink; webpack fallback then detects the root/worktree duplicate Playwright installations. The same behaviors are covered by the focused component regressions above; this is an environment runner blocker, not an actionable source finding.
