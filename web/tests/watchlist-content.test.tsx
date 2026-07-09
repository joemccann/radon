/**
 * @vitest-environment jsdom
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { PriceData } from "@/lib/pricesProtocol";

const mocks = vi.hoisted(() => ({
  setActiveTicker: vi.fn(),
  setActivePositionId: vi.fn(),
  setDepthSymbol: vi.fn(),
  toggleWatch: vi.fn(),
}));

vi.mock("@/lib/TickerDetailContext", () => ({
  useTickerDetail: () => ({
    getPrices: () => ({}),
    getFundamentals: () => ({}),
    getDepths: () => ({}),
    getTape: () => ({}),
    setActiveTicker: mocks.setActiveTicker,
    setActivePositionId: mocks.setActivePositionId,
    setDepthSymbol: mocks.setDepthSymbol,
  }),
}));

vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({
    watchlist: [
      { id: "w1", symbol: "AAPL", sector: "Technology", added_at: "2026-07-01T12:00:00Z" },
      { id: "w2", symbol: "MSFT", sector: "Technology", added_at: "2026-07-02T12:00:00Z" },
    ],
    isLoading: false,
    isWatched: () => true,
    toggleWatch: mocks.toggleWatch,
  }),
}));

vi.mock("@/components/TickerDetailContent", () => ({
  default: (props: { ticker: string }) => (
    <div data-testid={`ticker-detail-${props.ticker}`}>{props.ticker} detail</div>
  ),
}));

import WatchlistContent from "@/components/watchlist/WatchlistContent";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

function price(last: number, close: number): PriceData {
  return { last, close } as PriceData;
}

describe("WatchlistContent", () => {
  it("selects watched symbols inline without navigation", async () => {
    const pushState = vi.spyOn(window.history, "pushState");
    const replaceState = vi.spyOn(window.history, "replaceState");

    render(
      <WatchlistContent
        prices={{ AAPL: price(212, 210), MSFT: price(498, 500) }}
        portfolio={null}
        orders={null}
        theme="dark"
      />,
    );

    expect(await screen.findByTestId("ticker-detail-AAPL")).toBeTruthy();
    expect(screen.queryByTestId("ticker-detail-MSFT")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Show MSFT watchlist detail" }));

    expect(await screen.findByTestId("ticker-detail-MSFT")).toBeTruthy();
    expect(screen.queryByTestId("ticker-detail-AAPL")).toBeNull();
    expect(mocks.setActiveTicker).toHaveBeenLastCalledWith("MSFT");
    expect(pushState).not.toHaveBeenCalled();
    expect(replaceState).not.toHaveBeenCalled();
  });

  it("removes a symbol through the watchlist hook", async () => {
    render(
      <WatchlistContent
        prices={{ AAPL: price(212, 210), MSFT: price(498, 500) }}
        portfolio={null}
        orders={null}
        theme="dark"
      />,
    );

    const row = await screen.findByTestId("watchlist-row-AAPL");
    const remove = row.querySelector(".star-toggle") as HTMLButtonElement;
    fireEvent.click(remove);
    expect(mocks.toggleWatch).toHaveBeenCalledWith("AAPL");
  });
});
