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
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockClear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("auto-refreshes from the live blotter route when active", async () => {
    fetchMock.mockImplementation(async (_input: RequestInfo | URL, init?: RequestInit) => {
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

    const { result } = renderHook(() => useBlotter(true));

    await waitFor(() => {
      expect(result.current.data?.summary.closed_trades).toBe(2);
    });

    // REL-048 / R-106: every request carries an AbortSignal.timeout now — a
    // wedged endpoint used to accumulate one hung request per interval tick
    // until the browser's 6-connection limit blocked the whole tab.
    for (const method of ["GET", "POST"]) {
      const call = fetchMock.mock.calls.find(
        ([url, init]) => url === "/api/blotter" && (init as RequestInit)?.method === method,
      );
      expect(call, `${method} /api/blotter was never issued`).toBeTruthy();
      const init = call![1] as RequestInit;
      expect(init.cache).toBe("no-store");
      expect(init.signal).toBeInstanceOf(AbortSignal);
    }
  });

  it("keeps cached history visible but surfaces the sync error when live refresh fails", async () => {
    fetchMock.mockImplementation(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const method = String(init?.method ?? "GET").toUpperCase();
      if (method === "POST") {
        return new Response(JSON.stringify({ error: "Flex Query request failed: Service account is inactive. (code: 1011)" }), {
          status: 502,
          headers: { "Content-Type": "application/json" },
        });
      }

      return new Response(JSON.stringify(STALE_BLOTTER), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    const { result } = renderHook(() => useBlotter(true));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.data?.summary.closed_trades).toBe(1);
      expect(result.current.error).toContain("Service account is inactive");
    });
  });
});
