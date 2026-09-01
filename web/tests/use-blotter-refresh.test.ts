/**
 * TDD: useBlotter is GET-only. Live fills come from journal_sync.
 * A POST would SendRequest and extend Flex 1025.
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

  it("polls GET /api/blotter when active and never POSTs", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(FRESH_BLOTTER), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { result } = renderHook(() => useBlotter(true));

    await waitFor(() => {
      expect(result.current.data?.summary.closed_trades).toBe(2);
    });

    const posts = fetchMock.mock.calls.filter(
      ([, init]) => String((init as RequestInit | undefined)?.method ?? "GET").toUpperCase() === "POST",
    );
    expect(posts).toHaveLength(0);
    const get = fetchMock.mock.calls.find(
      ([url, init]) => url === "/api/blotter" && String((init as RequestInit)?.method ?? "GET").toUpperCase() === "GET",
    );
    expect(get).toBeTruthy();
    const init = get![1] as RequestInit;
    expect(init.cache).toBe("no-store");
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("keeps GET history when the blotter is active", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(STALE_BLOTTER), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { result } = renderHook(() => useBlotter(true));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.data?.summary.closed_trades).toBe(1);
    });
    expect(result.current.error).toBeNull();
  });
});
