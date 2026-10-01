// @vitest-environment jsdom
//
// Adding a leg against a held option makes a spread. The ticket's order-only
// MAX GAIN / MAX LOSS price the held leg at $0 (already paid for), which is
// the incremental economics of THIS order. The operator also needs the whole
// spread with the held leg at its cost basis.
//
// Repro (2026-10-01): held LONG 1000x VIX 2026-10-20 $20C, SELL 1000x $17C
// @ $1.72. The ticket showed max loss $128,000 (width $300,000 - $172,000
// credit) and nothing that counted what the $20C cost.

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, renderHook } from "@testing-library/react";

import TicketRiskBlock from "@/components/ticker-detail/TicketRiskBlock";
import { useOrderRisk } from "@/lib/order/risk";
import { augmentOrderLegsWithPortfolioCoverage } from "@/lib/order/risk/__test_only__";
import { OrderConfirmSummary } from "@/lib/order/components/OrderConfirmSummary";
import type { PortfolioData } from "@/lib/types";

afterEach(cleanup);

function portfolioWithLongCall(entryCost: number): PortfolioData {
  return {
    positions: [
      {
        id: 1,
        ticker: "VIX",
        structure: "Long Call",
        structure_type: "Long Option",
        risk_profile: "defined",
        expiry: "2026-10-20",
        contracts: 1000,
        direction: "LONG",
        entry_cost: entryCost,
        max_risk: null,
        market_value: null,
        legs: [
          {
            direction: "LONG",
            contracts: 1000,
            type: "Call",
            strike: 20,
            entry_cost: entryCost,
            avg_cost: entryCost / 1000,
            market_price: null,
            market_value: null,
          },
        ],
        kelly_optimal: null,
        target: null,
        stop: null,
      },
    ],
    bankroll: 0,
    open_risk: 0,
    open_risk_pct: 0,
    convexity_score: null,
    convexity_breakdown: null,
    account_summary: null,
  } as unknown as PortfolioData;
}

const SHORT_17C = {
  ticker: "VIX",
  chainLegs: [
    { action: "SELL" as const, right: "C" as const, strike: 17, expiry: "20261020", quantity: 1000 },
  ],
  netPremium: -1.72,
  description: "Short Call @ $1.72",
  totalCost: -172_000,
};

describe("held-leg cost basis through the augmenter", () => {
  it("carries the consumed lot's basis on the covering leg and as a per-share adjustment", () => {
    const aug = augmentOrderLegsWithPortfolioCoverage(
      SHORT_17C.chainLegs,
      "VIX",
      portfolioWithLongCall(50_000),
    );
    expect(aug.coveringLegs[0]).toMatchObject({ type: "Option", strike: 20, contracts: 1000, entryCostDollars: 50_000 });
    // $50,000 / (1000 combos x 100) = $0.50 per share per combo.
    expect(aug.heldOptionBasisAdjustment).toBeCloseTo(0.5, 10);
    // Not folded into the order-only adjustment.
    expect(aug.netPremiumAdjustment).toBe(0);
  });

  it("prorates basis when only part of the held lot covers the order", () => {
    const aug = augmentOrderLegsWithPortfolioCoverage(
      [{ ...SHORT_17C.chainLegs[0], quantity: 400 }],
      "VIX",
      portfolioWithLongCall(50_000),
    );
    expect(aug.coveringLegs[0]).toMatchObject({ contracts: 400, entryCostDollars: 20_000 });
  });

  it("reports unknown basis as null, never as free", () => {
    const aug = augmentOrderLegsWithPortfolioCoverage(
      SHORT_17C.chainLegs,
      "VIX",
      portfolioWithLongCall(Number.NaN),
    );
    expect(aug.heldOptionBasisAdjustment).toBeNull();
  });
});

describe("useOrderRisk — whole-spread figures", () => {
  it("keeps order-only figures and adds the spread with the held leg at basis", () => {
    const { result } = renderHook(() => useOrderRisk(SHORT_17C, portfolioWithLongCall(50_000)));
    const s = result.current!.summary;
    // Order-only, unchanged: width $300,000 - credit $172,000.
    expect(s.maxLoss).toBeCloseTo(128_000, 0);
    expect(s.maxGain).toBeCloseTo(172_000, 0);
    // Whole spread: credit $172,000 - basis $50,000 = $122,000 net credit.
    expect(s.withHeldLegs).toMatchObject({ maxGainUnbounded: false, maxLossUnbounded: false });
    expect(s.withHeldLegs!.maxGain).toBeCloseTo(122_000, 0);
    expect(s.withHeldLegs!.maxLoss).toBeCloseTo(178_000, 0);
    expect(s.withHeldLegs!.heldBasisDollars).toBeCloseTo(50_000, 0);
  });

  it("is null when no held option covers the order", () => {
    const empty = { ...portfolioWithLongCall(0), positions: [] } as PortfolioData;
    const { result } = renderHook(() => useOrderRisk(SHORT_17C, empty));
    expect(result.current!.summary.withHeldLegs ?? null).toBeNull();
  });

  it("is null under partial cover, where the order is already UNBOUNDED", () => {
    const oversized = { ...SHORT_17C, chainLegs: [{ ...SHORT_17C.chainLegs[0], quantity: 1200 }] };
    const { result } = renderHook(() => useOrderRisk(oversized, portfolioWithLongCall(50_000)));
    expect(result.current!.summary.maxLossUnbounded).toBe(true);
    expect(result.current!.summary.withHeldLegs ?? null).toBeNull();
  });

  it("shows unknown basis as unknown figures", () => {
    const { result } = renderHook(() => useOrderRisk(SHORT_17C, portfolioWithLongCall(Number.NaN)));
    expect(result.current!.summary.withHeldLegs).toMatchObject({ maxGain: null, maxLoss: null, heldBasisDollars: null });
  });

  it("renders both figure sets in the confirm summary", () => {
    const { result } = renderHook(() => useOrderRisk(SHORT_17C, portfolioWithLongCall(50_000)));
    const { getByTestId } = render(<OrderConfirmSummary summary={result.current!.summary} />);
    expect(getByTestId("order-confirm-spread-max-gain").textContent).toContain("$122,000");
    expect(getByTestId("order-confirm-spread-max-loss").textContent).toContain("$178,000");
  });
});

function cell(container: HTMLElement, label: string): string {
  const node = [...container.querySelectorAll(".ticket-risk-cell")].find(
    (el) => (el.querySelector(".ticket-risk-cell-label")?.textContent ?? "").trim() === label,
  );
  if (!node) throw new Error(`no risk cell labelled ${label}`);
  return (node.querySelector(".ticket-risk-cell-value")?.textContent ?? "").trim();
}

describe("TicketRiskBlock — spread section", () => {
  const base = {
    legs: [{ action: "SELL" as const, right: "C" as const, strike: 17, quantity: 1 }],
    netPremium: -1.72,
    spot: 16.43,
    maxGain: 172_000,
    maxLoss: 128_000,
    maxLossUnbounded: false,
    marginRequirement: 128_000,
    fundsAfter: -83_972,
    total: 172_000,
    isCredit: true,
  };

  it("renders spread max gain / loss beside the order-only figures", () => {
    const { container, getByTestId } = render(
      <TicketRiskBlock
        {...base}
        withHeldLegs={{ maxGain: 122_000, maxLoss: 178_000, maxGainUnbounded: false, maxLossUnbounded: false, heldBasisDollars: 50_000 }}
      />,
    );
    expect(cell(container, "MAX LOSS")).toBe("$128,000.00");
    expect(cell(container, "SPREAD MAX GAIN")).toBe("$122,000.00");
    expect(cell(container, "SPREAD MAX LOSS")).toBe("$178,000.00");
    expect(getByTestId("ticket-risk-spread").textContent).toContain("$50,000 BASIS");
  });

  it("renders no spread section without held coverage", () => {
    const { queryByTestId } = render(<TicketRiskBlock {...base} />);
    expect(queryByTestId("ticket-risk-spread")).toBeNull();
  });
});
