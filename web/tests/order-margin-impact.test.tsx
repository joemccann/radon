/**
 * @vitest-environment jsdom
 *
 * Phase-1 margin-impact estimate tests.
 *
 * Two layers:
 *   1. The pure `estimateInitialMargin` branches (via `__test_only__`).
 *   2. The `useOrderRisk` attachment + `<OrderConfirmSummary>` render of the
 *      two rows (Margin Req / <baseline> After).
 *
 * The load-bearing correctness invariant: a NAKED SHORT must NOT use `maxLoss`
 * (assignment-at-zero stress) as the margin requirement — it uses the Reg-T
 * estimate, which is an order of magnitude smaller.
 */
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, renderHook } from "@testing-library/react";
import type { AccountSummary, PortfolioData } from "@/lib/types";
import { useOrderRisk } from "@/lib/order/risk";
import { OrderConfirmSummary } from "@/lib/order/components/OrderConfirmSummary";
import { estimateInitialMargin } from "@/lib/order/risk/__test_only__";

afterEach(cleanup);

const accountSummary: AccountSummary = {
  net_liquidation: 1_000_000,
  daily_pnl: 0,
  unrealized_pnl: 0,
  realized_pnl: 0,
  settled_cash: 500_000,
  maintenance_margin: 0,
  excess_liquidity: 500_000,
  buying_power: 2_000_000,
  dividends: 0,
  available_funds: 500_000,
};

function portfolioWith(account: AccountSummary | undefined): PortfolioData {
  return {
    positions: [],
    bankroll: 0,
    peak_value: 0,
    last_sync: "",
    total_deployed_pct: 0,
    total_deployed_dollars: 0,
    remaining_capacity_pct: 0,
    position_count: 0,
    defined_risk_count: 0,
    undefined_risk_count: 0,
    avg_kelly_optimal: null,
    account_summary: account,
  } as PortfolioData;
}

const resolvedPortfolio = portfolioWith(accountSummary);

// ── 1. Pure estimator branches ───────────────────────────────────────────────

describe("estimateInitialMargin — pure branches", () => {
  it("naked short put uses Reg-T (NOT maxLoss): 15x MU 850P @ spot 118 → $138,150", () => {
    // perShare = max(0.20*118 - max(0, 850-118), 0.10*850) = max(-708.4, 85) = 85
    // requirement = 85*100*15 + |7.10*15*100| = 127,500 + 10,650 = 138,150
    const est = estimateInitialMargin({
      kind: "naked-option",
      right: "P",
      strike: 850,
      spot: 118,
      quantity: 15,
      premiumPerShare: 7.1,
    });
    expect(est.requirement).toBeCloseTo(138_150, 4);
    expect(est.source).toBe("regt-estimate");
    expect(est.approximate).toBe(true);
    // The maxLoss (assignment-at-zero) would be ~1.275M — must NOT be returned.
    const maxLoss = 850 * 100 * 15; // 1,275,000
    expect(est.requirement).toBeLessThan(maxLoss / 5);
  });

  it("naked short call uses 20% spot less OTM, floored at 10% strike", () => {
    // spot 100, strike 110 (OTM call): max(0.20*100 - max(0,100-110), 0.10*110)
    // = max(20, 11) = 20 → 20*100*1 + |2*1*100| = 2000 + 200 = 2200
    const est = estimateInitialMargin({
      kind: "naked-option",
      right: "C",
      strike: 110,
      spot: 100,
      quantity: 1,
      premiumPerShare: 2,
    });
    expect(est.requirement).toBeCloseTo(2200, 6);
    expect(est.source).toBe("regt-estimate");
  });

  it("naked short with no spot → requirement null (no guess)", () => {
    const est = estimateInitialMargin({
      kind: "naked-option",
      right: "P",
      strike: 100,
      spot: null,
      quantity: 1,
      premiumPerShare: 1,
    });
    expect(est.requirement).toBeNull();
  });

  it("defined-risk uses maxLoss exactly", () => {
    const est = estimateInitialMargin({ kind: "defined-risk", maxLoss: 4321 });
    expect(est.requirement).toBe(4321);
    expect(est.source).toBe("exact-maxloss");
    expect(est.approximate).toBe(false);
  });

  it("long-debit uses |totalCost|", () => {
    const est = estimateInitialMargin({ kind: "long-debit", totalCost: -2500 });
    expect(est.requirement).toBe(2500);
    expect(est.source).toBe("exact-maxloss");
    expect(est.approximate).toBe(false);
  });

  it("short stock → 50% of notional", () => {
    const est = estimateInitialMargin({ kind: "short-stock", price: 50, quantity: 100 });
    expect(est.requirement).toBe(2500);
    expect(est.approximate).toBe(true);
  });

  it("short future unbounded → requirement null (never maxLoss)", () => {
    const est = estimateInitialMargin({ kind: "short-future-unbounded" });
    expect(est.requirement).toBeNull();
  });

  it("close-out → requirement 0", () => {
    const est = estimateInitialMargin({ kind: "close-out" });
    expect(est.requirement).toBe(0);
    expect(est.approximate).toBe(false);
  });
});

// ── 2. useOrderRisk attaches marginImpact ─────────────────────────────────────

describe("useOrderRisk — marginImpact attachment", () => {
  it("naked short put: requirement is Reg-T, NOT maxLoss", () => {
    const input = {
      ticker: "MU",
      chainLegs: [
        { action: "SELL" as const, right: "P" as const, strike: 850, expiry: "20270115", quantity: 15 },
      ],
      netPremium: -7.1,
      description: "Short Put @ $7.10",
      totalCost: -10_650,
      underlyingSpot: 118,
    };
    const { result } = renderHook(() => useOrderRisk(input, resolvedPortfolio));
    const mi = result.current!.summary.marginImpact!;
    expect(mi.source).toBe("regt-estimate");
    expect(mi.approximate).toBe(true);
    expect(mi.requirement).toBeCloseTo(138_150, 4);
    // Must NOT equal the assignment-at-zero maxLoss the risk model produced.
    expect(result.current!.summary.maxLoss).not.toBeNull();
    expect(mi.requirement).not.toBeCloseTo(result.current!.summary.maxLoss as number, 0);
    expect(mi.baselineLabel).toBe("Available Funds");
    expect(mi.availableBefore).toBe(500_000);
    expect(mi.availableAfter).toBeCloseTo(500_000 - 138_150, 4);
  });

  it("defined-risk bull call spread: requirement === maxLoss, exact", () => {
    const input = {
      ticker: "GOOG",
      chainLegs: [
        { action: "BUY" as const, right: "C" as const, strike: 100, expiry: "20270115", quantity: 1 },
        { action: "SELL" as const, right: "C" as const, strike: 110, expiry: "20270115", quantity: 1 },
      ],
      netPremium: 2,
      description: "Bull Call Spread @ $2.00",
      totalCost: 200,
      underlyingSpot: 105,
    };
    const { result } = renderHook(() => useOrderRisk(input, resolvedPortfolio));
    const mi = result.current!.summary.marginImpact!;
    expect(mi.source).toBe("exact-maxloss");
    expect(mi.approximate).toBe(false);
    expect(mi.requirement).toBe(result.current!.summary.maxLoss);
  });

  it("long single call BUY: requirement === |totalCost|, no warn", () => {
    const input = {
      ticker: "AAPL",
      chainLegs: [
        { action: "BUY" as const, right: "C" as const, strike: 200, expiry: "20270115", quantity: 2 },
      ],
      netPremium: 5,
      description: "Long Call @ $5.00",
      totalCost: 1000,
      underlyingSpot: 210,
    };
    const { result } = renderHook(() => useOrderRisk(input, resolvedPortfolio));
    const mi = result.current!.summary.marginImpact!;
    expect(mi.requirement).toBe(1000);
    expect(mi.source).toBe("exact-maxloss");
    expect(mi.approximate).toBe(false);
    expect(mi.availableAfter! < 0).toBe(false);
  });

  it("BUY stock (linear): requirement === |totalCost|", () => {
    const input = {
      type: "linear" as const,
      ticker: "TSLA",
      action: "BUY" as const,
      quantity: 100,
      limitPrice: 250,
      multiplier: 1,
      instrument: "stock" as const,
      description: "BUY 100 TSLA @ $250",
    };
    const { result } = renderHook(() => useOrderRisk(input, resolvedPortfolio));
    const mi = result.current!.summary.marginImpact!;
    expect(mi.requirement).toBe(25_000);
    expect(mi.source).toBe("exact-maxloss");
  });

  it("no account_summary → marginImpact === null (never $0)", () => {
    const noAccount = portfolioWith(undefined);
    const input = {
      ticker: "MU",
      chainLegs: [
        { action: "SELL" as const, right: "P" as const, strike: 850, expiry: "20270115", quantity: 15 },
      ],
      netPremium: -7.1,
      description: "Short Put @ $7.10",
      totalCost: -10_650,
      underlyingSpot: 118,
    };
    const { result } = renderHook(() => useOrderRisk(input, noAccount));
    expect(result.current!.coverageStatus).toBe("resolved");
    expect(result.current!.summary.marginImpact).toBeNull();
  });

  it("exceeds available → availableAfter < 0", () => {
    const small = portfolioWith({ ...accountSummary, available_funds: 50_000 });
    const input = {
      ticker: "MU",
      chainLegs: [
        { action: "SELL" as const, right: "P" as const, strike: 850, expiry: "20270115", quantity: 15 },
      ],
      netPremium: -7.1,
      description: "Short Put @ $7.10",
      totalCost: -10_650,
      underlyingSpot: 118,
    };
    const { result } = renderHook(() => useOrderRisk(input, small));
    const mi = result.current!.summary.marginImpact!;
    expect(mi.requirement!).toBeGreaterThan(50_000);
    expect(mi.availableAfter!).toBeLessThan(0);
  });

  it("close-out (sell-to-close long): requirement 0, no warn", () => {
    const input = {
      ticker: "AAPL",
      chainLegs: [],
      netPremium: -4,
      description: "Sell to close",
      totalCost: 4000,
      closeOut: { entryCostDollars: 1000 },
    };
    const { result } = renderHook(() => useOrderRisk(input, resolvedPortfolio));
    const mi = result.current!.summary.marginImpact!;
    expect(mi.requirement).toBe(0);
    expect(mi.source).toBe("exact-maxloss");
    expect(mi.availableAfter).toBe(500_000);
  });

  it("short VIX future (UNBOUNDED): requirement from estimator/null, NOT maxLoss", () => {
    const input = {
      type: "linear" as const,
      ticker: "VIX",
      action: "SELL" as const,
      quantity: 1,
      limitPrice: 18,
      multiplier: 1000,
      instrument: "future" as const,
      description: "SELL 1 VIX future @ 18",
    };
    const { result } = renderHook(() => useOrderRisk(input, resolvedPortfolio));
    const mi = result.current!.summary.marginImpact!;
    expect(result.current!.summary.maxLossUnbounded).toBe(true);
    expect(mi.requirement).toBeNull();
  });

  it("falls back to Buying Power label when available_funds absent", () => {
    const noAvail = portfolioWith({ ...accountSummary, available_funds: undefined });
    const input = {
      ticker: "GOOG",
      chainLegs: [
        { action: "BUY" as const, right: "C" as const, strike: 100, expiry: "20270115", quantity: 1 },
        { action: "SELL" as const, right: "C" as const, strike: 110, expiry: "20270115", quantity: 1 },
      ],
      netPremium: 2,
      description: "Bull Call Spread @ $2.00",
      totalCost: 200,
      underlyingSpot: 105,
    };
    const { result } = renderHook(() => useOrderRisk(input, noAvail));
    const mi = result.current!.summary.marginImpact!;
    expect(mi.baselineLabel).toBe("Buying Power");
    expect(mi.availableBefore).toBe(2_000_000);
  });
});

// ── 3. OrderConfirmSummary renders the two rows ───────────────────────────────

describe("OrderConfirmSummary — margin rows render", () => {
  it("multi-leg unbounded combo: renders explicit unavailable margin impact instead of hiding it", () => {
    const input = {
      ticker: "AVGO",
      chainLegs: [
        { action: "SELL" as const, right: "C" as const, strike: 1250, expiry: "20260626", quantity: 10 },
        { action: "BUY" as const, right: "C" as const, strike: 1200, expiry: "20260626", quantity: 5 },
      ],
      netPremium: -1.17,
      description: "Bull Call Spread @ $-1.17",
      totalCost: -585,
      underlyingSpot: 1200,
    };
    const { result } = renderHook(() => useOrderRisk(input, resolvedPortfolio));
    expect(result.current!.summary.maxLossUnbounded).toBe(true);
    expect(result.current!.summary.marginImpact?.requirement).toBeNull();

    const { container } = render(
      <OrderConfirmSummary summary={result.current!.summary} variant="info" />,
    );
    const text = container.textContent ?? "";
    expect(text).toMatch(/Margin Req:/);
    expect(text).toMatch(/UNAVAILABLE/);
    expect(text).toMatch(/IB what-if required/);
    expect(text).toMatch(/Available Funds After:/);
    expect(text).toMatch(/---/);
  });

  it("naked short put: renders Margin Req with ~ + est. Reg-T and the After row", () => {
    const input = {
      ticker: "MU",
      chainLegs: [
        { action: "SELL" as const, right: "P" as const, strike: 850, expiry: "20270115", quantity: 15 },
      ],
      netPremium: -7.1,
      description: "Short Put @ $7.10",
      totalCost: -10_650,
      underlyingSpot: 118,
    };
    const { result } = renderHook(() => useOrderRisk(input, resolvedPortfolio));
    const { container } = render(
      <OrderConfirmSummary summary={result.current!.summary} variant="info" />,
    );
    const text = container.textContent ?? "";
    expect(text).toMatch(/Margin Req:/);
    expect(text).toMatch(/~\$138,150/);
    expect(text).toMatch(/est\. Reg-T/);
    expect(text).toMatch(/Available Funds After:/);
    expect(text).toMatch(/\$361,850/); // 500,000 - 138,150
  });

  it("exceeds-available: the After value carries data-margin-exceeded", () => {
    const small = portfolioWith({ ...accountSummary, available_funds: 50_000 });
    const input = {
      ticker: "MU",
      chainLegs: [
        { action: "SELL" as const, right: "P" as const, strike: 850, expiry: "20270115", quantity: 15 },
      ],
      netPremium: -7.1,
      description: "Short Put @ $7.10",
      totalCost: -10_650,
      underlyingSpot: 118,
    };
    const { result } = renderHook(() => useOrderRisk(input, small));
    const { container } = render(
      <OrderConfirmSummary summary={result.current!.summary} variant="info" />,
    );
    const exceeded = container.querySelector('[data-margin-exceeded="true"]');
    expect(exceeded).not.toBeNull();
  });

  it("no account_summary: no margin rows render", () => {
    const noAccount = portfolioWith(undefined);
    const input = {
      ticker: "MU",
      chainLegs: [
        { action: "SELL" as const, right: "P" as const, strike: 850, expiry: "20270115", quantity: 15 },
      ],
      netPremium: -7.1,
      description: "Short Put @ $7.10",
      totalCost: -10_650,
      underlyingSpot: 118,
    };
    const { result } = renderHook(() => useOrderRisk(input, noAccount));
    const { container } = render(
      <OrderConfirmSummary summary={result.current!.summary} variant="info" />,
    );
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/Margin Req:/);
  });
});
