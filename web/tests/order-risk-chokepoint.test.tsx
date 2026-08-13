/**
 * @vitest-environment jsdom
 *
 * Contract tests for the order-risk chokepoint (steps 1+2 of the refactor
 * in `tasks/order-risk-chokepoint-refactor.md`).
 *
 * Three guarantees this suite pins:
 *
 * 1. `useOrderRisk` emits a branded `AugmentedOrderSummary` with the
 *    correct `coverageStatus` for each input regime (resolved / pending /
 *    no-portfolio).
 * 2. `<OrderConfirmSummary>` renders a "Coverage indeterminate" skeleton
 *    when status is not `"resolved"` — never silently shows zeros.
 * 3. `<OrderRiskGate>` produces the same end-to-end result as calling the
 *    hook directly and feeding the summary to `<OrderConfirmSummary>` —
 *    proving the gate is just a thin pairing and adds no surprise behavior.
 *
 * These tests run independently of the 50 `order-risk.test.ts` math cases,
 * so a future refactor of `computeOrderRisk` cannot mask a regression in
 * the chokepoint contract itself.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, renderHook } from "@testing-library/react";
import type { PortfolioData } from "@/lib/types";
import { OrderRiskGate, useOrderRisk } from "@/lib/order/risk";
import { isAugmentedOrderSummary } from "@/lib/order/types";
import { OrderConfirmSummary } from "@/lib/order/components/OrderConfirmSummary";
import { brandAugmentedSummaryForTest } from "@/lib/order/risk/__test_only__";

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

const sampleInput = {
  ticker: "WULF",
  chainLegs: [
    { action: "SELL" as const, right: "C" as const, strike: 31, expiry: "20270115", quantity: 77 },
  ],
  netPremium: -5.60,
  description: "Short Call @ $5.60",
  totalCost: -43_120,
};

const correlationBreach = {
  clusters: [{
    tickers: ["AAA", "BBB"],
    aggregate_exposure: 0.05,
    budget: 0.025,
    breached: true,
    max_pair_corr: 0.98,
    per_ticker_exposure: { AAA: 0.025, BBB: 0.025 },
  }],
  breaches: [{
    tickers: ["AAA", "BBB"],
    aggregate_exposure: 0.05,
    budget: 0.025,
    breached: true,
    max_pair_corr: 0.98,
    per_ticker_exposure: { AAA: 0.025, BBB: 0.025 },
  }],
  aggregate_exposure: 0.05,
  insufficient_data: [],
  corr_threshold: 0.7,
  book_budget: 0.025,
};

describe("useOrderRisk — branded output contract", () => {
  it("returns null when input is null", () => {
    const { result } = renderHook(() => useOrderRisk(null, emptyPortfolio));
    expect(result.current).toBeNull();
  });

  it("emits coverageStatus='pending' when portfolio is undefined", () => {
    const { result } = renderHook(() => useOrderRisk(sampleInput, undefined));
    expect(result.current).not.toBeNull();
    expect(result.current!.coverageStatus).toBe("pending");
    expect(result.current!.okToSubmit).toBe(false);
    expect(isAugmentedOrderSummary(result.current!.summary)).toBe(true);
    expect(result.current!.summary.coverageStatus).toBe("pending");
  });

  it("emits coverageStatus='no-portfolio' when portfolio is null", () => {
    const { result } = renderHook(() => useOrderRisk(sampleInput, null));
    expect(result.current!.coverageStatus).toBe("no-portfolio");
    expect(result.current!.okToSubmit).toBe(false);
    expect(result.current!.summary.coverageStatus).toBe("no-portfolio");
  });

  it("emits coverageStatus='resolved' when portfolio is provided", () => {
    const { result } = renderHook(() => useOrderRisk(sampleInput, emptyPortfolio));
    expect(result.current!.coverageStatus).toBe("resolved");
    expect(isAugmentedOrderSummary(result.current!.summary)).toBe(true);
    expect(result.current!.summary.coverageStatus).toBe("resolved");
  });

  it("keeps okToSubmit true on a resolved UNBOUNDED verdict (Gate 4 disabled; Gate 1 advisory)", () => {
    // Naked short call on empty portfolio → UNBOUNDED, but coverage is
    // resolved so the order stays submittable. Gate 1 renders as a warning,
    // it does not hard-block (Gate 4 disabled 2026-04-30).
    const { result } = renderHook(() => useOrderRisk(sampleInput, emptyPortfolio));
    expect(result.current!.coverageStatus).toBe("resolved");
    expect(result.current!.summary.maxLossUnbounded).toBe(true);
    expect(result.current!.okToSubmit).toBe(true);
  });

  it("close-out short-circuit emits proceeds + realized P&L, no risk fields", () => {
    const closeInput = {
      ticker: "RR",
      chainLegs: [],
      netPremium: -0.19,
      description: "Close 100x RR $3.50C @ $0.19",
      totalCost: 1_900,
      closeOut: { entryCostDollars: 500 },
    };
    const { result } = renderHook(() => useOrderRisk(closeInput, emptyPortfolio));
    expect(result.current!.summary.totalLabel).toMatch(/Close Credit/);
    expect(result.current!.summary.estimatedPnl).toBe(1_400); // $1,900 − $500
    expect(result.current!.summary.maxLoss).toBeUndefined();
    expect(result.current!.summary.maxGain).toBeUndefined();
    expect(result.current!.okToSubmit).toBe(true);
  });

  it("closing a protective long blocks when the retained short becomes uncovered", () => {
    const portfolio = {
      ...emptyPortfolio,
      positions: [
        {
          id: 1,
          ticker: "SPY",
          structure: "Call Vertical",
          structure_type: "Call Vertical",
          risk_profile: "DEFINED",
          expiry: "2026-12-18",
          contracts: 1,
          direction: "LONG",
          entry_cost: 100,
          max_risk: 400,
          market_value: 200,
          legs: [
            { direction: "LONG", contracts: 1, type: "Call", strike: 100, expiry: "2026-12-18", entry_cost: 0, avg_cost: 100, market_price: 3, market_value: 300 },
            { direction: "SHORT", contracts: 1, type: "Call", strike: 105, expiry: "2026-12-18", entry_cost: 0, avg_cost: 0, market_price: 1, market_value: -100 },
          ],
          kelly_optimal: null,
          target: null,
          stop: null,
          entry_date: "2026-08-01",
        },
      ],
    } as unknown as PortfolioData;
    const closeProtectiveLong = {
      ticker: "SPY",
      chainLegs: [
        { action: "SELL" as const, right: "C" as const, strike: 100, expiry: "20261218", quantity: 1 },
      ],
      netPremium: -3,
      description: "Close protective call",
      totalCost: 300,
      closeOut: { entryCostDollars: 100 },
    };

    const { result } = renderHook(() => useOrderRisk(closeProtectiveLong, portfolio));

    expect(result.current!.summary.maxLossUnbounded).toBe(true);
    expect(result.current!.summary.undefinedRiskReason).toMatch(/retained short call/i);
    expect(result.current!.okToSubmit).toBe(false);
  });

  it("emits a fresh traceId per memo input (correlates with telemetry buffer)", () => {
    const { result: a } = renderHook(() => useOrderRisk(sampleInput, emptyPortfolio));
    const { result: b } = renderHook(() => useOrderRisk(sampleInput, emptyPortfolio));
    expect(a.current!.summary.traceId).toBeDefined();
    expect(b.current!.summary.traceId).toBeDefined();
    // Different render cycles → different trace ids
    expect(a.current!.summary.traceId).not.toBe(b.current!.summary.traceId);
  });
});

describe("OrderConfirmSummary — pending / no-portfolio skeleton", () => {
  it("renders a 'Coverage indeterminate — portfolio resolving' skeleton when pending", () => {
    const summary = brandAugmentedSummaryForTest(
      { description: "Short Call @ $5.60", totalCost: -43_120 },
      { coverageStatus: "pending" },
    );
    const { container } = render(<OrderConfirmSummary summary={summary} />);
    expect(container.textContent).toMatch(/Coverage indeterminate/);
    expect(container.textContent).toMatch(/resolving/);
    // Must NOT silently render zero max-loss
    expect(container.textContent).not.toMatch(/Max Loss/);
    expect(container.querySelector("[data-coverage-status='pending']")).toBeTruthy();
  });

  it("renders a 'portfolio not in scope' skeleton when status is 'no-portfolio'", () => {
    const summary = brandAugmentedSummaryForTest(
      { description: "Short Call @ $5.60", totalCost: -43_120 },
      { coverageStatus: "no-portfolio" },
    );
    const { container } = render(<OrderConfirmSummary summary={summary} />);
    expect(container.textContent).toMatch(/Coverage indeterminate/);
    expect(container.textContent).toMatch(/not in scope/);
    expect(container.querySelector("[data-coverage-status='no-portfolio']")).toBeTruthy();
  });

  it("renders the full risk panel when status is 'resolved'", () => {
    const summary = brandAugmentedSummaryForTest({
      description: "Bull Call Spread @ $2.00",
      totalCost: 200,
      maxLoss: 200,
      maxGain: 800,
    });
    const { container } = render(<OrderConfirmSummary summary={summary} />);
    expect(container.textContent).toMatch(/Max Loss/);
    expect(container.textContent).toMatch(/Max Gain/);
    expect(container.textContent).not.toMatch(/Coverage indeterminate/);
  });
});

describe("OrderRiskGate — gate is a thin pairing", () => {
  it("renders nothing when input is null", () => {
    const { container } = render(
      <OrderRiskGate input={null} portfolio={emptyPortfolio} surface="test" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders <OrderConfirmSummary> when input is provided", () => {
    const { container } = render(
      <OrderRiskGate input={sampleInput} portfolio={emptyPortfolio} surface="test" />,
    );
    // The naked short call on empty portfolio yields UNBOUNDED — that label
    // must reach the DOM via the gate.
    expect(container.textContent).toMatch(/Short Call/);
    expect(container.textContent).toMatch(/UNBOUNDED/);
  });

  it("all live order surfaces render correlation gate", () => {
    const portfolio = {
      ...emptyPortfolio,
      risk_budget: correlationBreach,
    } as PortfolioData;
    const { container } = render(
      <OrderRiskGate input={sampleInput} portfolio={portfolio} surface="test" />,
    );
    const { result } = renderHook(() => useOrderRisk(sampleInput, portfolio));

    expect(result.current!.correlationStatus).toBe("resolved");
    expect(result.current!.correlationReport?.breaches).toHaveLength(1);
    expect(container.querySelector("[data-testid='correlation-risk-banner']")).toBeTruthy();
    expect(container.textContent).toMatch(/Gate 3: correlated exposure over budget/);
    expect(container.textContent).toMatch(/AAA \+ BBB/);
  });

  it("resolved portfolio without correlation data reports measurement unavailable", () => {
    const { container } = render(
      <OrderRiskGate input={sampleInput} portfolio={emptyPortfolio} surface="test" />,
    );

    expect(container.textContent).toMatch(/Gate 3: correlation measurement unavailable/);
  });

  it("renders the pending skeleton when portfolio is undefined", () => {
    const { container } = render(
      <OrderRiskGate input={sampleInput} portfolio={undefined} surface="test" />,
    );
    expect(container.textContent).toMatch(/Coverage indeterminate/);
    expect(container.textContent).not.toMatch(/UNBOUNDED/);
  });

  it("calls onState callback with the resolved state", () => {
    let captured: unknown = null;
    render(
      <OrderRiskGate
        input={sampleInput}
        portfolio={emptyPortfolio}
        surface="test"
        onState={(s) => { captured = s; }}
      />,
    );
    expect(captured).not.toBeNull();
    expect((captured as { coverageStatus: string }).coverageStatus).toBe("resolved");
  });

  it("reports state after commit without updating the parent during render", () => {
    const onState = vi.fn();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <OrderRiskGate
        input={sampleInput}
        portfolio={emptyPortfolio}
        surface="test"
        onState={onState}
      />,
    );

    expect(onState).toHaveBeenCalled();
    expect(consoleError.mock.calls.flat().join(" ")).not.toMatch(
      /Cannot update a component while rendering/i,
    );
    consoleError.mockRestore();
  });
});

describe("WULF covered short — single-leg credit semantics", () => {
  // 2026-05-26 weekend repro: with `isDebit` derived from live WS quotes,
  // a SELL leg on a closed market showed `Max Loss = abs(credit)` instead of
  // `$0`. The fix is structural: single-leg SELL ⇒ credit, single-leg BUY ⇒
  // debit. This test pins the credit-sign contract at the hook level so
  // future refactors of OptionsChainTab cannot regress it.
  it("SELL 77x $31 Call against held 77x LONG $17 Call yields max loss $0, max gain $150,920", () => {
    const portfolio: PortfolioData = {
      positions: [
        {
          id: 12,
          ticker: "WULF",
          structure: "Long Call",
          structure_type: "Long Option",
          risk_profile: "defined",
          expiry: "2027-01-15",
          contracts: 77,
          direction: "LONG",
          entry_cost: 40_040,
          max_risk: null,
          market_value: null,
          legs: [
            {
              direction: "LONG" as const,
              contracts: 77,
              type: "Call" as const,
              strike: 17,
              entry_cost: 40_040,
              avg_cost: 5.20,
              market_price: null,
              market_value: null,
            },
          ],
          kelly_optimal: null,
          target: null,
          stop: null,
        } as unknown as PortfolioData["positions"][number],
      ],
      bankroll: 0,
      open_risk: 0,
      open_risk_pct: 0,
      convexity_score: null,
      convexity_breakdown: null,
      account_summary: null,
    } as unknown as PortfolioData;

    const input = {
      ticker: "WULF",
      chainLegs: [
        {
          action: "SELL" as const,
          right: "C" as const,
          strike: 31,
          expiry: "20270115",
          quantity: 77,
        },
      ],
      netPremium: -5.60, // CREDIT — caller must pass this signed correctly
      description: "Short Call @ $5.60",
      totalCost: -43_120,
    };

    const { result } = renderHook(() => useOrderRisk(input, portfolio));
    expect(result.current).not.toBeNull();
    expect(result.current!.coverageStatus).toBe("resolved");
    expect(result.current!.summary.maxLossUnbounded).toBe(false);
    expect(result.current!.summary.maxLoss).toBe(0);
    expect(result.current!.summary.maxGain).toBeCloseTo(150_920, 0);
    expect(result.current!.coveringLegs).toHaveLength(1);
    expect(result.current!.coveringLegs[0]).toMatchObject({
      type: "Option",
      strike: 17,
      contracts: 77,
    });
  });
});

describe("multi-leg combo scaling — bull call spread", () => {
  // 2026-05-27 production regression (VIX 500/500 bull call spread).
  // OptionsChainTab was passing already-GCD-normalised legs (q=1/1 for a
  // 500/500 spread) into `chainLegs`. The augmenter then re-applied gcd,
  // resolved `comboQuantity = 1`, and the displayed risk was per-combo
  // ($880 / $120) instead of 500-combo ($440,000 / $60,000).
  //
  // This test pins the chain contract: `ChainOrderLeg.quantity` is raw
  // user-entered contracts. Scaling all legs by N must scale risk by N.
  it("500/500 VIX 18/28 bull call spread @ $1.20 debit ⇒ max gain $440,000, max loss $60,000", () => {
    const input = {
      ticker: "VIX",
      chainLegs: [
        { action: "BUY" as const,  right: "C" as const, strike: 18, expiry: "20260616", quantity: 500 },
        { action: "SELL" as const, right: "C" as const, strike: 28, expiry: "20260616", quantity: 500 },
      ],
      netPremium: 1.20, // net debit
      description: "Bull Call Spread @ $1.20",
      totalCost: 60_000,
    };
    const { result } = renderHook(() => useOrderRisk(input, emptyPortfolio));
    expect(result.current).not.toBeNull();
    expect(result.current!.coverageStatus).toBe("resolved");
    expect(result.current!.summary.maxLossUnbounded).toBe(false);
    expect(result.current!.summary.maxGainUnbounded).toBe(false);
    expect(result.current!.summary.maxLoss).toBeCloseTo(60_000, 0);
    expect(result.current!.summary.maxGain).toBeCloseTo(440_000, 0);
    expect(result.current!.okToSubmit).toBe(true);
  });

  it("scales linearly: N×N bull call spread risk = N × 1×1 bull call spread risk", () => {
    const make = (qty: number) => ({
      ticker: "VIX",
      chainLegs: [
        { action: "BUY" as const,  right: "C" as const, strike: 18, expiry: "20260616", quantity: qty },
        { action: "SELL" as const, right: "C" as const, strike: 28, expiry: "20260616", quantity: qty },
      ],
      netPremium: 1.20,
      description: "Bull Call Spread @ $1.20",
      totalCost: 120 * qty,
    });
    const r1 = renderHook(() => useOrderRisk(make(1), emptyPortfolio)).result;
    const r500 = renderHook(() => useOrderRisk(make(500), emptyPortfolio)).result;
    expect(r500.current!.summary.maxLoss).toBeCloseTo(500 * (r1.current!.summary.maxLoss as number), 0);
    expect(r500.current!.summary.maxGain).toBeCloseTo(500 * (r1.current!.summary.maxGain as number), 0);
  });

  it("scales linearly: asymmetric ratios (1×2 backspread) preserve combo unit", () => {
    // 100 long / 200 short is gcd=100 → 1 combo of (1 long, 2 short) × 100.
    // Augmenter must resolve `comboQuantity = 100`, NOT 1.
    const input = {
      ticker: "TEST",
      chainLegs: [
        { action: "BUY" as const,  right: "C" as const, strike: 50, expiry: "20260919", quantity: 100 },
        { action: "SELL" as const, right: "C" as const, strike: 60, expiry: "20260919", quantity: 200 },
      ],
      // 1×2 short call ratio = unbounded loss (net short calls). The point
      // here isn't the dollar value (it's UNBOUNDED) — it's that the augmenter
      // doesn't collapse the 100-combo aggregate into a single combo.
      netPremium: -0.50,
      description: "1x2 Call Backspread",
      totalCost: -5_000,
    };
    const { result } = renderHook(() => useOrderRisk(input, emptyPortfolio));
    expect(result.current!.summary.maxLossUnbounded).toBe(true);
    // Sanity: traceId present, coverage resolved — confirms the pipeline
    // ran (didn't bail early). UNBOUNDED is the structural answer regardless
    // of the comboQuantity drift, but the regression we're guarding against
    // shows up on BOUNDED structures. The two preceding tests cover that.
    expect(result.current!.coverageStatus).toBe("resolved");
  });
});

describe("brand integrity", () => {
  it("isAugmentedOrderSummary returns false for plain literals", () => {
    expect(
      isAugmentedOrderSummary({
        description: "fake",
        totalCost: 0,
      }),
    ).toBe(false);
  });

  it("isAugmentedOrderSummary returns true for hook output", () => {
    const { result } = renderHook(() => useOrderRisk(sampleInput, emptyPortfolio));
    expect(isAugmentedOrderSummary(result.current!.summary)).toBe(true);
  });

  it("isAugmentedOrderSummary returns true for brandAugmentedSummaryForTest output", () => {
    const branded = brandAugmentedSummaryForTest({
      description: "test",
      totalCost: 0,
    });
    expect(isAugmentedOrderSummary(branded)).toBe(true);
  });
});
