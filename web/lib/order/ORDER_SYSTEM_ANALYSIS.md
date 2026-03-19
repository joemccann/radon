# Order System Analysis

## Current State — Order Entry Points

### 1. Order Placement (5 locations)

| Component | Location | Type | Features |
|-----------|----------|------|----------|
| `OrderTab > NewOrderForm` | `/{TICKER}?tab=order` | Stock/Single Option | BUY/SELL, Qty, Price, BID/MID/ASK, TIF, 2-step confirm |
| `OrderTab > ComboOrderForm` | `/{TICKER}?tab=order` | Multi-leg Spread | Leg pills, Spread price strip, BUY/SELL, Qty, Net Price, BID/MID/ASK, TIF |
| `OptionsChainTab > OrderBuilder` | `/{TICKER}?tab=chain` | Chain builder | Leg list, Action toggle, Qty input, Price, Clear, Confirm |
| `BookTab > StockOrderForm` | `/{TICKER}?tab=book` | Stock only | BUY/SELL, Qty, Price, BID/MID/ASK, TIF |
| `InstrumentDetailModal > LegOrderForm` | Position modal | Single Leg | BUY/SELL, Qty, Price, BID/MID/ASK, TIF |

### 2. Order Viewing (3 locations)

| Component | Location | Display |
|-----------|----------|---------|
| `OrderTab > ExistingOrderRow` | `/{TICKER}?tab=order` | Per-ticker open orders with MODIFY/CANCEL |
| `WorkspaceSections > OrdersSections` | `/orders` | All open orders table + executed orders table |
| `PositionTable` (inline) | `/portfolio` | Shows open orders count per position |

### 3. Order Modification (2 locations)

| Component | Location | Features |
|-----------|----------|----------|
| `ModifyOrderModal` | `/orders`, `/{TICKER}?tab=order` | New price, New qty, BID/MID/ASK, Outside RTH, Delta display |
| Combo replace flow | `ModifyOrderModal` | Leg editing, Replace as new order |

### 4. Order Cancellation (2 locations)

| Component | Location | Features |
|-----------|----------|----------|
| `CancelOrderDialog` | `/orders` | Confirmation dialog |
| Inline cancel button | `OrderTab`, `/orders` | Direct cancel with pending state |

---

## Feature Matrix — Current Gaps

| Feature | OrderTab (New) | OrderTab (Combo) | ChainBuilder | BookTab | InstrumentModal | ModifyModal |
|---------|----------------|------------------|--------------|---------|-----------------|-------------|
| **Price Display** |
| Spread price strip | ❌ | ✅ NEW | ✅ DONE | ❌ | ❌ | ✅ DONE |
| BID/MID/ASK buttons | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Price values in buttons | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ |
| Spread width display | ❌ | ✅ NEW | ❌ | ❌ | ❌ | ❌ |
| **Leg Display** |
| Leg pills (colored) | N/A | ✅ NEW | ✅ DONE | N/A | N/A | ✅ DONE |
| Leg list (vertical) | N/A | ❌ legacy | ✅ | N/A | N/A | ✅ |
| Direction indicators | N/A | ✅ +/− | ✅ action | N/A | N/A | ✅ |
| **Input Validation** |
| Zero/negative reject | ✅ API | ✅ API | ✅ API | ✅ API | ✅ API | ✅ API |
| Client-side validation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Confirmation** |
| 2-step confirm | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Order summary | ✅ DONE | ✅ DONE | ✅ | ❌ | ❌ | ✅ delta |
| Total cost display | ✅ DONE | ✅ DONE | ❌ | ❌ | ❌ | ❌ |
| **Layout** |
| Form above orders | ✅ NEW | ✅ NEW | N/A | N/A | N/A | N/A |

---

## Inconsistencies to Fix

### 1. Price Strip (spread BID/MID/ASK/SPREAD)
- ✅ ComboOrderForm has it
- ❌ ChainBuilder doesn't have it
- ❌ ModifyModal for combos doesn't have it

### 2. Leg Display
- ✅ ComboOrderForm uses pills (+/− colored)
- ❌ ChainBuilder uses vertical list with action buttons
- ❌ ModifyModal uses cards with dropdowns

### 3. Price in Buttons
- ✅ OrderTab shows "$6.50" in buttons
- ❌ ChainBuilder shows just "BID", "MID", "ASK"
- ❌ ModifyModal shows just "BID", "MID", "ASK"

### 4. Order Summary Before Confirm
- ❌ None of the forms show total cost, max gain, R:R
- ❌ Confirmation just repeats the order description

---

## Proposed Unified Component Architecture

```
web/lib/order/
├── ORDER_SYSTEM_ANALYSIS.md      # This file
├── types.ts                       # Shared order types
├── hooks/
│   ├── useOrderPrices.ts          # Compute BID/MID/ASK for any order
│   ├── useOrderValidation.ts      # Client-side validation
│   └── useOrderSubmit.ts          # Submit + loading + error state
├── components/
│   ├── OrderPriceStrip.tsx        # BID/MID/ASK/SPREAD strip (reusable)
│   ├── OrderLegPills.tsx          # Colored leg pills (reusable)
│   ├── OrderPriceButtons.tsx      # Quick-fill BID/MID/ASK buttons
│   ├── OrderQuantityInput.tsx     # Quantity input with validation
│   ├── OrderPriceInput.tsx        # Price input with $ prefix
│   ├── OrderTifSelector.tsx       # DAY/GTC toggle
│   ├── OrderActionToggle.tsx      # BUY/SELL toggle
│   ├── OrderConfirmSummary.tsx    # Order summary with total cost
│   └── OrderForm.tsx              # Composed form (stock, option, combo)
└── index.ts                       # Public exports
```

### Key Design Principles

1. **Composable primitives** — Each component handles one concern
2. **Consistent styling** — All use same CSS classes
3. **Shared hooks** — Price computation, validation, submission
4. **Context-aware** — Components adapt to stock/option/combo
5. **Progressive disclosure** — Show complexity only when needed
