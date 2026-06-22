/**
 * Tests for web/lib/order/costs.ts — shared transaction-cost + slippage model.
 *
 * This is the TypeScript mirror of scripts/costs.py. Both files MUST model the
 * SAME numbers so a Python backtest and the live order builder agree on what a
 * fill costs. Expected values are derived from first principles in comments.
 *
 * Also pins that `computeOrderRisk` reflects max-loss / max-gain NET of cost
 * when handed a round-trip cost estimate (entry pays cost, exit pays cost).
 */
import { describe, it, expect } from "vitest";
import {
  COMMISSION_PER_CONTRACT,
  COMMISSION_MIN_PER_ORDER,
  COMBO_LEG_SLIPPAGE_PENALTY,
  ESTIMATED_HALF_SPREAD_PER_CONTRACT,
  OPTION_MULTIPLIER,
  commissionCost,
  halfSpreadSlippage,
  comboFillPenalty,
  totalEntryCost,
  estimateRoundTripCost,
} from "../lib/order/costs";
import { computeOrderRisk, type OrderRiskLeg } from "../lib/orderRisk";

function leg(
  action: "BUY" | "SELL",
  right: "C" | "P",
  strike: number,
  quantity = 1,
  expiry = "20260320",
): OrderRiskLeg {
  return { action, right, strike, expiry, quantity };
}

describe("commissionCost — IB-style flat per contract with floor", () => {
  it("scales per contract above the floor", () => {
    // 10 × 0.65 = 6.50, above the 1.00 floor.
    const expected = 10 * COMMISSION_PER_CONTRACT;
    expect(expected).toBeGreaterThan(COMMISSION_MIN_PER_ORDER);
    expect(commissionCost(10)).toBeCloseTo(expected, 10);
  });

  it("applies the per-order floor for tiny orders", () => {
    // 1 × 0.65 = 0.65 < 1.00 → floor wins.
    expect(1 * COMMISSION_PER_CONTRACT).toBeLessThan(COMMISSION_MIN_PER_ORDER);
    expect(commissionCost(1)).toBeCloseTo(COMMISSION_MIN_PER_ORDER, 10);
  });

  it("zero contracts is zero (no order, floor does not apply)", () => {
    expect(commissionCost(0)).toBe(0);
  });

  it("negative contracts charged by magnitude", () => {
    expect(commissionCost(-10)).toBeCloseTo(commissionCost(10), 10);
  });
});

describe("halfSpreadSlippage — half the bid/ask spread × multiplier", () => {
  it("computes from a live quote", () => {
    // bid=1.00 ask=1.20 → half=0.10 per share; 5 × 0.10 × 100 = 50.
    const cost = halfSpreadSlippage(1.0, 1.2, 5);
    expect(cost).toBeCloseTo(0.1 * 5 * OPTION_MULTIPLIER, 10);
  });

  it("falls back to the estimate when the quote is missing", () => {
    const cost = halfSpreadSlippage(null, null, 3);
    expect(cost).toBeCloseTo(
      ESTIMATED_HALF_SPREAD_PER_CONTRACT * 3 * OPTION_MULTIPLIER,
      10,
    );
  });

  it("falls back on a crossed / zero-width quote (never understates to 0)", () => {
    const cost = halfSpreadSlippage(1.2, 1.0, 2);
    expect(cost).toBeCloseTo(
      ESTIMATED_HALF_SPREAD_PER_CONTRACT * 2 * OPTION_MULTIPLIER,
      10,
    );
  });
});

describe("comboFillPenalty — extra slippage for BAG legs", () => {
  it("single leg has no penalty", () => {
    expect(comboFillPenalty(1, 10)).toBe(0);
  });

  it("zero / negative legs have no penalty", () => {
    expect(comboFillPenalty(0, 10)).toBe(0);
  });

  it("two-leg penalty = 1 extra leg", () => {
    // (2-1) × 0.01 × 5 × 100 = 5.
    expect(comboFillPenalty(2, 5)).toBeCloseTo(
      1 * COMBO_LEG_SLIPPAGE_PENALTY * 5 * OPTION_MULTIPLIER,
      10,
    );
  });

  it("four-leg penalty scales with extra legs", () => {
    expect(comboFillPenalty(4, 2)).toBeCloseTo(
      3 * COMBO_LEG_SLIPPAGE_PENALTY * 2 * OPTION_MULTIPLIER,
      10,
    );
  });
});

describe("totalEntryCost + estimateRoundTripCost", () => {
  it("one-way = commission + half-spread + combo penalty", () => {
    const got = totalEntryCost({ contracts: 5, numLegs: 1, bid: 1.0, ask: 1.2 });
    const expected =
      commissionCost(5) +
      halfSpreadSlippage(1.0, 1.2, 5) +
      comboFillPenalty(1, 5);
    expect(got).toBeCloseTo(expected, 10);
  });

  it("round trip is double one-way when symmetric", () => {
    const oneWay = totalEntryCost({ contracts: 5, numLegs: 1, bid: 1.0, ask: 1.2 });
    const rt = estimateRoundTripCost({
      contracts: 5,
      numLegs: 1,
      entryBid: 1.0,
      entryAsk: 1.2,
      exitBid: 1.0,
      exitAsk: 1.2,
    });
    expect(rt).toBeCloseTo(2 * oneWay, 10);
  });

  it("exit quote defaults to the estimate when absent", () => {
    const entry = totalEntryCost({ contracts: 5, numLegs: 1, bid: 1.0, ask: 1.2 });
    const exitEst = totalEntryCost({ contracts: 5, numLegs: 1 });
    const rt = estimateRoundTripCost({
      contracts: 5,
      numLegs: 1,
      entryBid: 1.0,
      entryAsk: 1.2,
    });
    expect(rt).toBeCloseTo(entry + exitEst, 10);
  });
});

describe("computeOrderRisk — net of cost", () => {
  it("subtracts round-trip cost from gain and adds it to loss", () => {
    // Bull call spread: long 100C / short 110C, $2 net debit, 1 contract.
    // Gross: maxLoss = 200, maxGain = 800.
    const legs = [leg("BUY", "C", 100), leg("SELL", "C", 110)];
    const gross = computeOrderRisk(legs, 2, 1);
    expect(gross.maxLoss).toBeCloseTo(200, 6);
    expect(gross.maxGain).toBeCloseTo(800, 6);

    // 2-leg combo, 1 contract, no quote → estimated half-spread both ways.
    const rt = estimateRoundTripCost({ contracts: 1, numLegs: 2 });
    const net = computeOrderRisk(legs, 2, 1, { roundTripCost: rt });

    // Cost makes losses larger and gains smaller.
    expect(net.maxLoss).toBeCloseTo(200 + rt, 6);
    expect(net.maxGain).toBeCloseTo(800 - rt, 6);
  });

  it("clamps net max-gain at zero when cost exceeds gross gain", () => {
    const legs = [leg("BUY", "C", 100), leg("SELL", "C", 110)];
    const gross = computeOrderRisk(legs, 2, 1);
    const hugeCost = (gross.maxGain ?? 0) + 1000;
    const net = computeOrderRisk(legs, 2, 1, { roundTripCost: hugeCost });
    expect(net.maxGain).toBe(0);
  });

  it("leaves unbounded legs unbounded (cost does not bound them)", () => {
    // Naked short call → unbounded loss; cost cannot make it finite.
    const legs = [leg("SELL", "C", 100)];
    const net = computeOrderRisk(legs, 1, 1, { roundTripCost: 50 });
    expect(net.maxLossUnbounded).toBe(true);
    expect(net.maxLoss).toBeNull();
  });

  it("is a no-op when no cost is supplied (backwards compatible)", () => {
    const legs = [leg("BUY", "C", 100), leg("SELL", "C", 110)];
    const a = computeOrderRisk(legs, 2, 1);
    const b = computeOrderRisk(legs, 2, 1);
    expect(a).toEqual(b);
    expect(b.maxLoss).toBeCloseTo(200, 6);
  });
});
