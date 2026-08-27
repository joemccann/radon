/**
 * Linear-instrument risk math — futures and stock.
 *
 * Where `computeOrderRisk` handles option payoffs (kinked at the strike,
 * with covering longs and stock-cover folds), `computeLinearRisk` handles
 * the straight-line case. Both pour into the same `OrderPresentationSummary`
 * shape so `<OrderConfirmSummary>` doesn't care which kind of instrument
 * produced the verdict.
 *
 * Payoff geometry:
 *
 *   LONG  → P&L = (S − entry) × qty × multiplier
 *           max-loss at S=0  → entry × qty × multiplier
 *           max-gain at S→∞  → UNBOUNDED
 *
 *   SHORT → P&L = (entry − S) × qty × multiplier
 *           max-loss at S→∞  → UNBOUNDED  ← the audit gap for SPX/futures
 *           max-gain at S=0  → entry × qty × multiplier
 *
 * Held-quantity close-out (stock; analogous for futures):
 *
 *   SELL N when held LONG M ≥ N      → pure close. maxLoss/maxGain = 0.
 *                                      Realised P&L = proceeds − cost basis.
 *   SELL N when held LONG 0 ≤ M < N  → M shares close, (N−M) naked SHORT.
 *                                      UNBOUNDED at the (N−M) tail.
 *   BUY  N when held SHORT M ≥ N     → buy-to-close. maxLoss/maxGain = 0.
 *
 * Reasonable bounds — same shape as `computeOrderRisk`'s return type. The
 * gate then renders the same UNBOUNDED label / Gate-1 warning the option
 * surfaces already use.
 */

import type { OrderRisk } from "./computeOrderRisk";

export interface LinearRiskInput {
  action: "BUY" | "SELL";
  /** Per-unit count: shares or futures contracts. */
  quantity: number;
  /** Per-unit signed price (positive magnitude; sign comes from action). */
  limitPrice: number;
  /** Contract multiplier: 1 for stock; instrument-specific for futures. */
  multiplier: number;
  /** Held LONG units on this instrument. Drives sell-to-close detection. */
  heldLong?: number;
  /** Held SHORT units on this instrument. Drives buy-to-close detection. */
  heldShort?: number;
}

export function computeLinearRisk(input: LinearRiskInput): OrderRisk {
  const qty = Math.max(0, input.quantity);
  const price = Math.max(0, input.limitPrice);
  const mult = Math.max(1, input.multiplier);
  if (qty === 0 || price === 0) {
    return {
      maxLoss: null,
      maxGain: null,
      maxLossUnbounded: false,
      maxGainUnbounded: false,
      hasUndefinedRisk: false,
      undefinedRiskReason: null,
    };
  }

  const heldLong = Math.max(0, input.heldLong ?? 0);
  const heldShort = Math.max(0, input.heldShort ?? 0);

  // Close-out short-circuit. SELL against LONG OR BUY against SHORT, up to
  // the held quantity, is a pure close — adds no new exposure. The (qty −
  // held) excess is treated as a fresh open in the opposite direction.
  const closingLong = input.action === "SELL" && heldLong >= qty;
  const closingShort = input.action === "BUY" && heldShort >= qty;
  if (closingLong || closingShort) {
    return {
      maxLoss: 0,
      maxGain: 0,
      maxLossUnbounded: false,
      maxGainUnbounded: false,
      hasUndefinedRisk: false,
      undefinedRiskReason: null,
    };
  }

  // Naked-excess for SELL against partial-LONG cover.
  let nakedQty = qty;
  if (input.action === "SELL" && heldLong > 0) {
    nakedQty = Math.max(0, qty - heldLong);
  } else if (input.action === "BUY" && heldShort > 0) {
    nakedQty = Math.max(0, qty - heldShort);
  }
  if (nakedQty === 0) {
    // Defensive: should have been caught by the close-out short-circuit
    // above, but if heldLong/Short rounding produced exactly N this branch
    // is the safety net.
    return {
      maxLoss: 0,
      maxGain: 0,
      maxLossUnbounded: false,
      maxGainUnbounded: false,
      hasUndefinedRisk: false,
      undefinedRiskReason: null,
    };
  }

  // Naked open — linear UNBOUNDED on the SELL side, bounded on the LONG side.
  const intrinsicCap = price * nakedQty * mult;

  if (input.action === "SELL") {
    // SHORT: max-loss UNBOUNDED, max-gain capped at price-to-zero × nakedQty.
    return {
      maxLoss: null,
      maxGain: intrinsicCap,
      maxLossUnbounded: true,
      maxGainUnbounded: false,
      hasUndefinedRisk: true,
      undefinedRiskReason: "Uncovered short: linear instrument has no price ceiling",
    };
  }

  // LONG: max-loss bounded at price-to-zero × nakedQty, max-gain UNBOUNDED.
  return {
    maxLoss: intrinsicCap,
    maxGain: null,
    maxLossUnbounded: false,
    maxGainUnbounded: true,
    hasUndefinedRisk: false,
    undefinedRiskReason: null,
  };
}
