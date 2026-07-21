/**
 * @vitest-environment jsdom
 *
 * Covered-call presentation at the order-risk chokepoint.
 *
 * EWY repro 2026-07-21: operator held 2,500 LONG shares @ $169.86 and sold
 * 25× $175 calls @ $4.45 credit. The augmentation pipeline correctly
 * composed the buy-write (bounded max loss), but the confirm summary still
 * presented the order as a naked short call:
 *
 *   - "Margin Req: $415,002" — the synthetic buy-write's maxLoss was used
 *     as the requirement. A covered call consumes NO new margin; the held
 *     shares are the collateral and are already margined in the account.
 *   - No covered-call labeling anywhere in the confirm panel — the operator
 *     could not tell the bounded stock-to-zero number from naked
 *     assignment-at-zero stress.
 *
 * These tests pin the fix: a `coverageNote` on the summary and a $0
 * incremental margin requirement for a fully stock-covered short call.
 *
 * Math (derived, not memorised):
 *   adjustedNetPremium = -4.45 + 169.86 = 165.41 per share per combo
 *   maxLoss  = 165.41 × 100 × 25 = $413,525   (stock to zero net of premium)
 *   maxGain  = (175 − 165.41) × 100 × 25 = $23,975
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, renderHook, screen } from "@testing-library/react";
import type { PortfolioData } from "@/lib/types";
import { OrderRiskGate, useOrderRisk } from "@/lib/order/risk";

afterEach(cleanup);

function makeStockPos(opts: {
  ticker: string;
  shares: number;
  avgCost: number;
}): PortfolioData["positions"][number] {
  return {
    id: 1,
    ticker: opts.ticker,
    structure: "LONG Stock",
    structure_type: "Stock",
    risk_profile: "equity",
    expiry: "",
    contracts: opts.shares,
    direction: "LONG",
    entry_cost: opts.shares * opts.avgCost,
    max_risk: null,
    market_value: null,
    legs: [
      {
        direction: "LONG",
        contracts: opts.shares,
        type: "Stock",
        strike: null,
        entry_cost: opts.shares * opts.avgCost,
        avg_cost: opts.avgCost,
        market_price: null,
        market_value: null,
      },
    ],
    kelly_optimal: null,
    target: null,
    stop: null,
  } as unknown as PortfolioData["positions"][number];
}

function makePortfolio(
  positions: PortfolioData["positions"][number][],
  availableFunds: number,
): PortfolioData {
  return {
    positions,
    bankroll: 0,
    open_risk: 0,
    open_risk_pct: 0,
    convexity_score: null,
    convexity_breakdown: null,
    account_summary: {
      available_funds: availableFunds,
      buying_power: availableFunds * 4,
    },
  } as unknown as PortfolioData;
}

const AVAILABLE_FUNDS = 663_002;

const ewyCoveredCallInput = {
  ticker: "EWY",
  chainLegs: [
    {
      action: "SELL" as const,
      right: "C" as const,
      strike: 175,
      expiry: "20260724",
      quantity: 25,
    },
  ],
  netPremium: -4.45,
  description: "Short Call @ $4.45",
  totalCost: -11_125,
  underlyingSpot: 173.53,
};

const ewyPortfolio = makePortfolio(
  [makeStockPos({ ticker: "EWY", shares: 2_500, avgCost: 169.86 })],
  AVAILABLE_FUNDS,
);

describe("fully stock-covered short call (EWY repro)", () => {
  it("surfaces a COVERED CALL note on the summary", () => {
    const { result } = renderHook(() =>
      useOrderRisk(ewyCoveredCallInput, ewyPortfolio),
    );
    const note = result.current!.summary.coverageNote;
    expect(note).toBeTruthy();
    expect(note).toContain("COVERED CALL");
    expect(note).toContain("2,500");
    expect(note).toContain("$169.86");
  });

  it("charges NO incremental margin: requirement 0, available funds unchanged", () => {
    const { result } = renderHook(() =>
      useOrderRisk(ewyCoveredCallInput, ewyPortfolio),
    );
    const margin = result.current!.summary.marginImpact;
    expect(margin).not.toBeNull();
    expect(margin!.requirement).toBe(0);
    expect(margin!.availableBefore).toBe(AVAILABLE_FUNDS);
    expect(margin!.availableAfter).toBe(AVAILABLE_FUNDS);
  });

  it("keeps the bounded buy-write risk math and stays submittable", () => {
    const { result } = renderHook(() =>
      useOrderRisk(ewyCoveredCallInput, ewyPortfolio),
    );
    const s = result.current!.summary;
    expect(s.maxLossUnbounded).not.toBe(true);
    expect(s.maxLoss).toBeCloseTo(413_525, 0);
    expect(s.maxGain).toBeCloseTo(23_975, 0);
    expect(result.current!.okToSubmit).toBe(true);
  });

  it("renders the coverage note inside <OrderConfirmSummary> via the gate", () => {
    render(
      <OrderRiskGate
        input={ewyCoveredCallInput}
        portfolio={ewyPortfolio}
        surface="test-covered-call"
      />,
    );
    const note = screen.getByTestId("order-confirm-coverage");
    expect(note.textContent).toContain("COVERED CALL");
    expect(note.textContent).toContain("2,500");
  });
});

describe("partially covered short call stays on the naked path", () => {
  // 1,000 shares cover only 10 of 25 calls; 15 remain naked → unbounded.
  const partialPortfolio = makePortfolio(
    [makeStockPos({ ticker: "EWY", shares: 1_000, avgCost: 169.86 })],
    AVAILABLE_FUNDS,
  );

  it("does not claim COVERED CALL and does not zero the margin requirement", () => {
    const { result } = renderHook(() =>
      useOrderRisk(ewyCoveredCallInput, partialPortfolio),
    );
    const s = result.current!.summary;
    expect(s.maxLossUnbounded).toBe(true);
    expect(s.coverageNote ?? "").not.toContain("COVERED CALL");
    const margin = s.marginImpact;
    expect(margin).not.toBeNull();
    expect(margin!.requirement).not.toBe(0);
    expect(result.current!.okToSubmit).toBe(false);
  });
});

describe("uncovered short call is unchanged", () => {
  const noStockPortfolio = makePortfolio([], AVAILABLE_FUNDS);

  it("has no coverage note and keeps the Reg-T naked estimate", () => {
    const { result } = renderHook(() =>
      useOrderRisk(ewyCoveredCallInput, noStockPortfolio),
    );
    const s = result.current!.summary;
    expect(s.coverageNote ?? null).toBeNull();
    expect(s.maxLossUnbounded).toBe(true);
    expect(s.marginImpact!.requirement).toBeGreaterThan(0);
  });
});
