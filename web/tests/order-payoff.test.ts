import { describe, expect, it } from "vitest";
import { netPremiumForPayoff, payoffAtExpiry, payoffCurve, type PayoffLeg } from "@/lib/order/payoff";

/**
 * Payoff at expiry is exact intrinsic arithmetic - no volatility, no rate, no
 * model. That is deliberate: the ticket rail's P&L curve and its breakeven
 * markers must be arithmetic the operator can check by hand, not a projection.
 */

const SHORT_STRANGLE: PayoffLeg[] = [
  { action: "SELL", right: "P", strike: 245, quantity: 1 },
  { action: "SELL", right: "C", strike: 280, quantity: 1 },
];

// Sold for 2.98 per share of net credit.
const CREDIT = -2.98;

describe("payoffAtExpiry", () => {
  it("keeps the full credit between the strikes", () => {
    expect(payoffAtExpiry(SHORT_STRANGLE, CREDIT, 260)).toBeCloseTo(2.98, 10);
    expect(payoffAtExpiry(SHORT_STRANGLE, CREDIT, 245)).toBeCloseTo(2.98, 10);
    expect(payoffAtExpiry(SHORT_STRANGLE, CREDIT, 280)).toBeCloseTo(2.98, 10);
  });

  it("loses one-for-one outside the strikes", () => {
    // 5 points through the put strike, less the credit.
    expect(payoffAtExpiry(SHORT_STRANGLE, CREDIT, 240)).toBeCloseTo(2.98 - 5, 10);
    // 5 points through the call strike, less the credit.
    expect(payoffAtExpiry(SHORT_STRANGLE, CREDIT, 285)).toBeCloseTo(2.98 - 5, 10);
  });

  it("bottoms at the put strike less the credit when the underlying goes to zero", () => {
    expect(payoffAtExpiry(SHORT_STRANGLE, CREDIT, 0)).toBeCloseTo(2.98 - 245, 10);
  });

  it("prices a long call as a debit that only pays above the strike", () => {
    const longCall: PayoffLeg[] = [{ action: "BUY", right: "C", strike: 100, quantity: 1 }];
    expect(payoffAtExpiry(longCall, 4, 90)).toBeCloseTo(-4, 10);
    expect(payoffAtExpiry(longCall, 4, 100)).toBeCloseTo(-4, 10);
    expect(payoffAtExpiry(longCall, 4, 110)).toBeCloseTo(6, 10);
  });

  it("scales with per-combo ratio, so a 2:1 leg counts twice", () => {
    const ratioed: PayoffLeg[] = [{ action: "BUY", right: "C", strike: 100, quantity: 2 }];
    expect(payoffAtExpiry(ratioed, 0, 110)).toBeCloseTo(20, 10);
  });
});

describe("payoffCurve", () => {
  it("finds both breakevens of a short strangle", () => {
    const curve = payoffCurve(SHORT_STRANGLE, CREDIT, { spot: 261 });
    expect(curve.breakevens).toHaveLength(2);
    expect(curve.breakevens[0]).toBeCloseTo(242.02, 2);
    expect(curve.breakevens[1]).toBeCloseTo(282.98, 2);
  });

  it("finds the single breakeven of a long call", () => {
    const longCall: PayoffLeg[] = [{ action: "BUY", right: "C", strike: 100, quantity: 1 }];
    const curve = payoffCurve(longCall, 4, { spot: 100 });
    expect(curve.breakevens).toHaveLength(1);
    expect(curve.breakevens[0]).toBeCloseTo(104, 2);
  });

  it("returns points spanning the sampled range in ascending underlying order", () => {
    const curve = payoffCurve(SHORT_STRANGLE, CREDIT, { spot: 261 });
    expect(curve.points.length).toBeGreaterThan(16);
    const xs = curve.points.map((p) => p.underlying);
    expect([...xs].sort((a, b) => a - b)).toEqual(xs);
    expect(curve.min).toBeLessThan(0);
    expect(curve.max).toBeCloseTo(2.98, 6);
  });

  it("reports no breakeven for a position that never crosses zero", () => {
    // A credit with no short leg: pure profit at every price.
    const freeMoney: PayoffLeg[] = [{ action: "BUY", right: "C", strike: 100, quantity: 1 }];
    const curve = payoffCurve(freeMoney, -1, { spot: 100 });
    expect(curve.breakevens).toEqual([]);
  });

  it("degrades to an empty curve rather than guessing when there are no legs", () => {
    const curve = payoffCurve([], 0, { spot: 100 });
    expect(curve.points).toEqual([]);
    expect(curve.breakevens).toEqual([]);
  });
});

/**
 * The chain displays a single leg's premium as a positive number regardless of
 * side - sign-flipping is a combo-only display concept. Payoff math needs the
 * ECONOMIC sign instead: a sold option is a credit received. Passing the
 * display sign made a short call look like a bought call, so its payoff never
 * crossed zero and BREAKEVENS rendered "---" on a live ticket.
 */
describe("netPremiumForPayoff", () => {
  it("treats a single-leg sale as a credit", () => {
    const legs: PayoffLeg[] = [{ action: "SELL", right: "C", strike: 970, quantity: 1 }];
    expect(netPremiumForPayoff(legs, false, 2.98)).toBeCloseTo(-2.98, 10);
  });

  it("treats a single-leg purchase as a debit", () => {
    const legs: PayoffLeg[] = [{ action: "BUY", right: "C", strike: 970, quantity: 1 }];
    expect(netPremiumForPayoff(legs, false, 2.98)).toBeCloseTo(2.98, 10);
  });

  it("trusts the combo's already-signed net price", () => {
    const legs: PayoffLeg[] = [
      { action: "SELL", right: "P", strike: 245, quantity: 1 },
      { action: "SELL", right: "C", strike: 280, quantity: 1 },
    ];
    expect(netPremiumForPayoff(legs, true, -2.98)).toBeCloseTo(-2.98, 10);
  });

  it("gives a short call a breakeven at strike plus credit", () => {
    const legs: PayoffLeg[] = [{ action: "SELL", right: "C", strike: 970, quantity: 1 }];
    const premium = netPremiumForPayoff(legs, false, 2.98);
    const curve = payoffCurve(legs, premium, { spot: 967 });
    expect(curve.breakevens).toHaveLength(1);
    expect(curve.breakevens[0]).toBeCloseTo(972.98, 1);
  });
});
