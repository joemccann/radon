// @vitest-environment jsdom

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { PriceData } from "@/lib/pricesProtocol";

const context = vi.hoisted(() => ({
  getPrices: vi.fn(),
  getFundamentals: () => ({}),
  getDepths: () => ({}),
  getTape: () => ({}),
  portfolio: null,
  orders: null,
  setDepthSymbols: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), back: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams("deck=c"),
}));

vi.mock("@/lib/TickerDetailContext", () => ({
  useTickerDetail: () => context,
  useTickerDetailOptional: () => context,
}));

vi.mock("../components/TickerDetailContent", () => ({
  default: ({ prices }: { prices: Record<string, PriceData> }) => (
    <output data-testid="ticker-consumer-price">{prices.MU?.last ?? "unavailable"}</output>
  ),
}));

const { default: WorkspaceSections } = await import("../components/WorkspaceSections");
const { default: TickerWorkspace } = await import("../components/TickerWorkspace");

function prices(last: number): Record<string, PriceData> {
  return { MU: {
    symbol: "MU", last, bid: last - 0.05, ask: last + 0.05, close: 119,
    lastIsCalculated: false, bidSize: 100, askSize: 100, volume: 1000,
    high: null, low: null, open: null, week52High: null, week52Low: null,
    avgVolume: null, delta: null, gamma: null, theta: null, vega: null,
    impliedVol: null, undPrice: null, timestamp: "2026-09-15T18:00:00Z",
  } };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ticker live quote propagation", () => {
  it("delivers a single final tick through memoized sections without waiting for the context ref", () => {
    const previous = prices(120);
    context.getPrices.mockReturnValue(previous);
    const getter = context.getPrices;
    const view = render(<WorkspaceSections section="ticker-detail" tickerParam="MU" prices={previous} />);
    expect(screen.getByTestId("ticker-consumer-price").textContent).toBe("120");

    view.rerender(<WorkspaceSections section="ticker-detail" tickerParam="MU" prices={prices(127)} />);
    expect(screen.getByTestId("ticker-consumer-price").textContent).toBe("127");
    expect(context.getPrices).toBe(getter);
    expect(context.getPrices).not.toHaveBeenCalled();
  });

  it("retains context fallback for callers without reactive prices", () => {
    context.getPrices.mockReturnValue(prices(120));
    render(<TickerWorkspace ticker="MU" theme="dark" />);
    expect(screen.getByTestId("ticker-consumer-price").textContent).toBe("120");
    expect(context.getPrices).toHaveBeenCalled();
  });

  it("does not resurrect a stale context quote when the reactive snapshot is empty", () => {
    context.getPrices.mockReturnValue(prices(120));
    render(<WorkspaceSections section="ticker-detail" tickerParam="MU" prices={{}} />);
    expect(screen.getByTestId("ticker-consumer-price").textContent).toBe("unavailable");
    expect(context.getPrices).not.toHaveBeenCalled();
  });
});
