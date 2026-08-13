/** @vitest-environment jsdom */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PortfolioData } from "@/lib/types";

const mocks = vi.hoisted(() => ({
  futuresState: null as unknown,
  indexHook: vi.fn(),
}));

vi.mock("@/lib/TickerDetailContext", () => ({ useTickerDetailOptional: () => null }));
vi.mock("@/lib/useFuturesChain", () => ({
  useFuturesChain: () => mocks.futuresState,
}));
vi.mock("@/lib/useIndexOptionsChain", () => ({
  useIndexOptionsChain: (symbol: string, expiry: string | null) => mocks.indexHook(symbol, expiry),
}));

import { FuturesOrderForm } from "../components/ticker-detail/FuturesOrderForm";
import { IndexOptionOrderForm } from "../components/ticker-detail/IndexOptionOrderForm";

afterEach(() => {
  cleanup();
  mocks.indexHook.mockReset();
});

const future = {
  conId: 9001,
  symbol: "VIX",
  localSymbol: "VIXU6",
  exchange: "CFE",
  currency: "USD",
  lastTradeDateOrContractMonth: "20260916",
  multiplier: "1000",
  tradingClass: "VX",
  marketName: "VIX",
  minTick: 0.05,
};

function resolvedPortfolio(): PortfolioData {
  return { positions: [] } as unknown as PortfolioData;
}

describe("listed-contract order safety", () => {
  it("futures submit blocks pending coverage and a stale cross-ticker contract", async () => {
    mocks.futuresState = {
      data: { symbol: "VIX", exchange: "CFE", contracts: [future], count: 1 },
      loading: false,
      error: null,
    };
    const { rerender } = render(<FuturesOrderForm ticker="VIX" />);
    fireEvent.change(screen.getByPlaceholderText("0.00"), { target: { value: "20" } });
    await waitFor(() => expect((screen.getByRole("button", { name: /BUY VIXU6/ }) as HTMLButtonElement).disabled).toBe(true));

    rerender(<FuturesOrderForm ticker="ES" portfolio={resolvedPortfolio()} />);
    await waitFor(() => expect((screen.getByRole("button", { name: /BUY ES/ }) as HTMLButtonElement).disabled).toBe(true));
  });

  it("futures risk uses exact conId holdings for a sell-to-close", async () => {
    mocks.futuresState = {
      data: { symbol: "VIX", exchange: "CFE", contracts: [future], count: 1 },
      loading: false,
      error: null,
    };
    const portfolio = {
      positions: [{
        id: 1,
        ticker: "VIX",
        structure: "VIX Future",
        structure_type: "Future",
        risk_profile: "linear",
        expiry: "2026-09-16",
        contracts: 2,
        direction: "LONG",
        entry_cost: 38_000,
        max_risk: null,
        market_value: 40_000,
        legs: [{
          con_id: 9001,
          direction: "LONG",
          contracts: 2,
          type: "Stock",
          strike: null,
          entry_cost: 38_000,
          avg_cost: 19_000,
          market_price: 20,
          market_value: 40_000,
        }],
        kelly_optimal: null,
        target: null,
        stop: null,
        entry_date: "2026-08-01",
      }],
    } as unknown as PortfolioData;

    render(<FuturesOrderForm ticker="VIX" portfolio={portfolio} />);
    fireEvent.click(screen.getByRole("button", { name: "SELL" }));
    fireEvent.change(screen.getByPlaceholderText("0.00"), { target: { value: "20" } });

    await waitFor(() => expect(document.body.textContent).toMatch(/Est\. Realized P&L/i));
    expect((screen.getByRole("button", { name: /SELL VIXU6/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("index options reject an out-of-order chain belonging to the prior ticker", async () => {
    const contract = {
      conId: 42,
      symbol: "VIX",
      localSymbol: "VIX  260916C00020000",
      exchange: "CBOE",
      currency: "USD",
      lastTradeDateOrContractMonth: "20260916",
      strike: 20,
      right: "C" as const,
      multiplier: "100",
      tradingClass: "VIX",
      minTick: 0.05,
    };
    mocks.indexHook.mockImplementation((_symbol: string, expiry: string | null) => ({
      data: expiry == null
        ? { symbol: "VIX", exchange: "CBOE", tradingClass: "VIX", expirations: ["20260916"], contracts: [], count: 0 }
        : { symbol: "VIX", exchange: "CBOE", tradingClass: "VIX", expirations: ["20260916"], contracts: [contract], count: 1 },
      loading: false,
      error: null,
    }));

    const { rerender } = render(<IndexOptionOrderForm ticker="VIX" portfolio={resolvedPortfolio()} />);
    await waitFor(() => expect(screen.getByText("$20 C")).toBeTruthy());
    fireEvent.change(screen.getAllByRole("combobox")[1], { target: { value: "42" } });
    fireEvent.change(screen.getByPlaceholderText("0.00"), { target: { value: "1" } });
    await waitFor(() => expect((screen.getByRole("button", { name: /BUY VIX/ }) as HTMLButtonElement).disabled).toBe(false));

    rerender(<IndexOptionOrderForm ticker="SPX" portfolio={resolvedPortfolio()} />);
    await waitFor(() => expect((screen.getByRole("button", { name: /BUY SPX/ }) as HTMLButtonElement).disabled).toBe(true));
  });
});
