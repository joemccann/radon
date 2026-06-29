/**
 * Structural identity for an order's IB what-if margin request.
 *
 * The key DELIBERATELY excludes the limit price / net premium so that price
 * keystrokes at the confirm step don't refire a real IB round-trip — the
 * initial-margin requirement is a property of the POSITION (legs + ratios), not
 * the price you pay for it. Returns null when the input is not a what-if-
 * eligible combo, which keeps `useWhatIfMargin` idle.
 *
 * Module-private (under internal/) like its risk-module siblings.
 */
import type { OrderRiskInput, OptionOrderRiskInput } from "../useOrderRisk";

function gcd(a: number, b: number): number {
  return b === 0 ? a : gcd(b, a % b);
}

export function whatIfKey(input: OrderRiskInput | null): string | null {
  if (input == null || input.type === "linear") return null;
  const opt = input as OptionOrderRiskInput;
  if (opt.closeOut != null) return null;
  const legs = opt.chainLegs;
  if (!Array.isArray(legs) || legs.length < 2) return null;

  const quantities = legs.map((l) => Math.max(1, Math.trunc(l.quantity)));
  const combos = quantities.reduce((a, b) => gcd(a, b));
  const parts = legs
    .map(
      (l, i) =>
        `${l.expiry}|${l.strike}|${l.right}|${l.action}|${Math.round(quantities[i] / combos)}`,
    )
    .sort();
  return `${opt.ticker.toUpperCase()}::x${combos}::${parts.join(",")}`;
}

/**
 * Build the FastAPI /orders/whatif body from a risk input. Reconstructs the
 * combo envelope (BUY entry) + per-leg ratios from the raw chain legs. Price is
 * informational for the margin requirement; a non-zero limit is required by the
 * combo schema, so net premium falls back to a tiny non-zero sentinel.
 */
export function buildWhatIfBody(input: OrderRiskInput): Record<string, unknown> | null {
  if (input.type === "linear") return null;
  const opt = input as OptionOrderRiskInput;
  const legs = opt.chainLegs;
  if (!Array.isArray(legs) || legs.length < 2) return null;

  const quantities = legs.map((l) => Math.max(1, Math.trunc(l.quantity)));
  const combos = quantities.reduce((a, b) => gcd(a, b));
  const limitPrice = opt.netPremium !== 0 ? opt.netPremium : -0.01;

  return {
    type: "combo",
    symbol: opt.ticker.toUpperCase(),
    action: "BUY",
    quantity: combos,
    limitPrice,
    tif: "DAY",
    legs: legs.map((l, i) => ({
      expiry: l.expiry,
      strike: l.strike,
      right: l.right,
      action: l.action,
      ratio: Math.round(quantities[i] / combos),
    })),
  };
}
