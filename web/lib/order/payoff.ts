/**
 * Payoff at expiry for an option combo.
 *
 * Exact intrinsic arithmetic — no volatility, no rate, no model. The ticket
 * rail draws this curve and marks its breakevens next to the transmit button,
 * so it must be arithmetic the operator can check by hand rather than a
 * projection that depends on an assumption they cannot see.
 *
 * Everything here is per SHARE and per ONE combo. Multiply by 100 and by the
 * combo count at the display layer, the way the rest of the order code does.
 */

export type PayoffLeg = {
  action: "BUY" | "SELL";
  right: "C" | "P";
  /** Strike in dollars. */
  strike: number;
  /** Contracts of this leg per one combo (the ratio, not the total). */
  quantity: number;
};

export type PayoffPoint = { underlying: number; pnl: number };

export type PayoffCurve = {
  points: PayoffPoint[];
  /** Underlying prices where the combo crosses zero, ascending. */
  breakevens: number[];
  min: number;
  max: number;
};

function intrinsic(right: "C" | "P", strike: number, underlying: number): number {
  return right === "C" ? Math.max(0, underlying - strike) : Math.max(0, strike - underlying);
}

/**
 * P&L per share for one combo at an expiry underlying price.
 *
 * `netPremium` follows the repo's sign convention: positive is a net DEBIT
 * paid, negative is a net CREDIT received.
 */
export function payoffAtExpiry(legs: PayoffLeg[], netPremium: number, underlying: number): number {
  const settlement = legs.reduce((total, leg) => {
    const value = intrinsic(leg.right, leg.strike, underlying) * leg.quantity;
    return total + (leg.action === "BUY" ? value : -value);
  }, 0);
  return settlement - netPremium;
}

/**
 * Resolve the ECONOMIC net premium for payoff math.
 *
 * The chain displays a single leg's premium as a positive number whichever way
 * it is traded — sign-flipping is a combo-only display concept. Payoff math
 * needs the real cash direction instead: a sold option is a credit received.
 * Feeding the display sign in makes a short call look like a bought call, so
 * its payoff never crosses zero and the ticket reports no breakeven.
 *
 * Combos already carry a correctly signed net price, so those pass through.
 */
export function netPremiumForPayoff(
  legs: PayoffLeg[],
  isCombo: boolean,
  signedOrDisplayPrice: number,
): number {
  if (isCombo) return signedOrDisplayPrice;
  const magnitude = Math.abs(signedOrDisplayPrice);
  return legs[0]?.action === "SELL" ? -magnitude : magnitude;
}

/** Linear interpolation of the zero crossing between two sampled points. */
function zeroCrossing(a: PayoffPoint, b: PayoffPoint): number | null {
  if (a.pnl === 0) return a.underlying;
  if ((a.pnl < 0) === (b.pnl < 0)) return null;
  const span = b.pnl - a.pnl;
  if (span === 0) return null;
  return a.underlying + ((0 - a.pnl) / span) * (b.underlying - a.underlying);
}

const SAMPLE_COUNT = 96;

/**
 * Sample the payoff across a range wide enough to show both wings.
 *
 * The range spans the strikes plus a margin, and always includes spot, so a
 * far-OTM ticket still shows where the position actually turns.
 */
export function payoffCurve(
  legs: PayoffLeg[],
  netPremium: number,
  { spot }: { spot: number },
): PayoffCurve {
  if (legs.length === 0) return { points: [], breakevens: [], min: 0, max: 0 };

  const strikes = legs.map((l) => l.strike);
  const anchors = [...strikes, spot].filter((n) => Number.isFinite(n) && n > 0);
  if (anchors.length === 0) return { points: [], breakevens: [], min: 0, max: 0 };

  const lo = Math.min(...anchors);
  const hi = Math.max(...anchors);
  // A flat span (single strike at spot) still needs a width to draw.
  const margin = Math.max((hi - lo) * 0.6, hi * 0.15, 1);
  const from = Math.max(0, lo - margin);
  const to = hi + margin;
  const step = (to - from) / (SAMPLE_COUNT - 1);

  const points: PayoffPoint[] = [];
  for (let i = 0; i < SAMPLE_COUNT; i += 1) {
    const underlying = from + i * step;
    points.push({ underlying, pnl: payoffAtExpiry(legs, netPremium, underlying) });
  }

  const breakevens: number[] = [];
  for (let i = 1; i < points.length; i += 1) {
    const crossing = zeroCrossing(points[i - 1], points[i]);
    if (crossing != null && !breakevens.some((b) => Math.abs(b - crossing) < 1e-6)) {
      breakevens.push(crossing);
    }
  }
  breakevens.sort((a, b) => a - b);

  const pnls = points.map((p) => p.pnl);
  return { points, breakevens, min: Math.min(...pnls), max: Math.max(...pnls) };
}
