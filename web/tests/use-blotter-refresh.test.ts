/**
 * TDD: useBlotter should promote cached history to a live Flex Query refresh
 * when the blotter is active on /orders.
 *
 * Bug: the orders page only read `/api/blotter` once, so the Historical Trades
 * section could stay pinned to the cached snapshot until the user manually hit
 * Refresh. The hook now auto-syncs from the live POST route when active.
 */

/**
 * @vitest-environment jsdom
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useBlotter } from "../lib/useBlotter";

const STALE_BLOTTER = {
  as_of: "2026-03-18T16:00:00Z",
  summary: { closed_trades: 1, open_trades: 0, total_commissions: 2.6, realized_pnl: 340 },
  closed_trades: [{ symbol: "AAPL" }],
  open_trades: [],
};

const FRESH_BLOTTER = {
  as_of: "2026-03-19T16:10:00Z",
  summary: { closed_trades: 2, open_trades: 0, total_commissions: 5.2, realized_pnl: 725 },
  closed_trades: [{ symbol: "AAPL" }, { symbol: "GOOG" }],
  open_trades: [],
};

describe("useBlotter", () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const method = String(init?.method ?? "GET").toUpperCase();
    if (method === "POST") {
      return new Response(JSON.stringify(FRESH_BLOTTER), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }

    return new Response(JSON.stringify(STALE_BLOTTER), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockClear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("auto-refreshes from the live blotter route when active", async () => {
    const { result } = renderHook(() => useBlotter(true));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.data?.summary.closed_trades).toBe(2);
    });

    expect(fetchMock).toHaveBeenCalledWith("/api/blotter");
    expect(fetchMock).toHaveBeenCalledWith("/api/blotter", { method: "POST" });
  });
});
