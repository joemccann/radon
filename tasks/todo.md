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
