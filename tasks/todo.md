# Task: Orders page UX/UI improvements (critique execution)

Source: orders page UX critique (P0 safety → P1 hierarchy → P2 polish).

## Dependency graph

- T1 Pure display helpers — done
- T2 Combo cancel confirmation dialog — done
- T3 Wire cancel confirm + partial-fill display — done
- T4 Command strip + Historical IA — done
- T5 Δ to fill + status mapping + intent badges — done
- T6 Mobile action sheet + tone by intent — done
- T7 Action button CSS hit targets — done
- T8 Verification — done

## Checklist

- [x] T1 lib helpers + tests (`web/lib/orders/orderDisplay.ts`, `web/tests/orders-display.test.ts`)
- [x] T2 cancel dialog multi-order (`CancelOrderDialog.tsx`, `cancel-order-dialog.test.tsx`)
- [x] T3/T5 open-orders table integration (`WorkspaceSections.tsx`)
- [x] T4 command strip + historical Status label + collapse when open>0
- [x] T6 mobile order list
- [x] T7 CSS action targets + status/intent/delta styles
- [x] T8 verification

## Constraints

- Brand: tokens only, no em dashes in new user-facing copy, 4px max radius
- Red/green TDD for logic and UI behavior
- Surgical: only orders-related surfaces

## Review (2026-07-09)

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

### Verification evidence
```
vitest: 9 files / 78 tests passed
  orders-display, cancel-order-dialog, mobile-order-list-display,
  orders-command-strip, orders-empty-state, historical-trades-filter,
  workspace-orders-implied, open-order-combos, mobile-sort-parity

tsc --noEmit: clean

playwright e2e/orders-ux-command-strip.spec.ts: 3/3 passed
  - command strip
  - partial 4/10 + Partial
  - combo CANCEL ALL confirms before cancel POST
```

### Files
- `web/lib/orders/orderDisplay.ts` (new)
- `web/components/CancelOrderDialog.tsx`
- `web/components/WorkspaceSections.tsx`
- `web/components/mobile/MobileOrderList.tsx`
- `web/components/CashFlowsSection.tsx` (id=orders-cash)
- `web/components/ticker-detail/OrderTab.tsx` (modify tooltip only)
- `web/app/globals.css`
- tests + `web/e2e/orders-ux-command-strip.spec.ts`

### Not in this pass (defer)
- Keyboard shortcuts (M/X//)
- Bulk cancel by symbol
- Implied default-off density toggle beyond existing column toggle
- Historical page-size options
