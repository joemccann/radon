/**
 * @vitest-environment jsdom
 *
 * F13 — Paper-trading toggle contract.
 *
 * Two guarantees:
 *
 * 1. `<OrderRiskGate>` renders a Paper toggle when `onPaperModeChange` is
 *    supplied, and clicking it flips the mode and fires the callback.
 * 2. `resolvePlacementTarget` routes a Paper-mode order to the shadow engine
 *    and a live order to IB — the single source of truth for "where does this
 *    order go".
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import type { PortfolioData } from "@/lib/types";
import { OrderRiskGate } from "@/lib/order/risk";
import { resolvePlacementTarget } from "@/lib/order/risk";

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
    { action: "BUY" as const, right: "C" as const, strike: 31, expiry: "20270115", quantity: 10 },
  ],
  netPremium: 5.6,
  description: "Long Call @ $5.60",
  totalCost: 5_600,
};

describe("resolvePlacementTarget", () => {
  it("routes to the shadow engine when paper mode is on", () => {
    expect(resolvePlacementTarget(true)).toBe("paper-shadow");
  });

  it("routes to IB when paper mode is off", () => {
    expect(resolvePlacementTarget(false)).toBe("ib");
  });
});

describe("OrderRiskGate — Paper toggle", () => {
  it("renders the Paper toggle when onPaperModeChange is provided", () => {
    render(
      <OrderRiskGate
        input={sampleInput}
        portfolio={emptyPortfolio}
        surface="test-surface"
        paperMode={false}
        onPaperModeChange={() => {}}
      />,
    );
    expect(screen.getByTestId("paper-mode-toggle")).toBeTruthy();
  });

  it("does NOT render the Paper toggle when onPaperModeChange is omitted", () => {
    render(
      <OrderRiskGate
        input={sampleInput}
        portfolio={emptyPortfolio}
        surface="test-surface"
      />,
    );
    expect(screen.queryByTestId("paper-mode-toggle")).toBeNull();
  });

  it("fires onPaperModeChange with the flipped value on click", () => {
    const onChange = vi.fn();
    render(
      <OrderRiskGate
        input={sampleInput}
        portfolio={emptyPortfolio}
        surface="test-surface"
        paperMode={false}
        onPaperModeChange={onChange}
      />,
    );
    fireEvent.click(screen.getByTestId("paper-mode-toggle"));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});
