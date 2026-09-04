/**
 * @vitest-environment jsdom
 *
 * Linear-instrument branch of `useOrderRisk` — futures and stock. The bug
 * class on these surfaces was:
 *   - Audit gap #1: SPX SELL CALL bundled as Notional only, no UNBOUNDED
 *     (now covered by IndexOptionOrderForm migration which routes options).
 *   - **SHORT futures** (VIX, ES, etc.) — structurally unbounded; the
 *     FuturesOrderForm shipped with only an inline warning until this
 *     branch landed.
 *   - **SHORT stock** — BookTab StockOrderForm passed `portfolio={null}`
 *     to the gate before this branch existed, so a sell of borrowed shares
 *     showed only positive Notional with no warning.
 *
 * The discriminated `type: "linear"` input now flows through `<OrderRiskGate>`
 * exactly like options. Same brand, same telemetry, same submit-gating.
 */
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, renderHook } from "@testing-library/react";
import type { PortfolioData } from "@/lib/types";
import { OrderRiskGate, useOrderRisk } from "@/lib/order/risk";
import { isAugmentedOrderSummary } from "@/lib/order/types";
import { singleLegSubmitPermitted } from "@/components/SingleLegOrderTicket";

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

describe("useOrderRisk — linear branch (futures)", () => {
  it("SHORT VIX future at $19 × 1 contract → UNBOUNDED loss", () => {
    const input = {
      type: "linear" as const,
      ticker: "VIX",
      instrument: "future" as const,
      action: "SELL" as const,
      quantity: 1,
      limitPrice: 19,
      multiplier: 1000,
      description: "SELL 1 VIX Future @ $19",
    };
    const { result } = renderHook(() => useOrderRisk(input, emptyPortfolio));
    expect(result.current).not.toBeNull();
    expect(result.current!.summary.maxLossUnbounded).toBe(true);
    expect(result.current!.summary.maxLoss).toBeNull();
    // Max gain bounded at VIX-to-zero × 1000 multiplier = $19,000
    expect(result.current!.summary.maxGain).toBe(19_000);
    expect(result.current!.summary.undefinedRiskReason).toMatch(/short/i);
    // Unbounded but coverage-resolved → submittable (Gate 4 disabled).
    expect(result.current!.okToSubmit).toBe(true);
  });

  it("LONG VIX future at $19 × 1 contract → bounded loss = $19,000, unbounded gain", () => {
    const input = {
      type: "linear" as const,
      ticker: "VIX",
      instrument: "future" as const,
      action: "BUY" as const,
      quantity: 1,
      limitPrice: 19,
      multiplier: 1000,
      description: "BUY 1 VIX Future @ $19",
    };
    const { result } = renderHook(() => useOrderRisk(input, emptyPortfolio));
    expect(result.current!.summary.maxLoss).toBe(19_000);
    expect(result.current!.summary.maxLossUnbounded).toBe(false);
    expect(result.current!.summary.maxGainUnbounded).toBe(true);
    expect(result.current!.okToSubmit).toBe(true);
  });

  it("ES (S&P) future BUY at $5000 × 1 multiplier=50 → max loss $250,000", () => {
    const input = {
      type: "linear" as const,
      ticker: "ES",
      instrument: "future" as const,
      action: "BUY" as const,
      quantity: 1,
      limitPrice: 5000,
      multiplier: 50,
      description: "BUY 1 ES Future @ $5000",
    };
    const { result } = renderHook(() => useOrderRisk(input, emptyPortfolio));
    expect(result.current!.summary.maxLoss).toBe(250_000);
  });

  it("totalCost is signed by action: SELL → negative (credit), BUY → positive (debit)", () => {
    const sell = renderHook(() =>
      useOrderRisk(
        {
          type: "linear" as const,
          ticker: "VIX",
          instrument: "future" as const,
          action: "SELL" as const,
          quantity: 2,
          limitPrice: 20,
          multiplier: 1000,
          description: "SELL 2 VIX @ $20",
        },
        emptyPortfolio,
      ),
    );
    const buy = renderHook(() =>
      useOrderRisk(
        {
          type: "linear" as const,
          ticker: "VIX",
          instrument: "future" as const,
          action: "BUY" as const,
          quantity: 2,
          limitPrice: 20,
          multiplier: 1000,
          description: "BUY 2 VIX @ $20",
        },
        emptyPortfolio,
      ),
    );
    expect(sell.result.current!.summary.totalCost).toBe(-40_000);
    expect(buy.result.current!.summary.totalCost).toBe(40_000);
  });
});

describe("useOrderRisk — linear branch (stock)", () => {
  it("stock order uses stock risk variant not empty option legs", () => {
    const input = {
      type: "linear" as const,
      ticker: "WULF",
      instrument: "stock" as const,
      action: "BUY" as const,
      quantity: 100,
      limitPrice: 22.82,
      multiplier: 1,
      description: "BUY 100 WULF @ $22.82",
    };
    const { result } = renderHook(() => useOrderRisk(input, emptyPortfolio));

    expect(input.type).toBe("linear");
    expect("chainLegs" in input).toBe(false);
    expect(result.current!.summary.maxLoss).toBeCloseTo(2_282, 0);
    expect(result.current!.summary.maxGainUnbounded).toBe(true);
  });

  it("SHORT stock with no held shares → UNBOUNDED", () => {
    const input = {
      type: "linear" as const,
      ticker: "WULF",
      instrument: "stock" as const,
      action: "SELL" as const,
      quantity: 1000,
      limitPrice: 22.82,
      multiplier: 1,
      description: "SELL 1000 WULF @ $22.82",
    };
    const { result } = renderHook(() => useOrderRisk(input, emptyPortfolio));
    expect(result.current!.summary.maxLossUnbounded).toBe(true);
    expect(result.current!.summary.maxGain).toBeCloseTo(22_820, 0);
    // Unbounded but coverage-resolved → submittable (Gate 4 disabled).
    expect(result.current!.okToSubmit).toBe(true);
  });

  it("LONG stock at $22.82 × 1000 shares → max loss $22,820 (stock-to-zero), unbounded gain", () => {
    const input = {
      type: "linear" as const,
      ticker: "WULF",
      instrument: "stock" as const,
      action: "BUY" as const,
      quantity: 1000,
      limitPrice: 22.82,
      multiplier: 1,
      description: "BUY 1000 WULF @ $22.82",
    };
    const { result } = renderHook(() => useOrderRisk(input, emptyPortfolio));
    expect(result.current!.summary.maxLoss).toBeCloseTo(22_820, 0);
    expect(result.current!.summary.maxGainUnbounded).toBe(true);
    expect(result.current!.okToSubmit).toBe(true);
  });

  it("SELL N stock with held LONG ≥ N → pure close, max-loss/gain = 0", () => {
    const input = {
      type: "linear" as const,
      ticker: "RR",
      instrument: "stock" as const,
      action: "SELL" as const,
      quantity: 100,
      limitPrice: 2.86,
      multiplier: 1,
      heldQuantity: 10_000, // held >= sold
      description: "SELL 100 RR @ $2.86 (close)",
      closeOut: { entryCostDollars: 443 }, // 100 × $4.43
    };
    const { result } = renderHook(() => useOrderRisk(input, emptyPortfolio));
    expect(result.current!.summary.maxLoss).toBeUndefined();
    expect(result.current!.summary.maxGain).toBeUndefined();
    expect(result.current!.summary.totalLabel).toMatch(/Proceeds/);
    // Proceeds = 100 × 2.86 = 286; basis = 443; realised P&L = 286 - 443 = -157
    expect(result.current!.summary.totalCost).toBeCloseTo(286, 0);
    expect(result.current!.summary.estimatedPnl).toBeCloseTo(-157, 0);
    expect(result.current!.summary.estimatedPnlPct).toBeCloseTo((-157 / 443) * 100, 6);
    expect(result.current!.okToSubmit).toBe(true);
  });

  it("warns when a stock close leaves retained short calls uncovered", () => {
    const portfolioWithMargin = {
      ...emptyPortfolio,
      positions: [
        {
          ticker: "EWY",
          expiry: "2026-08-07",
          legs: [
            { type: "Stock", direction: "LONG", contracts: 2_500 },
            { type: "Call", direction: "SHORT", contracts: 25 },
          ],
        },
      ],
      account_summary: { available_funds: 500_000 },
    } as unknown as PortfolioData;
    const input = {
      type: "linear" as const,
      ticker: "EWY",
      instrument: "stock" as const,
      action: "SELL" as const,
      quantity: 2_500,
      limitPrice: 170,
      multiplier: 1,
      heldQuantity: 2_500,
      description: "SELL 2500 EWY Stock @ $170.00",
      closeOut: {
        entryCostDollars: 424_650,
      },
    };
    const { result } = renderHook(() => useOrderRisk(input, portfolioWithMargin));
    const summary = result.current!.summary;

    expect(summary.totalLabel).toBe("Proceeds:");
    expect(summary.totalCost).toBe(425_000);
    expect(summary.estimatedPnl).toBe(350);
    expect(summary.maxLossUnbounded).toBe(true);
    expect(summary.undefinedRiskReason).toContain("25 retained short calls uncovered");
    expect(summary.marginImpact?.requirement).toBeNull();
    expect(result.current!.okToSubmit).toBe(true);
  });

  it("SELL N stock with held LONG < N → partial-naked, UNBOUNDED on excess", () => {
    // Held 100 long, sell 150 → 100 close + 50 naked SHORT
    const input = {
      type: "linear" as const,
      ticker: "ABC",
      instrument: "stock" as const,
      action: "SELL" as const,
      quantity: 150,
      limitPrice: 50,
      multiplier: 1,
      heldQuantity: 100,
      description: "SELL 150 ABC @ $50",
    };
    const { result } = renderHook(() => useOrderRisk(input, emptyPortfolio));
    expect(result.current!.summary.maxLossUnbounded).toBe(true);
    // Max gain for the 50-share naked-short: 50 × $50 × 1 = $2,500
    expect(result.current!.summary.maxGain).toBe(2_500);
  });

  it("BUY-to-close held SHORT → max-loss/gain = 0, P&L = basis − cost", () => {
    const input = {
      type: "linear" as const,
      ticker: "XYZ",
      instrument: "stock" as const,
      action: "BUY" as const,
      quantity: 100,
      limitPrice: 50,
      multiplier: 1,
      heldShortQuantity: 100,
      description: "BUY 100 XYZ to cover @ $50",
      closeOut: { entryCostDollars: 6_000 }, // sold short at $60 = $6k credit basis
    };
    const { result } = renderHook(() => useOrderRisk(input, emptyPortfolio));
    expect(result.current!.summary.maxLoss).toBeUndefined();
    expect(result.current!.summary.maxGain).toBeUndefined();
    // Cost to cover = 100 × $50 = $5,000; basis = $6,000;
    // realised P&L (for short) = basis − cost = $1,000 profit
    expect(result.current!.summary.totalCost).toBe(5_000);
    expect(result.current!.summary.totalLabel).toMatch(/Cost to Cover/);
    expect(result.current!.summary.estimatedPnl).toBe(1_000);
    expect(result.current!.summary.estimatedPnlPct).toBeCloseTo((1_000 / 6_000) * 100, 6);
  });
});

describe("OrderRiskGate — linear inputs render branded summary", () => {
  it("single leg ticket cannot submit when gate blocks", () => {
    expect(singleLegSubmitPermitted(true, null)).toBe(false);
    expect(singleLegSubmitPermitted(true, { okToSubmit: false } as ReturnType<typeof useOrderRisk>)).toBe(false);
    expect(singleLegSubmitPermitted(true, { okToSubmit: true } as ReturnType<typeof useOrderRisk>)).toBe(true);
  });

  it("instrument modal blocks pending and null coverage", () => {
    const input = {
      type: "linear" as const,
      ticker: "WULF",
      instrument: "stock" as const,
      action: "BUY" as const,
      quantity: 1,
      limitPrice: 20,
      multiplier: 1,
      description: "BUY WULF",
    };
    const pending = renderHook(() => useOrderRisk(input, undefined));
    const absent = renderHook(() => useOrderRisk(input, null));
    expect(singleLegSubmitPermitted(true, pending.result.current)).toBe(false);
    expect(singleLegSubmitPermitted(true, absent.result.current)).toBe(false);
  });

  it("renders UNBOUNDED + warning when SHORT futures hands to the gate", () => {
    const { container } = render(
      <OrderRiskGate
        input={{
          type: "linear" as const,
          ticker: "VIX",
          instrument: "future" as const,
          action: "SELL" as const,
          quantity: 1,
          limitPrice: 19,
          multiplier: 1000,
          description: "SELL 1 VIX @ $19",
        }}
        portfolio={emptyPortfolio}
        surface="futures-form"
      />,
    );
    expect(container.textContent).toMatch(/SELL 1 VIX/);
    expect(container.textContent).toMatch(/UNBOUNDED/);
    expect(container.textContent).toMatch(/GATE 1/);
  });

  it("branded summary on linear path", () => {
    const { result } = renderHook(() =>
      useOrderRisk(
        {
          type: "linear" as const,
          ticker: "WULF",
          instrument: "stock" as const,
          action: "BUY" as const,
          quantity: 100,
          limitPrice: 22.82,
          multiplier: 1,
          description: "BUY 100 WULF",
        },
        emptyPortfolio,
      ),
    );
    expect(isAugmentedOrderSummary(result.current!.summary)).toBe(true);
    expect(result.current!.coverageStatus).toBe("resolved");
  });

  it("pending state for linear when portfolio is undefined", () => {
    const { result } = renderHook(() =>
      useOrderRisk(
        {
          type: "linear" as const,
          ticker: "WULF",
          instrument: "stock" as const,
          action: "BUY" as const,
          quantity: 100,
          limitPrice: 22.82,
          multiplier: 1,
          description: "BUY 100 WULF",
        },
        undefined,
      ),
    );
    expect(result.current!.coverageStatus).toBe("pending");
    expect(result.current!.okToSubmit).toBe(false);
  });
});

describe("useOrderRisk — backwards compatibility (option without type field)", () => {
  it("OptionOrderRiskInput without `type` still works (default = options)", () => {
    // Mirrors all existing call sites pre-2026-05-26 that don't pass `type`.
    const input = {
      ticker: "WULF",
      chainLegs: [
        { action: "SELL" as const, right: "C" as const, strike: 100, expiry: "20270115", quantity: 1 },
      ],
      netPremium: -2,
      description: "Short Call",
      totalCost: -200,
    };
    const { result } = renderHook(() => useOrderRisk(input, emptyPortfolio));
    // Naked short call → UNBOUNDED
    expect(result.current!.summary.maxLossUnbounded).toBe(true);
  });
});

/**
 * T-345: a close-out is a trade against a position the ticket cannot see when
 * coverage is `no-portfolio`. The option close-out refused to arm under that
 * state since 2026-08-13; the stock/futures close-out kept the original
 * unconditional `okToSubmit: true` (gated only on finiteness since R-322) and
 * armed Transmit behind a "Coverage indeterminate: portfolio not in scope"
 * skeleton. Both close-out paths now fail closed on unresolved coverage
 * (Gate 3 direction), and each contract is pinned separately so loosening
 * one reds exactly one case.
 */
describe("useOrderRisk — close-out coverage gate (T-345)", () => {
  const STOCK_CLOSE = {
    type: "linear" as const,
    ticker: "RR",
    instrument: "stock" as const,
    action: "SELL" as const,
    quantity: 100,
    limitPrice: 2.86,
    multiplier: 1,
    heldQuantity: 10_000,
    description: "SELL 100 RR @ $2.86 (close)",
    closeOut: { entryCostDollars: 443 },
  };

  const OPTION_CLOSE = {
    ticker: "RR",
    chainLegs: [
      { action: "SELL" as const, right: "C" as const, strike: 100, expiry: "2026-12-18", quantity: 5 },
    ],
    netPremium: -5,
    totalCost: 2_500,
    description: "SELL 5x RR 100C @ $5.00 (close)",
    closeOut: { entryCostDollars: 2_500 },
  };

  it("stock close-out under no-portfolio coverage refuses to arm submit", () => {
    const { result } = renderHook(() => useOrderRisk(STOCK_CLOSE, null));
    expect(result.current!.coverageStatus).toBe("no-portfolio");
    expect(result.current!.okToSubmit).toBe(false);
  });

  it("stock close-out under resolved coverage still arms submit", () => {
    const { result } = renderHook(() => useOrderRisk(STOCK_CLOSE, emptyPortfolio));
    expect(result.current!.coverageStatus).toBe("resolved");
    expect(result.current!.okToSubmit).toBe(true);
  });

  it("option close-out under no-portfolio coverage refuses to arm submit", () => {
    const { result } = renderHook(() => useOrderRisk(OPTION_CLOSE, null));
    expect(result.current!.coverageStatus).toBe("no-portfolio");
    expect(result.current!.okToSubmit).toBe(false);
  });
});
