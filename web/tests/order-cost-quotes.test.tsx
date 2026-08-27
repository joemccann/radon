/**
 * @vitest-environment jsdom
 *
 * Limit-priced tickets fill at `netPremium`. Live bid/ask still travel on
 * `OptionOrderRiskInput.quote` (surfaces keep threading them) but must not
 * haircut structural max-gain / max-loss: charging half the quoted spread on
 * top of a limit double-counts entry slippage, and at-expiry max is not an
 * exit trade. CBRS 40× short $182.5 put @ $4 rendered MAX GAIN $12,248 against
 * a $16,000 credit (bid 2.50 / ask 4.30).
 *
 * `computeOrderRisk(..., { roundTripCost })` remains the backtest path that
 * fills at mid. Surfaces that open (not close) still attach `quote` so that
 * path stays reachable; the ticket verdict stays structural.
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

describe("useOrderRisk — limit fill is structural max, not quoted-spread net", () => {
  it("without quotes → gross max-loss / max-gain (unchanged, back-compat)", () => {
    const { result } = renderHook(() =>
      useOrderRisk(bullCallSpreadInput(), emptyPortfolio),
    );
    expect(result.current).not.toBeNull();
    expect(result.current!.summary.maxLoss).toBeCloseTo(200, 6);
    expect(result.current!.summary.maxGain).toBeCloseTo(800, 6);
  });

  it("limit-priced short put max gain is the credit, not credit minus quoted spread", () => {
    // CBRS 2026-08-27: 40× $182.5 put sold at a $4 limit. Bid 2.50 / ask 4.30
    // (52% spread). Folding the F1 round-trip (half-spread on entry + estimated
    // exit) into the verdict rendered MAX GAIN $12,248 against a $16,000 credit.
    // The limit IS the fill; at-expiry max is not an exit trade.
    const input: OptionOrderRiskInput = {
      type: "options",
      ticker: "CBRS",
      chainLegs: [
        { action: "SELL", right: "P", strike: 182.5, expiry: "20260828", quantity: 40 },
      ],
      netPremium: -4,
      description: "Short Put @ 4.00",
      totalCost: -16_000,
      quote: { bid: 2.5, ask: 4.3 },
    };
    const { result } = renderHook(() => useOrderRisk(input, emptyPortfolio));
    expect(result.current).not.toBeNull();

    const rt = estimateRoundTripCost({
      contracts: 40,
      numLegs: 1,
      entryBid: 2.5,
      entryAsk: 4.3,
    });
    expect(rt).toBeCloseTo(3_752, 0);

    expect(result.current!.summary.maxGain).toBe(16_000);
    expect(result.current!.summary.maxLoss).toBe(714_000);
    expect(result.current!.summary.totalCost).toBe(-16_000);
    expect(result.current!.summary.maxGain).not.toBe(16_000 - rt);
    expect(result.current!.summary.maxLoss).not.toBe(714_000 + rt);
  });

  it("quoted spread does not haircut a limit-priced debit spread", () => {
    const quote = { bid: 1.8, ask: 2.2 };
    const { result } = renderHook(() =>
      useOrderRisk(bullCallSpreadInput({ quote }), emptyPortfolio),
    );
    expect(result.current).not.toBeNull();
    expect(result.current!.summary.maxLoss).toBeCloseTo(200, 6);
    expect(result.current!.summary.maxGain).toBeCloseTo(800, 6);
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

  it("partial quote leaves structural max-loss / max-gain unchanged", () => {
    const { result: withPartial } = renderHook(() =>
      useOrderRisk(
        bullCallSpreadInput({ quote: { bid: 1.8, ask: null } }),
        emptyPortfolio,
      ),
    );
    const { result: withNone } = renderHook(() =>
      useOrderRisk(bullCallSpreadInput(), emptyPortfolio),
    );
    expect(withPartial.current!.summary.maxLoss).toBe(withNone.current!.summary.maxLoss);
    expect(withPartial.current!.summary.maxGain).toBe(withNone.current!.summary.maxGain);
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
