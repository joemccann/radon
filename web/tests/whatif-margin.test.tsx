/**
 * @vitest-environment jsdom
 *
 * Phase-2 IB what-if margin layer:
 *   - whatIfKey includes price and portfolio revision so broker margin cannot go stale.
 *   - mergeWhatIfMargin preserves the brand and computes availableAfter.
 *   - useWhatIfMargin fires only for an undefined-risk combo when ENABLED.
 *   - OrderConfirmSummary renders loading / the "IB margin" tag.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, renderHook, waitFor } from "@testing-library/react";
import type { AccountSummary, PortfolioData } from "@/lib/types";
import { isAugmentedOrderSummary } from "@/lib/order/types";
import { useOrderRisk, useWhatIfMargin, mergeWhatIfMargin } from "@/lib/order/risk";
import { whatIfKey } from "@/lib/order/risk/internal/whatIfKey";
import { OrderConfirmSummary } from "@/lib/order/components/OrderConfirmSummary";

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

const resolvedPortfolio = {
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
  account_summary: accountSummary,
} as PortfolioData;

// Undefined-risk combo (ratio spread that the risk model leaves UNBOUNDED →
// marginImpact.requirement === null). This is the what-if-eligible case.
const UNDEFINED_COMBO = {
  ticker: "AVGO",
  chainLegs: [
    { action: "SELL" as const, right: "C" as const, strike: 1250, expiry: "20260626", quantity: 10 },
    { action: "BUY" as const, right: "C" as const, strike: 1200, expiry: "20260626", quantity: 5 },
  ],
  netPremium: -1.17,
  description: "Ratio combo",
  totalCost: -585,
  underlyingSpot: 1200,
};

// Defined-risk vertical (requirement === maxLoss, NOT null) → NOT eligible.
const DEFINED_SPREAD = {
  ticker: "GOOG",
  chainLegs: [
    { action: "BUY" as const, right: "C" as const, strike: 100, expiry: "20270115", quantity: 1 },
    { action: "SELL" as const, right: "C" as const, strike: 110, expiry: "20270115", quantity: 1 },
  ],
  netPremium: 2,
  description: "Bull Call Spread",
  totalCost: 200,
  underlyingSpot: 105,
};

describe("whatIfKey", () => {
  it("changes across different prices and portfolio revisions", () => {
    const a = whatIfKey({ ...UNDEFINED_COMBO, netPremium: -1.17 });
    const b = whatIfKey({ ...UNDEFINED_COMBO, netPremium: -2.5 });
    expect(a).not.toBeNull();
    expect(a).not.toBe(b);
    expect(whatIfKey(UNDEFINED_COMBO, "portfolio-a")).not.toBe(
      whatIfKey(UNDEFINED_COMBO, "portfolio-b"),
    );
  });

  it("is null for a single-leg input", () => {
    expect(whatIfKey({ ...UNDEFINED_COMBO, chainLegs: [UNDEFINED_COMBO.chainLegs[0]] })).toBeNull();
  });
});

describe("mergeWhatIfMargin", () => {
  it("preserves the brand, sets ib-whatif source, computes availableAfter", () => {
    const { result } = renderHook(() => useOrderRisk(UNDEFINED_COMBO, resolvedPortfolio));
    expect(result.current!.summary.marginImpact?.requirement).toBeNull(); // precondition

    const merged = mergeWhatIfMargin(result.current!.summary, 5000);
    expect(isAugmentedOrderSummary(merged)).toBe(true);
    expect(merged.marginImpact!.source).toBe("ib-whatif");
    expect(merged.marginImpact!.requirement).toBe(5000);
    expect(merged.marginImpact!.availableAfter).toBe(500_000 - 5000);
  });
});

describe("useWhatIfMargin", () => {
  it("fires for an undefined combo when enabled and POSTs /api/orders/whatif", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ initMargin: 5000 }) }));
    // @ts-expect-error test stub
    global.fetch = fetchMock;

    const { result } = renderHook(() => {
      const state = useOrderRisk(UNDEFINED_COMBO, resolvedPortfolio);
      return useWhatIfMargin(UNDEFINED_COMBO, state, true);
    });

    await waitFor(() => expect(result.current.status).toBe("success"), { timeout: 2500 });
    expect(result.current.initMargin).toBe(5000);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/orders/whatif",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("stays idle (no fetch) when the flag is disabled", async () => {
    const fetchMock = vi.fn();
    // @ts-expect-error test stub
    global.fetch = fetchMock;

    const { result } = renderHook(() => {
      const state = useOrderRisk(UNDEFINED_COMBO, resolvedPortfolio);
      return useWhatIfMargin(UNDEFINED_COMBO, state, false);
    });

    await new Promise((r) => setTimeout(r, 600));
    expect(result.current.status).toBe("idle");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("stays idle for a defined-risk combo even when enabled", async () => {
    const fetchMock = vi.fn();
    // @ts-expect-error test stub
    global.fetch = fetchMock;

    const { result } = renderHook(() => {
      const state = useOrderRisk(DEFINED_SPREAD, resolvedPortfolio);
      return useWhatIfMargin(DEFINED_SPREAD, state, true);
    });

    await new Promise((r) => setTimeout(r, 600));
    expect(result.current.status).toBe("idle");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refetches when the effective order price changes", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ initMargin: 5000 }) }));
    // @ts-expect-error test stub
    global.fetch = fetchMock;

    const { result, rerender } = renderHook(
      ({ premium }) => {
        const input = { ...UNDEFINED_COMBO, netPremium: premium };
        const state = useOrderRisk(input, resolvedPortfolio);
        return useWhatIfMargin(input, state, true);
      },
      { initialProps: { premium: -1.17 } },
    );

    await waitFor(() => expect(result.current.status).toBe("success"), { timeout: 2500 });
    rerender({ premium: -2.5 });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2), { timeout: 2500 });
  });

  it("refetches when the resolved portfolio state changes", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ initMargin: 5000 }) }));
    // @ts-expect-error test stub
    global.fetch = fetchMock;

    const { result, rerender } = renderHook(
      ({ portfolio }) => {
        const state = useOrderRisk(UNDEFINED_COMBO, portfolio);
        return useWhatIfMargin(UNDEFINED_COMBO, state, true);
      },
      { initialProps: { portfolio: resolvedPortfolio } },
    );

    await waitFor(() => expect(result.current.status).toBe("success"), { timeout: 2500 });
    rerender({
      portfolio: {
        ...resolvedPortfolio,
        positions: [{
          position_id: 99,
          ticker: "SPY",
          structure: "Long Stock",
          expiry: "N/A",
          contracts: 100,
          cost_basis: 50_000,
          current_value: 51_000,
          unrealized_pnl: 1_000,
          return_pct: 2,
          legs: [{
            type: "Stock",
            strike: null,
            direction: "LONG",
            contracts: 100,
            avg_cost: 500,
          }],
        }],
      },
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2), { timeout: 2500 });
  });
});

describe("OrderConfirmSummary — what-if render states", () => {
  it("loading shows 'Calculating IB margin' instead of UNAVAILABLE", () => {
    const { result } = renderHook(() => useOrderRisk(UNDEFINED_COMBO, resolvedPortfolio));
    const { container } = render(
      <OrderConfirmSummary summary={result.current!.summary} marginWhatIf={{ status: "loading" }} />,
    );
    expect(container.textContent).toMatch(/Calculating IB margin/);
    expect(container.textContent).not.toMatch(/UNAVAILABLE/);
  });

  it("ib-whatif source renders the real number with an 'IB margin' tag", () => {
    const { result } = renderHook(() => useOrderRisk(UNDEFINED_COMBO, resolvedPortfolio));
    const merged = mergeWhatIfMargin(result.current!.summary, 5000);
    const { container } = render(<OrderConfirmSummary summary={merged} />);
    expect(container.textContent).toMatch(/IB margin/);
    expect(container.textContent).toMatch(/\$5,000/);
  });
});
