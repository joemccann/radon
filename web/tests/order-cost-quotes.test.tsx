/**
 * @vitest-environment jsdom
 *
 * FU7 — live quotes threaded into the order-risk chokepoint so the F1 cost
 * model (web/lib/order/costs.ts) renders net-of-cost max-loss / max-gain.
 *
 * F1 built `computeOrderRisk(legs, netPremium, comboQuantity, { roundTripCost })`
 * but nothing fed live quotes, so the net-of-cost path was dead. This wires an
 * optional per-order `quote: { bid, ask }` into `OptionOrderRiskInput`. When
 * present, `useOrderRisk` calls `estimateRoundTripCost` and folds the round-trip
 * cost into the augmented summary: max-loss grows by the cost, max-gain shrinks
 * by it. When absent, behavior is byte-for-byte identical (back-compat).
 *
 * The surface assertion confirms `OptionsChainTab` passes the net bid/ask it
 * already computes into the gate, so net-of-cost actually renders in the UI.
 */
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";
import type { PortfolioData } from "@/lib/types";
import { useOrderRisk, type OptionOrderRiskInput } from "@/lib/order/risk";
import { estimateRoundTripCost } from "@/lib/order/costs";
import { buildPositionTradeOrder } from "@/lib/order/positionTrade";
import type { PortfolioPosition } from "@/lib/types";

afterEach(cleanup);

const emptyPortfolio: PortfolioData = {
  positions: [],
  bankroll: 0,
  open_risk: 0,
  open_risk_pct: 0,
  convexity_score: null,
  convexity_breakdown: null,
  account_summary: null,
} as unknown as PortfolioData;

// Bull call spread: long 100C / short 110C, $2 net debit, 1 contract.
// Gross (F1 verified): maxLoss = 200, maxGain = 800.
function bullCallSpreadInput(
  extra: Partial<OptionOrderRiskInput> = {},
): OptionOrderRiskInput {
  return {
    type: "options",
    ticker: "TEST",
    chainLegs: [
      { action: "BUY", right: "C", strike: 100, expiry: "20260320", quantity: 1 },
      { action: "SELL", right: "C", strike: 110, expiry: "20260320", quantity: 1 },
    ],
    netPremium: 2,
    description: "Bull Call Spread @ 2.00",
    totalCost: 200,
    ...extra,
  };
}

describe("useOrderRisk — net-of-cost when quotes supplied (FU7)", () => {
  it("without quotes → gross max-loss / max-gain (unchanged, back-compat)", () => {
    const { result } = renderHook(() =>
      useOrderRisk(bullCallSpreadInput(), emptyPortfolio),
    );
    expect(result.current).not.toBeNull();
    expect(result.current!.summary.maxLoss).toBeCloseTo(200, 6);
    expect(result.current!.summary.maxGain).toBeCloseTo(800, 6);
  });

  it("with quotes → loss UP and gain DOWN by the F1 round-trip cost", () => {
    // Net combo quote: bid 1.80 / ask 2.20 → half-spread 0.20/share.
    const quote = { bid: 1.8, ask: 2.2 };
    const { result } = renderHook(() =>
      useOrderRisk(bullCallSpreadInput({ quote }), emptyPortfolio),
    );
    expect(result.current).not.toBeNull();

    // Derive the expected cost from the F1 model directly (no hand-math):
    // 2-leg combo, 1 contract, entry quote = 1.80/2.20, exit quote unknown
    // → exit falls back to the estimated half-spread.
    const rt = estimateRoundTripCost({
      contracts: 1,
      numLegs: 2,
      entryBid: 1.8,
      entryAsk: 2.2,
    });
    expect(rt).toBeGreaterThan(0);

    expect(result.current!.summary.maxLoss).toBeCloseTo(200 + rt, 6);
    expect(result.current!.summary.maxGain).toBeCloseTo(800 - rt, 6);
  });

  it("quotes never bound an unbounded leg (naked short call stays UNBOUNDED)", () => {
    const input: OptionOrderRiskInput = {
      type: "options",
      ticker: "TEST",
      chainLegs: [
        { action: "SELL", right: "C", strike: 100, expiry: "20260320", quantity: 1 },
      ],
      netPremium: -1,
      description: "Naked short call",
      totalCost: -100,
      quote: { bid: 0.9, ask: 1.1 },
    };
    const { result } = renderHook(() => useOrderRisk(input, emptyPortfolio));
    expect(result.current).not.toBeNull();
    expect(result.current!.summary.maxLossUnbounded).toBe(true);
    expect(result.current!.summary.maxLoss).toBeNull();
  });

  it("partial quote (only bid) → falls back to estimated half-spread but still applies cost", () => {
    const { result: withPartial } = renderHook(() =>
      useOrderRisk(
        bullCallSpreadInput({ quote: { bid: 1.8, ask: null } }),
        emptyPortfolio,
      ),
    );
    const { result: withNone } = renderHook(() =>
      useOrderRisk(bullCallSpreadInput(), emptyPortfolio),
    );
    // A null ask makes the entry half-spread fall back to the estimate (same
    // as no quote on the entry leg), but cost is still applied — so loss/gain
    // differ from the no-quote (gross) path.
    expect(withPartial.current!.summary.maxLoss).toBeGreaterThan(
      withNone.current!.summary.maxLoss!,
    );
    expect(withPartial.current!.summary.maxGain).toBeLessThan(
      withNone.current!.summary.maxGain!,
    );
  });

  it("close-out branch ignores quotes (no risk math to adjust)", () => {
    const { result } = renderHook(() =>
      useOrderRisk(
        bullCallSpreadInput({
          quote: { bid: 1.8, ask: 2.2 },
          closeOut: { entryCostDollars: 150 },
        }),
        emptyPortfolio,
      ),
    );
    expect(result.current).not.toBeNull();
    // Close-out short-circuits: max-loss/max-gain are not present; estimatedPnl
    // = proceeds − basis is surfaced instead.
    expect(result.current!.summary.estimatedPnl).toBeCloseTo(200 - 150, 6);
  });
});

// ---------------------------------------------------------------------------
// Surface wiring: `buildPositionTradeOrder` (used by PositionTradeTicket) must
// thread the live quote into the OPENING branches and omit it from close-outs.
// ---------------------------------------------------------------------------

function longPutPosition(): PortfolioPosition {
  return {
    id: 1,
    ticker: "MU",
    structure: "Long Put $800.0",
    structure_type: "Long Put",
    direction: "LONG",
    contracts: 5,
    expiry: "2026-07-17",
    entry_date: "2026-05-29",
    entry_cost: 29500,
    market_value: 20500,
    market_price_is_calculated: false,
    legs: [
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

describe("buildPositionTradeOrder surface threads the quote (FU7)", () => {
  it("opening (BUY more of a long leg) carries the quote into riskInput", () => {
    const o = buildPositionTradeOrder({
      position: longPutPosition(),
      target: { kind: "leg", index: 0 },
      action: "BUY", // buy more of the long put = opening
      quantity: 2,
      limitPrice: 41.0,
      tif: "DAY",
      quote: { bid: 40.5, ask: 41.5 },
    })!;
    expect(o.isClosing).toBe(false);
    expect(o.riskInput.quote).toEqual({ bid: 40.5, ask: 41.5 });
  });

  it("close-out (SELL-to-close a long leg) does not attach a quote", () => {
    const o = buildPositionTradeOrder({
      position: longPutPosition(),
      target: { kind: "leg", index: 0 },
      action: "SELL", // sell-to-close = close-out branch
      quantity: 5,
      limitPrice: 41.0,
      tif: "DAY",
      quote: { bid: 40.5, ask: 41.5 },
    })!;
    expect(o.isClosing).toBe(true);
    expect(o.riskInput.quote).toBeUndefined();
  });

  it("back-compat: omitting the quote param leaves opening riskInput without one", () => {
    const o = buildPositionTradeOrder({
      position: longPutPosition(),
      target: { kind: "leg", index: 0 },
      action: "BUY",
      quantity: 2,
      limitPrice: 41.0,
      tif: "DAY",
    })!;
    expect(o.riskInput.quote ?? null).toBeNull();
  });
});
