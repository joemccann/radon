import { describe, expect, it } from "vitest";
import {
  buildPositionTradeOrder,
  closingActionFor,
  heldComboUnits,
  overClosesHeldCombo,
  type TradeTarget,
} from "../lib/order/positionTrade";
import {
  augmentOrderLegsWithPortfolioCoverage,
  computeOrderRisk,
} from "../lib/order/risk/__test_only__";
import type { OptionOrderRiskInput } from "@/lib/order";
import type { PortfolioData, PortfolioPosition } from "@/lib/types";

// A risk reversal like the screenshot: SHORT Call $1050 x3, LONG Put $800 x5.
// avg_cost is per-CONTRACT (already x100): short call $109.99/sh -> 10999,
// long put $59.00/sh -> 5900.
function riskReversal(): PortfolioPosition {
  return {
    id: 7,
    ticker: "MU",
    structure: "Ratio Reverse Risk Reversal 5x3 (P$800.0/C$1050.0)",
    structure_type: "Risk Reversal",
    direction: "COMBO",
    contracts: 5,
    expiry: "2026-07-17",
    entry_date: "2026-05-29",
    entry_cost: -3495,
    market_value: -46290,
    market_price_is_calculated: false,
    legs: [
      {
        direction: "SHORT",
        type: "Call",
        strike: 1050,
        contracts: 3,
        avg_cost: 10999,
        entry_cost: -32997,
        market_price: 133.93,
        market_price_is_calculated: false,
      },
      {
        direction: "LONG",
        type: "Put",
        strike: 800,
        contracts: 5,
        avg_cost: 5900,
        entry_cost: 29500,
        market_price: 41.0,
        market_price_is_calculated: false,
      },
    ],
  } as unknown as PortfolioPosition;
}

function portfolioWith(position: PortfolioPosition): PortfolioData {
  return { positions: [position], account_summary: null } as unknown as PortfolioData;
}

/**
 * Replays the resolved (non-close-out) half of `useOrderRisk` — augmentation
 * then `computeOrderRisk` — so a partial close can be checked for the naked
 * residue without a jsdom render. `useOrderRisk` branches on `closeOut != null`
 * alone, so an input with no `closeOut` provably lands here.
 */
function riskVerdictFor(input: OptionOrderRiskInput, portfolio: PortfolioData) {
  const augmented = augmentOrderLegsWithPortfolioCoverage(
    input.chainLegs,
    input.ticker,
    portfolio,
  );
  return {
    ...computeOrderRisk(
      augmented.riskLegs,
      input.netPremium + augmented.netPremiumAdjustment,
      augmented.comboQuantity,
    ),
    coveringLegs: augmented.coveringLegs,
  };
}

describe("closingActionFor", () => {
  it("combo closes with SELL", () => {
    expect(closingActionFor(riskReversal(), { kind: "combo" })).toBe("SELL");
  });
  it("long leg closes with SELL", () => {
    expect(closingActionFor(riskReversal(), { kind: "leg", index: 1 })).toBe("SELL");
  });
  it("short leg closes with BUY", () => {
    expect(closingActionFor(riskReversal(), { kind: "leg", index: 0 })).toBe("BUY");
  });
});

describe("buildPositionTradeOrder — combo", () => {
  it("SELL = close-out: combo payload with structure leg actions + closeOut basis", () => {
    const o = buildPositionTradeOrder({
      position: riskReversal(),
      target: { kind: "combo" },
      action: "SELL",
      quantity: 1,
      limitPrice: 2.0,
      tif: "DAY",
    })!;
    expect(o.isClosing).toBe(true);
    expect(o.payload.type).toBe("combo");
    expect(o.payload.action).toBe("SELL");
    // Leg actions encode STRUCTURE (SHORT call -> SELL, LONG put -> BUY).
    // Ratios encode the HELD 5x3 shape reduced by gcd(3, 5) = 1 → 3:5.
    // (This assertion pinned `ratio: 1` until T-020; a 1:1 BAG over-trades the
    // 3-contract call side of a 5x3 ratio structure.)
    expect(o.payload.legs).toEqual([
      { expiry: "20260717", strike: 1050, right: "C", action: "SELL", ratio: 3 },
      { expiry: "20260717", strike: 800, right: "P", action: "BUY", ratio: 5 },
    ]);
    // closeOut basis = resolveEntryCost (signed sum of leg entry costs).
    // -32997 (short, sign -1 * |entry|) + 29500 (long) = -3497... resolveEntryCost
    // uses sign*|entry_cost|: short -1*32997 + long +1*29500 = -3497.
    expect(o.riskInput.closeOut?.entryCostDollars).toBe(-3497);
    expect(o.riskInput.totalCost).toBe(1 * 2.0 * 100);
  });

  it("BUY = add: hands legs to the augmenter (no closeOut)", () => {
    const o = buildPositionTradeOrder({
      position: riskReversal(),
      target: { kind: "combo" },
      action: "BUY",
      quantity: 5,
      limitPrice: 2.0,
      tif: "DAY",
    })!;
    expect(o.isClosing).toBe(false);
    expect(o.riskInput.closeOut).toBeUndefined();
    expect(o.riskInput.chainLegs).toHaveLength(2);
  });

  it("scales close basis for a partial combo close", () => {
    const position = riskReversal();
    position.legs[0].contracts = 10;
    position.legs[1].contracts = 10;
    position.entry_cost = -2_000;
    position.legs[0].entry_cost = -5_000;
    position.legs[1].entry_cost = 3_000;

    const order = buildPositionTradeOrder({
      position,
      target: { kind: "combo" },
      action: "SELL",
      quantity: 4,
      limitPrice: 2,
      tif: "DAY",
    })!;

    expect(heldComboUnits(position)).toBe(10);
    expect(order.isClosing).toBe(true);
    expect(order.riskInput.closeOut?.entryCostDollars).toBe(-800);
  });

  it("does not classify an oversized combo SELL as a riskless close", () => {
    const position = riskReversal();
    const order = buildPositionTradeOrder({
      position,
      target: { kind: "combo" },
      action: "SELL",
      quantity: heldComboUnits(position) + 1,
      limitPrice: 2,
      tif: "DAY",
    })!;
    expect(order.isClosing).toBe(false);
    expect(order.riskInput.closeOut).toBeUndefined();
    expect(order.riskInput.chainLegs.length).toBeGreaterThan(0);
  });

  it("overclose is blocked before risk and placement", () => {
    expect(overClosesHeldCombo("SELL", 2, 1)).toBe(true);
    expect(overClosesHeldCombo("SELL", 1, 1)).toBe(false);
    expect(overClosesHeldCombo("BUY", 2, 1)).toBe(false);
  });

  it("preserves each calendar leg expiry in payload and risk input", () => {
    const position = riskReversal();
    position.legs[0].expiry = "2026-07-17";
    position.legs[1].expiry = "2026-09-18";
    const order = buildPositionTradeOrder({
      position,
      target: { kind: "combo" },
      action: "BUY",
      quantity: 1,
      limitPrice: 2,
      tif: "DAY",
    })!;
    expect((order.payload.legs as Array<{ expiry: string }>).map((leg) => leg.expiry)).toEqual([
      "20260717",
      "20260918",
    ]);
    expect(order.riskInput.chainLegs.map((leg) => leg.expiry)).toEqual([
      "20260717",
      "20260918",
    ]);
  });
});

describe("buildPositionTradeOrder — single leg", () => {
  it("SELL-to-close the LONG put: proceeds + realized P&L from per-contract basis", () => {
    const o = buildPositionTradeOrder({
      position: riskReversal(),
      target: { kind: "leg", index: 1 }, // long put $800, avg_cost 5900/contract
      action: "SELL",
      quantity: 5,
      limitPrice: 41.0,
      tif: "DAY",
    })!;
    expect(o.isClosing).toBe(true);
    expect(o.payload).toMatchObject({ type: "option", strike: 800, right: "P", action: "SELL", quantity: 5 });
    const proceeds = 5 * 41.0 * 100; // 20500
    expect(o.riskInput.totalCost).toBe(proceeds);
    expect(o.riskInput.totalLabel).toBe("Proceeds:");
    // basis = 5 * 5900 = 29500. pnl (computed by gate) = 20500 - 29500 = -9000.
    expect(o.riskInput.closeOut?.entryCostDollars).toBe(29500);
  });

  it("BUY-to-close the SHORT call: debit paid, basis is the original credit (negative)", () => {
    const o = buildPositionTradeOrder({
      position: riskReversal(),
      target: { kind: "leg", index: 0 }, // short call $1050, avg_cost 10999/contract
      action: "BUY",
      quantity: 3,
      limitPrice: 133.93,
      tif: "DAY",
    })!;
    expect(o.isClosing).toBe(true);
    expect(o.payload).toMatchObject({ type: "option", strike: 1050, right: "C", action: "BUY", quantity: 3 });
    const debit = 3 * 133.93 * 100; // 40179
    expect(o.riskInput.totalCost).toBe(-debit);
    expect(o.riskInput.totalLabel).toBe("Close Debit:");
    // basis negative = original credit. pnl = (-40179) - (-32997) = -7182.
    expect(o.riskInput.closeOut?.entryCostDollars).toBe(-(3 * 10999));
  });

  it("opening more of a leg routes through chainLegs (no closeOut)", () => {
    const o = buildPositionTradeOrder({
      position: riskReversal(),
      target: { kind: "leg", index: 1 }, // long put
      action: "BUY", // buy MORE of the long put = opening
      quantity: 2,
      limitPrice: 41.0,
      tif: "DAY",
    })!;
    expect(o.isClosing).toBe(false);
    expect(o.riskInput.closeOut).toBeUndefined();
    expect(o.riskInput.chainLegs).toEqual([
      { action: "BUY", right: "P", strike: 800, expiry: "2026-07-17", quantity: 2 },
    ]);
  });
});

// ---------------------------------------------------------------------------
// T-019 — a close is a close only up to the HELD size.
//
// `buildPositionTradeOrder` classified a close from direction + action alone,
// so SELL 10 against 5 held longs emitted `isClosing: true` + `closeOut`. The
// close-out short-circuit in `useOrderRisk` then zeroed max-loss/max-gain and
// the 5 naked shorts rendered as a riskless close. Mirrors the partial-cover
// split already in `computeLinearRisk` (SELL N vs held M < N → M closes, the
// (N − M) excess is a fresh naked open).
// ---------------------------------------------------------------------------

describe("buildPositionTradeOrder — over-closing a leg (T-019)", () => {
  it("SELL 10 against 5 held LONG puts is NOT a pure close", () => {
    const o = buildPositionTradeOrder({
      position: riskReversal(),
      target: { kind: "leg", index: 1 }, // LONG put $800, 5 held
      action: "SELL",
      quantity: 10,
      limitPrice: 41.0,
      tif: "DAY",
    })!;
    expect(o.isClosing).toBe(false);
    // No `closeOut` → `useOrderRisk` runs the augmentation pipeline instead of
    // short-circuiting to a riskless close.
    expect(o.riskInput.closeOut ?? null).toBeNull();
    // RAW order quantity, never pre-netted against the held longs — the
    // augmenter owns coverage (feedback_chain_order_legs_must_be_raw).
    expect(o.riskInput.chainLegs).toEqual([
      { action: "SELL", right: "P", strike: 800, expiry: "2026-07-17", quantity: 10 },
    ]);
    // The order itself is unchanged: IB still gets all 10 contracts.
    expect(o.payload).toMatchObject({ action: "SELL", quantity: 10 });
  });

  it("the 5-contract naked residue reaches the risk verdict", () => {
    const o = buildPositionTradeOrder({
      position: riskReversal(),
      target: { kind: "leg", index: 1 },
      action: "SELL",
      quantity: 10,
      limitPrice: 41.0,
      tif: "DAY",
    })!;
    const v = riskVerdictFor(o.riskInput, portfolioWith(riskReversal()));
    expect(v.undefinedRiskReason).toBe("Uncovered short put");
    expect(v.coveringLegs).toEqual([
      { type: "Option", right: "P", strike: 800, expiry: "20260717", contracts: 5 },
    ]);
    // 5 naked puts assigned at zero (5 × 800 × 100) less the whole order's
    // premium (10 × 41 × 100). Pre-fix this read maxLoss/maxGain = 0.
    expect(v.maxLoss).toBeCloseTo(5 * 800 * 100 - 10 * 41 * 100, 6);
    expect(v.maxGain).toBeCloseTo(10 * 41 * 100, 6);
  });

  it("BUY 10 against 3 held SHORT calls is NOT a pure close", () => {
    const o = buildPositionTradeOrder({
      position: riskReversal(),
      target: { kind: "leg", index: 0 }, // SHORT call $1050, 3 held
      action: "BUY",
      quantity: 10,
      limitPrice: 133.93,
      tif: "DAY",
    })!;
    expect(o.isClosing).toBe(false);
    expect(o.riskInput.closeOut ?? null).toBeNull();
    // Debit for all 10, not a fabricated realized P&L off a 10-contract basis.
    expect(o.riskInput.totalCost).toBeCloseTo(10 * 133.93 * 100, 6);
    const v = riskVerdictFor(o.riskInput, portfolioWith(riskReversal()));
    // The 7-contract residue is a fresh LONG call. Bounded loss, unbounded gain.
    expect(v.maxGainUnbounded).toBe(true);
    expect(v.maxLossUnbounded).toBe(false);
  });

  it("a PARTIAL close stays a close and scales basis by the closed quantity", () => {
    const o = buildPositionTradeOrder({
      position: riskReversal(),
      target: { kind: "leg", index: 1 },
      action: "SELL",
      quantity: 2, // 2 of 5 held
      limitPrice: 41.0,
      tif: "DAY",
    })!;
    expect(o.isClosing).toBe(true);
    expect(o.riskInput.totalLabel).toBe("Proceeds:");
    expect(o.riskInput.closeOut?.entryCostDollars).toBe(2 * 5900);
  });

  it("basis never exceeds the held size (min(quantity, held))", () => {
    // Exact-size close is the boundary: 5 of 5 held still uses 5 × avg_cost.
    const exact = buildPositionTradeOrder({
      position: riskReversal(),
      target: { kind: "leg", index: 1 },
      action: "SELL",
      quantity: 5,
      limitPrice: 41.0,
      tif: "DAY",
    })!;
    expect(exact.isClosing).toBe(true);
    expect(exact.riskInput.closeOut?.entryCostDollars).toBe(5 * 5900);
  });
});

// ---------------------------------------------------------------------------
// T-020 — ComboLeg.ratio must encode the HELD structure.
//
// The payload contract (`PlaceOrderComboLegSchema` → `ib_place_order.py`
// `cl.ratio = int(leg["ratio"])`) is: contracts traded on leg i =
// `payload.quantity × ratio_i`. Hardcoding `ratio: 1` shipped a 1:1 BAG for a
// 5x3 ratio structure, over-trading the 3-contract call side.
// ---------------------------------------------------------------------------

/** Same shape as riskReversal() with the per-leg contract counts overridden. */
function comboWithContracts(callContracts: number, putContracts: number): PortfolioPosition {
  const base = riskReversal();
  return {
    ...base,
    legs: [
      { ...base.legs[0], contracts: callContracts },
      { ...base.legs[1], contracts: putContracts },
    ],
  } as unknown as PortfolioPosition;
}

describe("buildPositionTradeOrder — combo leg ratios (T-020)", () => {
  it("heldComboUnits is the gcd of the per-leg contract counts", () => {
    expect(heldComboUnits(riskReversal())).toBe(1); // gcd(3, 5)
    expect(heldComboUnits(comboWithContracts(50, 50))).toBe(50);
    expect(heldComboUnits(comboWithContracts(6, 4))).toBe(2);
  });

  it("quantity × ratio equals the held contracts on a full close", () => {
    const position = riskReversal();
    const units = heldComboUnits(position);
    const o = buildPositionTradeOrder({
      position,
      target: { kind: "combo" },
      action: "SELL",
      quantity: units,
      limitPrice: 2.0,
      tif: "DAY",
    })!;
    const legs = o.payload.legs as Array<{ ratio: number }>;
    expect(legs.map((l) => units * l.ratio)).toEqual([3, 5]);
  });

  it("an equal-leg combo still reduces to 1:1 with units = the leg size", () => {
    const position = comboWithContracts(50, 50);
    expect(heldComboUnits(position)).toBe(50);
    const o = buildPositionTradeOrder({
      position,
      target: { kind: "combo" },
      action: "SELL",
      quantity: 50,
      limitPrice: 2.0,
      tif: "DAY",
    })!;
    const legs = o.payload.legs as Array<{ ratio: number }>;
    expect(legs.map((l) => l.ratio)).toEqual([1, 1]);
    expect(legs.map((l) => 50 * l.ratio)).toEqual([50, 50]);
  });

  it("a 6x4 combo reduces to 3:2 with units = 2", () => {
    const position = comboWithContracts(6, 4);
    const o = buildPositionTradeOrder({
      position,
      target: { kind: "combo" },
      action: "SELL",
      quantity: heldComboUnits(position),
      limitPrice: 2.0,
      tif: "DAY",
    })!;
    const legs = o.payload.legs as Array<{ ratio: number }>;
    expect(legs.map((l) => l.ratio)).toEqual([3, 2]);
    expect(legs.map((l) => 2 * l.ratio)).toEqual([6, 4]);
  });

  it("ratios are integers — ib_place_order.py does int(leg['ratio'])", () => {
    for (const [c, p] of [[3, 5], [50, 50], [6, 4], [7, 11], [1, 3]] as const) {
      const legs = buildPositionTradeOrder({
        position: comboWithContracts(c, p),
        target: { kind: "combo" },
        action: "SELL",
        quantity: 1,
        limitPrice: 2.0,
        tif: "DAY",
      })!.payload.legs as Array<{ ratio: number }>;
      for (const leg of legs) expect(Number.isInteger(leg.ratio)).toBe(true);
    }
  });

  it("combo BUY chainLegs carry RAW contract counts (quantity × ratio)", () => {
    const o = buildPositionTradeOrder({
      position: riskReversal(),
      target: { kind: "combo" },
      action: "BUY",
      quantity: 2,
      limitPrice: 2.0,
      tif: "DAY",
    })!;
    expect(o.riskInput.chainLegs).toEqual([
      { action: "SELL", right: "C", strike: 1050, expiry: "20260717", quantity: 6 },
      { action: "BUY", right: "P", strike: 800, expiry: "20260717", quantity: 10 },
    ]);
  });

  it("the risk model sees the 3:5 structure, not 1:1", () => {
    const o = buildPositionTradeOrder({
      position: riskReversal(),
      target: { kind: "combo" },
      action: "BUY",
      quantity: 2,
      limitPrice: 2.0,
      tif: "DAY",
    })!;
    const augmented = augmentOrderLegsWithPortfolioCoverage(o.riskInput.chainLegs, "MU", null);
    expect(augmented.comboQuantity).toBe(2);
    expect(augmented.riskLegs.map((l) => [l.right, l.action, l.quantity])).toEqual([
      ["C", "SELL", 3],
      ["P", "BUY", 5],
    ]);
  });
});
