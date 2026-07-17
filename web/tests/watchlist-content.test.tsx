/**
 * @vitest-environment jsdom
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { PriceData } from "@/lib/pricesProtocol";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  toggleWatch: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push, prefetch: vi.fn() }),
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
  it("renders no inline detail pane", () => {
    render(
      <WatchlistContent
        prices={{ AAPL: price(212, 210), MSFT: price(498, 500) }}
        portfolio={null}
        orders={null}
      />,
    );

    expect(screen.queryByTestId("watchlist-detail")).toBeNull();
    expect(screen.queryByText(/INLINE DETAIL/i)).toBeNull();
    expect(screen.getByTestId("watchlist-row-AAPL")).toBeTruthy();
    expect(screen.getByTestId("watchlist-row-MSFT")).toBeTruthy();
  });

  it("navigates to the instrument cockpit on row click", () => {
    render(
      <WatchlistContent
        prices={{ AAPL: price(212, 210), MSFT: price(498, 500) }}
        portfolio={null}
        orders={null}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open MSFT instrument cockpit" }));
    expect(mocks.push).toHaveBeenCalledWith("/MSFT");
  });

  it("removes a symbol through the watchlist hook without navigating", async () => {
    render(
      <WatchlistContent
        prices={{ AAPL: price(212, 210), MSFT: price(498, 500) }}
        portfolio={null}
        orders={null}
      />,
    );

    const row = await screen.findByTestId("watchlist-row-AAPL");
    const remove = row.querySelector(".star-toggle") as HTMLButtonElement;
    fireEvent.click(remove);
    expect(mocks.toggleWatch).toHaveBeenCalledWith("AAPL");
    expect(mocks.push).not.toHaveBeenCalled();
  });
});
