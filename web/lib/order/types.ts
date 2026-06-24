/**
 * Unified Order System Types
 *
 * Shared type definitions for the order component system.
 */

export type OrderAction = "BUY" | "SELL";
export type OrderTif = "DAY" | "GTC";
export type OrderType = "stock" | "option" | "combo";

/** Computed prices for an order (single or spread) */
export interface OrderPrices {
  bid: number | null;
  mid: number | null;
  ask: number | null;
  spread: number | null;      // ask - bid
  spreadPct: number | null;   // spread / mid * 100
  available: boolean;         // true if all prices resolved
}

/** A single leg in a multi-leg order */
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

/** Order form state */
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

/** Validation result */
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
  estimatedPnlLabel?: string;
  /**
   * Phase-1 margin-impact estimate. Optional/nullable so existing producers
   * may omit it; it rides inside the existing brand (no brand change). Only
   * populated by `useOrderRisk` when coverage is resolved AND a baseline
   * (available funds / buying power) is present — never $0 for a missing
   * baseline. `source` is the Phase-1 union; Phase 2 will add `'ib-whatif'`.
   * For naked / undefined-risk shorts `requirement` is the Reg-T ESTIMATE,
   * NOT `maxLoss` (which is the assignment-at-zero stress bound and massively
   * overstates real margin).
   */
  marginImpact?: {
    requirement: number | null;
    availableBefore: number | null;
    availableAfter: number | null;
    baselineLabel: string;
    source: "exact-maxloss" | "regt-estimate";
    approximate: boolean;
  } | null;
}

/**
 * Branded augmented summary — the ONLY shape `<OrderConfirmSummary>` accepts.
 *
 * The `__augmented` brand is a `unique symbol` declared in the risk module.
 * It can only be attached by `useOrderRisk`. A plain literal `{ description:
 * "...", ... }` will FAIL TYPECHECK against `<OrderConfirmSummary>` because
 * it lacks the symbol-keyed property — and the symbol itself is not exported
 * from any module outside `lib/order/risk/`. Bypass requires defeating the
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
 * `traceId` correlates with the per-session telemetry buffer (added in a
 * follow-up step). Today it's a UUID slice; tomorrow it lights up bug
 * reports.
 */
export type CoverageStatus = "resolved" | "pending" | "no-portfolio";

// The brand symbol lives at runtime so dev-mode guards can detect a missing
// brand. It's intentionally NOT exported — only producer code inside
// `lib/order/risk/` references it via the SAME module identity. Consumer
// code cannot construct the symbol because they cannot import it.
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

/** Props for price-related components */
export interface PriceDisplayProps {
  prices: OrderPrices;
  showSpread?: boolean;
  compact?: boolean;
}

/** Props for leg display components */
export interface LegDisplayProps {
  legs: OrderLeg[];
  compact?: boolean;
  showPrices?: boolean;
}

/** Common order form props */
export interface OrderFormProps {
  ticker: string;
  type: OrderType;
  legs?: OrderLeg[];
  defaultAction?: OrderAction;
  defaultQuantity?: number;
  onOrderPlaced?: () => void;
}
