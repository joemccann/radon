/**
 * @vitest-environment jsdom
 */
import React from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import FillsModal from "@/components/FillsModal";
import SystemStatusBar from "@/components/admin/SystemStatusBar";
import MobileJournalList from "@/components/mobile/MobileJournalList";
import NewsTab from "@/components/ticker-detail/NewsTab";
import SeasonalityTab from "@/components/ticker-detail/SeasonalityTab";
import type { ExecutedOrder, TradeEntry } from "@/lib/types";

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function response(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ticker-scoped detail requests", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("ignores an old news response after the ticker changes", async () => {
    const aapl = deferred<Response>();
    const msft = deferred<Response>();
    vi.mocked(fetch).mockImplementation((input) =>
      String(input).includes("AAPL") ? aapl.promise : msft.promise,
    );

    const { rerender } = render(<NewsTab ticker="AAPL" active />);
    rerender(<NewsTab ticker="MSFT" active />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));

    await act(async () => {
      msft.resolve(response({ data: [{ headline: "MSFT current", source: "uw", created_at: "2026-08-13" }] }));
      await msft.promise;
    });
    expect(await screen.findByText("MSFT current")).toBeTruthy();

    await act(async () => {
      aapl.resolve(response({ data: [{ headline: "AAPL stale", source: "uw", created_at: "2026-08-13" }] }));
      await aapl.promise;
    });
    expect(screen.queryByText("AAPL stale")).toBeNull();
    expect(screen.getByText("MSFT current")).toBeTruthy();
  });

  it("keeps seasonality ticker-scoped and excludes unobserved months from ratings", async () => {
    const aapl = deferred<Response>();
    const msft = deferred<Response>();
    vi.mocked(fetch).mockImplementation((input) =>
      String(input).includes("AAPL") ? aapl.promise : msft.promise,
    );
    const observed = {
      month: 1,
      avg_change: 0.1,
      median_change: 0.08,
      max_change: 0.2,
      min_change: -0.05,
      positive_closes: 8,
      positive_months_perc: 0.8,
      years: 10,
    };

    const { rerender } = render(<SeasonalityTab ticker="AAPL" active />);
    rerender(<SeasonalityTab ticker="MSFT" active />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));

    await act(async () => {
      msft.resolve(response({ data: [observed], source: "uw" }));
      await msft.promise;
    });
    expect((await screen.findAllByText("+10.0%")).length).toBeGreaterThan(0);
    expect(screen.getByText("1 favorable")).toBeTruthy();
    expect(screen.getByText("0 unfavorable")).toBeTruthy();
    expect(screen.getByText("NEUTRAL")).toBeTruthy();

    await act(async () => {
      aapl.resolve(response({ data: [{ ...observed, avg_change: -0.1 }], source: "uw" }));
      await aapl.promise;
    });
    expect(screen.queryByText("-10.0%")).toBeNull();
    expect(screen.getAllByText("+10.0%").length).toBeGreaterThan(0);
  });
});

describe("component data semantics", () => {
  it("reports missing service freshness as unknown", () => {
    render(<SystemStatusBar units={[]} health={null} updatedSecsAgo={null} stalled={false} />);
    expect(screen.getByTestId("system-status-bar").textContent).toContain("updated freshness unknown");
    expect(screen.getByTestId("system-status-bar").textContent).not.toContain("just now");
  });

  it("uses canonical total cost in the mobile journal", () => {
    const trade = {
      id: "trade-1",
      ticker: "AAPL",
      structure: "Long Call",
      decision: "OPEN",
      date: "2026-08-13",
      total_cost: 250,
      entry_cost: 100,
    } as unknown as TradeEntry;
    render(<MobileJournalList trades={[trade]} />);
    expect(screen.getByText("$250.00")).toBeTruthy();
    expect(screen.queryByText("$100.00")).toBeNull();
  });

  it("renders fill execution time in America/New_York", () => {
    const fill = {
      execId: "fill-1",
      time: "2026-01-15T15:30:45Z",
      symbol: "AAPL",
      side: "BOT",
      quantity: 1,
      avgPrice: 100,
      commission: 1,
      realizedPNL: 10,
      exchange: "SMART",
      contract: { conId: 1, symbol: "AAPL", secType: "STK", strike: null, right: null, expiry: null },
    } as ExecutedOrder;
    render(<FillsModal open fills={[fill]} totalRealizedPnl={10} onClose={() => {}} />);
    expect(screen.getByText("10:30:45")).toBeTruthy();
  });
});
