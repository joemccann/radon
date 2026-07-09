# Task: Standalone watchlist page (2026-07-09)

Source: user request — make watchlist a standalone page, enhance the feature for its own Radon-branded page, and show ticker detail inline instead of redirecting to `/{TICKER}`.

## Dependency graph

- T1 depends_on: [] — Document plan and inspect current routing/watchlist/detail contracts.
- T2 depends_on: [T1] — Add `/watchlist` route and workspace section/nav metadata.
- T3 depends_on: [T1, T2] — Build standalone watchlist page that selects tickers inline and renders ticker detail content without route push.
- T4 depends_on: [T2, T3] — Apply Radon-compliant high-end visual pass and responsive behavior.
- T5 depends_on: [T4] — Add focused regression and Playwright coverage for inline selection.
- T6 depends_on: [T5] — Run focused and broad verification, document review here.

## Checklist

- [x] T1 Plan + route/component/test inspection.
- [x] T2 `/watchlist` page, section type, nav/description/quick prompt updates, reserved ticker guard.
- [x] T3 Standalone watchlist content with inline ticker selection and `TickerDetailContent` reuse.
- [x] T4 Radon-branded premium layout: dense instrument list, selected state, inline context pane, responsive single-column behavior.
- [x] T5 Regression tests: route metadata/guard and watchlist inline selection without navigation.
- [x] T6 Verification: focused Vitest, relevant Playwright, typecheck, full test suite.

## Constraints

- Do not revert existing dirty worktree changes.
- Radon brand rules override generic visual-skill conflicts: tokens, matte instrument modules, max 4px radius, no gradients/glassmorphism/soft shadows.
- Client fetches to `/api/watchlist` must keep `cache: "no-store"`.
- Ticker click on `/watchlist` must not push to `/{TICKER}`.

## Review

### Shipped

1. Added `/watchlist` as a reserved standalone workspace route with nav metadata, section prompts, mobile overflow entry, and ticker-route guard coverage.
2. Built `WatchlistContent` so watchlist rows select an inline ticker detail pane instead of routing to `/{TICKER}`.
3. Reused the existing ticker detail cockpit data contract for selected symbols: fundamentals, portfolio/orders, depth, tape, deck tabs, and active ticker context.
4. Applied a Radon-branded visual pass: dense instrument rail, selected-market state, matte detail cockpit, responsive mobile stacking, and mobile scroll-to-detail on selection.
5. Added focused Vitest and Playwright regressions proving `/watchlist` stays fixed while the selected ticker detail changes.

### Verification evidence

```
npx vitest run --config vitest.config.ts web/tests/chat.test.ts web/tests/data.test.ts web/tests/watchlist-content.test.tsx
  3 files / 47 tests passed

npm run typecheck
  clean

npx playwright test --config playwright.config.ts e2e/watchlist-page.spec.ts --project=chromium
  1 passed

npm test
  415 files / 4042 tests passed / 26 skipped
```

---

# Task: Orders page UX/UI improvements (critique execution)

Source: orders page UX critique (P0 safety → P1 hierarchy → P2 polish).

## Dependency graph (pass 1 — shipped)

- T1 Pure display helpers — done
- T2 Combo cancel confirmation dialog — done
- T3 Wire cancel confirm + partial-fill display — done
- T4 Command strip + Historical IA — done
- T5 Δ to fill + status mapping + intent badges — done
- T6 Mobile action sheet + tone by intent — done
- T7 Action button CSS hit targets — done
- T8 Verification — done

## Dependency graph (pass 2 — deferred polish)

- R1 Keyboard shortcuts on /orders — done
- R2 Bulk cancel by selection — done
- R3 Implied default-off + density — done
- R4 Historical page-size options — done
- R5 Verification + commit — done

## Checklist

### Pass 1
- [x] T1 lib helpers + tests (`web/lib/orders/orderDisplay.ts`, `web/tests/orders-display.test.ts`)
- [x] T2 cancel dialog multi-order (`CancelOrderDialog.tsx`, `cancel-order-dialog.test.tsx`)
- [x] T3/T5 open-orders table integration (`WorkspaceSections.tsx`)
- [x] T4 command strip + historical Status label + collapse when open>0
- [x] T6 mobile order list
- [x] T7 CSS action targets + status/intent/delta styles
- [x] T8 verification

### Pass 2
- [x] R1 `/` focuses open-orders filter; `M`/`X` on selected row; selected-row class + tabIndex
- [x] R2 checkbox column + Cancel selected (N) → multi CancelOrderDialog; clear after confirm
- [x] R3 `ORDER_COLUMN_DEFAULTS.implied = false`; compact/comfortable density toggle
- [x] R4 historical page sizes 15/30/50 + localStorage + Showing X-Y of N
- [x] R5 focused vitest + tsc + commit

## Constraints

- Brand: tokens only, no em dashes in new user-facing copy, 4px max radius
- Red/green TDD for logic and UI behavior
- Surgical: only orders-related surfaces

## Review (2026-07-09) — pass 1

### Shipped
1. **P0 safety:** Combo `CANCEL ALL` (desktop + mobile) opens multi-leg `CancelOrderDialog`; no direct cancel. Confirm then sequential `requestCancel`.
2. **Partial fills:** `formatFillQuantity` (`3/10`) + `Partial` status pill on open table/cards.
3. **Command strip:** Working / Partial / Fills today / Last sync + jump anchors to open/executed/historical/cash.
4. **Δ Fill** column (default on) with near/through/far urgency classes.
5. **Status mapping:** IB raw in `title`, operator labels Working/Queued/Partial/…
6. **OPEN/CLOSE** intent badges from portfolio; mobile card tone by intent (CLOSE = default).
7. **Historical:** column Side → Status; `defaultExpanded={openOrderRows.length === 0}`.
8. **Actions:** min-height 32px buttons; mobile bottom sheet shows order summary before actions.
9. Orphan bottom Last Sync section removed (lives on strip).

### Verification evidence (pass 1)
```
vitest: 9 files / 78 tests passed
tsc --noEmit: clean
playwright e2e/orders-ux-command-strip.spec.ts: 3/3 passed
```

## Review (2026-07-09) — pass 2 deferred items

### Shipped
1. **R1 Keyboard:** Pure helpers in `web/lib/orders/ordersUx.ts`. Desktop: `/` focuses `#orders-open-filter`; click/focus selects open-order row (`open-order-row--selected`); `M` opens modify when canModify; `X` opens cancel (single or combo). Ignored while typing / with modifiers / when dialog open.
2. **R2 Bulk cancel:** Checkbox column + select-all; header `Cancel selected (N)` opens multi-order `CancelOrderDialog` with flattened legs; selection cleared after confirm. Multi dialog shows "N symbols" when mixed.
3. **R3 Implied default-off:** `ORDER_COLUMN_DEFAULTS.implied = false` (implied_mv already false). Compact/comfortable density toggle on open-orders table-wrap (`table-wrap--compact`), persisted `radon:orders-open-density`.
4. **R4 Historical page size:** Selectable 15/30/50; localStorage `radon:orders-historical-page-size`; `Showing X-Y of N` on desktop and mobile lists.

### Verification evidence (pass 2)
```
vitest 8 files / 77 tests:
  orders-display, orders-ux, cancel-order-dialog, orders-command-strip,
  workspace-orders-implied, historical-trades-filter, mobile-order-list-display,
  orders-bulk-cancel
tsc --noEmit: clean
```

### Files
- `web/lib/orders/ordersUx.ts` (new)
- `web/components/WorkspaceSections.tsx`
- `web/components/TableSearch.tsx`
- `web/components/CancelOrderDialog.tsx`
- `web/app/globals.css`
- `web/tests/orders-ux.test.ts` (new)
- `web/tests/orders-bulk-cancel.test.tsx` (new)
- `web/tests/workspace-orders-implied.test.tsx`
- `web/tests/historical-trades-filter.test.tsx`

---

# Task: Options OrderBuilder layout pass (2026-07-09)

## Shipped
- Fixed leg grid (56px BUY/SELL chip)
- Removed redundant OrderLegPills
- Tappable OrderPriceStrip for combos; no duplicate chips
- Compact skew (4 metrics)
- Prefill chip, compact TIF, risk teaser, no em-dash limit label
- CSS `.order-builder-*`

## Verify
vitest: order-builder-layout, combo-skew-panel, chain-url-deeplink, order-unified-components green
tsc clean
