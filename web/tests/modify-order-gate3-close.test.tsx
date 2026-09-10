/**
 * @vitest-environment jsdom
 *
 * Gate 3 vs a working equity CLOSE inside ModifyOrderModal (2026-09-01 TQQQ).
 *
 * Live symptom: TQQQ Long Stock CLOSE, SELL 10,000 @ 68.80, with the book
 * carrying a breached SMH+SPY+TQQQ cluster (73.0% vs 2.5%, corr 0.92). The
 * modify modal rendered the critical Gate 3 breach with "before adding
 * correlated risk" copy on an order that REDUCES that stack, and read as a
 * block on the flatten.
 *
 * Contracts pinned here:
 *  1. A stock close that reduces a breached-cluster ticker renders NO Gate 3
 *     banner ("Gate 3 banner absent on reduce").
 *  2. Gate 3 never disables modify on a reduce: the armed Modify Order click
 *     reaches the wire — full URL, method, exact payload — through the REAL
 *     OrderActionsProvider (the component that owns the fetch), and nothing
 *     fires while the gate is still closed (no field changed).
 *  3. An ADD on the same ticker keeps the critical breach banner and its
 *     trim-or-hedge copy.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { OpenOrder, PortfolioData, PortfolioPosition } from "@/lib/types";
import type { RiskBudgetReport } from "@/lib/correlationRiskBanner";
import type { ModifyOrderRequest } from "@/lib/orderModify";
import ModifyOrderModal from "@/components/ModifyOrderModal";
import { OrderActionsProvider, useOrderActions } from "@/lib/OrderActionsContext";

vi.mock("@/lib/useRiskFreeRate", () => ({
  useRiskFreeRate: () => 0,
}));

vi.mock("@/components/Modal", () => ({
  default: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? React.createElement("div", { className: "mock-modal" }, children) : null,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

const BREACHED_STACK: RiskBudgetReport = {
  clusters: [
    {
      tickers: ["SMH", "SPY", "TQQQ"],
      aggregate_exposure: 0.73,
      budget: 0.025,
      breached: true,
      max_pair_corr: 0.92,
      per_ticker_exposure: { SMH: 0.3, SPY: 0.23, TQQQ: 0.2 },
    },
  ],
  breaches: [
    {
      tickers: ["SMH", "SPY", "TQQQ"],
      aggregate_exposure: 0.73,
      budget: 0.025,
      breached: true,
      max_pair_corr: 0.92,
      per_ticker_exposure: { SMH: 0.3, SPY: 0.23, TQQQ: 0.2 },
    },
  ],
  aggregate_exposure: 0.73,
  insufficient_data: [],
  corr_threshold: 0.7,
  book_budget: 0.025,
};

function tqqqSellOrder(): OpenOrder {
  return {
    orderId: 41,
    permId: 653640001,
    symbol: "TQQQ",
    contract: {
      conId: 320227,
      symbol: "TQQQ",
      secType: "STK",
      strike: null,
      right: null,
      expiry: null,
    },
    action: "SELL",
    orderType: "LMT",
    totalQuantity: 10_000,
    limitPrice: 68.8,
    auxPrice: null,
    status: "PreSubmitted",
    filled: 0,
    remaining: 10_000,
    avgFillPrice: null,
    tif: "DAY",
    outsideRth: true,
  };
}

function portfolio(withTqqqStock: boolean): PortfolioData {
  const positions: PortfolioPosition[] = withTqqqStock
    ? [{
        id: 7,
        ticker: "TQQQ",
        structure: "Long Stock (10,000 shares)",
        structure_type: "Stock",
        risk_profile: "Defined",
        expiry: "",
        contracts: 10_000,
        direction: "LONG",
        entry_cost: 660_000,
        max_risk: null,
        market_value: null,
        legs: [{
          direction: "LONG",
          contracts: 10_000,
          type: "Stock",
          strike: null,
          entry_cost: 660_000,
          avg_cost: 66,
          market_price: null,
          market_value: null,
        }],
        kelly_optimal: null,
        target: null,
        stop: null,
        entry_date: "2026-08-14",
      }]
    : [];

  return {
    bankroll: 1_500_000,
    peak_value: 1_500_000,
    last_sync: "2026-09-01T23:30:00.000Z",
    positions,
    total_deployed_pct: 0,
    total_deployed_dollars: 0,
    remaining_capacity_pct: 100,
    position_count: positions.length,
    defined_risk_count: positions.length,
    undefined_risk_count: 0,
    avg_kelly_optimal: null,
    risk_budget: BREACHED_STACK,
    account_summary: {
      net_liquidation: 1_500_000,
      daily_pnl: 0,
      unrealized_pnl: 0,
      realized_pnl: 0,
      settled_cash: 300_000,
      maintenance_margin: 0,
      excess_liquidity: 300_000,
      buying_power: 600_000,
      available_funds: 300_000,
      dividends: 0,
    },
  };
}

type RecordedCall = { url: string; method: string; body: unknown };

function recordFetch(): RecordedCall[] {
  const calls: RecordedCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
      calls.push({ url, method: init?.method ?? "GET", body });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

/** Mirrors the /orders page wiring: onConfirm routes through the REAL
 *  requestModify, the component that owns the /api/orders/modify fetch. */
function Harness({ order, book }: { order: OpenOrder; book: PortfolioData }) {
  const { requestModify } = useOrderActions();
  return (
    <ModifyOrderModal
      order={order}
      loading={false}
      portfolio={book}
      onConfirm={(request: ModifyOrderRequest) => {
        void requestModify(order, request);
      }}
      onClose={() => {}}
    />
  );
}

function renderModal(order: OpenOrder, book: PortfolioData): RecordedCall[] {
  const calls = recordFetch();
  render(
    <OrderActionsProvider>
      <Harness order={order} book={book} />
    </OrderActionsProvider>,
  );
  return calls;
}

describe("Gate 3 on a stock close that reduces the breached stack", () => {
  it("renders no Gate 3 banner on the reduce", () => {
    renderModal(tqqqSellOrder(), portfolio(true));
    expect(screen.queryByTestId("correlation-risk-banner")).toBeNull();
  });

  it("does not fire the wire while the gate is closed (nothing changed)", async () => {
    const calls = renderModal(tqqqSellOrder(), portfolio(true));
    const modify = screen.getByRole("button", { name: /Modify Order/i }) as HTMLButtonElement;
    expect(modify.disabled).toBe(true);
    fireEvent.click(modify);
    await Promise.resolve();
    expect(calls).toHaveLength(0);
  });

  it("armed modify on the reduce reaches the wire with the exact payload", async () => {
    const calls = renderModal(tqqqSellOrder(), portfolio(true));

    fireEvent.change(screen.getByLabelText(/New Limit Price/i), { target: { value: "68.50" } });

    const modify = screen.getByRole("button", { name: /Modify Order/i }) as HTMLButtonElement;
    expect(modify.disabled).toBe(false);
    fireEvent.click(modify);
    await vi.waitFor(() => expect(calls).toHaveLength(1));

    expect(calls[0].url).toBe("/api/orders/modify");
    expect(calls[0].method).toBe("POST");
    expect(calls[0].body).toEqual({
      orderId: 41,
      permId: 653640001,
      newPrice: 68.5,
    });
  });

  it("an outsideRth-only change on the reduce also reaches the wire", async () => {
    const calls = renderModal(tqqqSellOrder(), portfolio(true));

    fireEvent.click(screen.getByLabelText(/FILL OUTSIDE RTH/i));

    const modify = screen.getByRole("button", { name: /Modify Order/i }) as HTMLButtonElement;
    expect(modify.disabled).toBe(false);
    fireEvent.click(modify);
    await vi.waitFor(() => expect(calls).toHaveLength(1));

    expect(calls[0].url).toBe("/api/orders/modify");
    expect(calls[0].method).toBe("POST");
    expect(calls[0].body).toEqual({
      orderId: 41,
      permId: 653640001,
      outsideRth: false,
    });
  });
});

describe("Gate 3 on the same ticker as an ADD", () => {
  it("keeps the critical breach banner with the trim-or-hedge copy", () => {
    // No held TQQQ stock: a SELL 10,000 is an opening short, not a close.
    renderModal(tqqqSellOrder(), portfolio(false));

    const banner = screen.getByTestId("correlation-risk-banner");
    expect(banner.getAttribute("data-level")).toBe("critical");
    expect(banner.textContent).toContain("SMH+SPY+TQQQ");
    expect(banner.textContent).toMatch(/adding correlated risk/);
    expect(banner.textContent).not.toContain("\u2014");
  });
});
