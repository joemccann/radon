export type OrderAction = "BUY" | "SELL";
export type OrderTif = "DAY" | "GTC";
export type OrderType = "stock" | "option" | "combo";

export interface OrderPrices {
  bid: number | null;
  mid: number | null;
  ask: number | null;
  spread: number | null;      // ask - bid
  spreadPct: number | null;   // spread / mid * 100
  available: boolean;         // true if all prices resolved
}

export interface OrderLeg {
  id: string;
  action: OrderAction;
  direction: "LONG" | "SHORT";
  strike: number;
  type: "Call" | "Put";
  expiry: string;
  quantity: number;
  bid?: number | null;
  ask?: number | null;
}

export interface OrderFormState {
  action: OrderAction;
  quantity: string;
  limitPrice: string;
  tif: OrderTif;
  confirmStep: boolean;
  loading: boolean;
  error: string | null;
  success: string | null;
}

export interface OrderValidation {
  isValid: boolean;
  errors: {
    quantity?: string;
    price?: string;
    general?: string;
  };
  parsedQuantity: number;
  parsedPrice: number;
}

/**
 * Order summary for confirmation — presentational shape.
 *
 * History: this used to be exported as `OrderSummary` and every order surface
 * built its own literal of this shape and handed it straight to
 * `<OrderConfirmSummary>`. That coupling was the root cause of three
 * production bugs (AAOI 2026-05-19, WULF 2026-05-26 morning, RR 2026-05-26
 * afternoon) — each surface re-discovered the portfolio/order seam by hand,
 * each time missed a case.
 *
 * The new contract: `OrderPresentationSummary` describes the FIELDS rendered.
 * `AugmentedOrderSummary` (below) is the BRANDED variant that `<OrderConfirmSummary>`
 * actually accepts. The brand is a `unique symbol` that only the
 * `useOrderRisk` hook (in `lib/order/risk/`) can attach. Plain literals do
 * not compile against `<OrderConfirmSummary>`'s signature.
 *
 * If you find yourself building one of these by hand outside the risk module,
 * stop and use `<OrderRiskGate>` instead.
 */
export interface OrderPresentationSummary {
  description: string;        // "BUY 44x GOOG Bull Call Spread @ $6.50"
  totalCost: number | null;   // quantity * price * 100 for options
  totalLabel?: string;        // override for close/debit/credit semantics
  maxGain?: number | null;    // For spreads
  maxLoss?: number | null;    // For spreads (positive magnitude when bounded)
  /** When true, the order has no theoretical max loss bound. UI should
   *  render "UNBOUNDED" instead of a number. */
  maxLossUnbounded?: boolean;
  /** When true, the order has no theoretical max gain bound (long call). */
  maxGainUnbounded?: boolean;
  /** Gate 1 warning surface: human-readable reason this order is
   *  not pure defined-risk. UI renders an "Undefined risk" badge. */
  undefinedRiskReason?: string | null;
  breakeven?: number | null;  // For options/spreads
  estimatedPnl?: number | null;
  /** Estimated realized P&L divided by the absolute signed entry basis. */
  estimatedPnlPct?: number | null;
  estimatedPnlLabel?: string;
  /**
   * Set when the order closes held inventory (declared by the surface or
   * inferred by the chokepoint from the exact held contract). Surfaces use
   * it to label the ticket "to close" instead of by opening structure.
   */
  closeOutAction?: "BUY" | "SELL" | null;
  /**
   * Phase-1 margin-impact estimate. Optional/nullable so existing producers
   * may omit it; it rides inside the existing brand (no brand change). Only
   * populated by `useOrderRisk` when coverage is resolved AND a baseline
   * (available funds / buying power) is present — never $0 for a missing
   * baseline.
   * For naked / undefined-risk shorts `requirement` is the Reg-T ESTIMATE,
   * NOT `maxLoss` (which is the assignment-at-zero stress bound and massively
   * overstates real margin).
   */
  marginImpact?: {
    requirement: number | null;
    availableBefore: number | null;
    availableAfter: number | null;
    baselineLabel: string;
    source: "exact-maxloss" | "regt-estimate" | "ib-whatif";
    approximate: boolean;
  } | null;
  /**
   * Portfolio-coverage annotation, populated ONLY by `useOrderRisk` when
   * held positions cover the order's short legs (covered call, SELL against
   * held longs). Rendered as a coverage chip inside the confirm panel so
   * the operator sees the covered structure at the point of confirmation,
   * not only in the builder header. Null/absent when no coverage applies.
   */
  coverageNote?: string | null;
  /**
   * Signed expiry P&L (dollars, + gain / - loss, never clamped at 0) when
   * held OPTION legs cover this order. `order*` treats the held leg as
   * already paid for; `spread*` prices it at its cost basis, i.e. the whole
   * resulting spread. Null/absent when no held option covers the order or
   * the order is unbounded. `spread*` are null when the held basis is
   * unknown; best values are null when `bestUnbounded`.
   */
  withHeldLegs?: {
    orderBest: number | null;
    orderWorst: number;
    spreadBest: number | null;
    spreadWorst: number | null;
    bestUnbounded: boolean;
    heldBasisDollars: number | null;
  } | null;
}

/**
 * Branded augmented summary — the ONLY shape `<OrderConfirmSummary>` accepts.
 *
 * The `__augmented` brand is a `unique symbol` declared in this module.
 * It is meant to be attached only by `useOrderRisk`. A plain literal `{ description:
 * "...", ... }` will FAIL TYPECHECK against `<OrderConfirmSummary>` because
 * it lacks the symbol-keyed property — and the symbol is imported only by
 * producer code inside `lib/order/risk/`. Bypass requires defeating the
 * type system explicitly (`as AugmentedOrderSummary`), the ESLint rule (which
 * bans `as` casts on this type), AND a dev-mode runtime assertion (below).
 *
 * `coverageStatus`:
 *   - `resolved`     — portfolio loaded; coverage augmentation applied
 *   - `pending`      — portfolio still loading; summary renders skeleton +
 *                      "Coverage indeterminate" + submit disabled
 *   - `no-portfolio` — portfolio not provided to the gate (e.g. surface
 *                      didn't thread it); same skeleton + warning as pending
 *                      so the operator never sees wrong risk silently
 *
 * `traceId` correlates with the per-session telemetry buffer
 * (`lib/order/risk/telemetry.ts`).
 */
export type CoverageStatus = "resolved" | "pending" | "no-portfolio";

// The brand symbol lives at runtime so dev-mode guards can detect a missing
// brand. It is exported only so producer code inside `lib/order/risk/`
// references it via the SAME module identity. Consumer code must not
// import it.
export const ORDER_RISK_BRAND: unique symbol = Symbol("AugmentedOrderSummary");

export type AugmentedOrderSummary = OrderPresentationSummary & {
  readonly [ORDER_RISK_BRAND]: "augmented";
  readonly coverageStatus: CoverageStatus;
  readonly traceId: string;
};

/**
 * Runtime guard — dev-mode `<OrderConfirmSummary>` uses this to catch a
 * non-branded literal that slipped past the type system (e.g. via an `as`
 * cast). Production builds skip the assertion; the brand is still enforced
 * at compile time.
 */
export function isAugmentedOrderSummary(
  value: unknown,
): value is AugmentedOrderSummary {
  if (value == null || typeof value !== "object") return false;
  return (value as Record<symbol, unknown>)[ORDER_RISK_BRAND] === "augmented";
}

/**
 * Legacy alias. Kept exported so the JSDoc on `OrderPresentationSummary` has
 * something to point at and so search-and-replace migrations don't fail
 * loudly. New code should reference `OrderPresentationSummary` or — if it
 * needs the risk fields — `AugmentedOrderSummary` from the risk module.
 *
 * @deprecated Use `AugmentedOrderSummary` via `useOrderRisk`. Plain literals
 * of this shape no longer satisfy `<OrderConfirmSummary>`'s prop type.
 */
export type OrderSummary = OrderPresentationSummary;

export interface PriceDisplayProps {
  prices: OrderPrices;
  showSpread?: boolean;
  compact?: boolean;
}

export interface LegDisplayProps {
  legs: OrderLeg[];
  compact?: boolean;
  showPrices?: boolean;
}

export interface OrderFormProps {
  ticker: string;
  type: OrderType;
  legs?: OrderLeg[];
  defaultAction?: OrderAction;
  defaultQuantity?: number;
  onOrderPlaced?: () => void;
}
