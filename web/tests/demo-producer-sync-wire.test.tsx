// @vitest-environment jsdom
/**
 * POST /api/portfolio, POST /api/orders and POST /api/journal/sync are
 * operatorOnly. On demo.radon.run every manual "Sync" (the header stale-pill,
 * the Journal SYNC IB button) fired them anyway, so a demo user's click went
 * 403 and then burned the producer tier into 429. Demo sync must re-read the
 * sample snapshot with a GET, matching useSyncHook's demo contract; the
 * operator path must still POST the exact producer URL.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";

import { usePortfolio } from "../lib/usePortfolio";
import { useOrders } from "../lib/useOrders";
import { useJournal } from "../lib/useJournal";

const portfolio = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-10-16T14:00:00.000Z",
  positions: [],
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
};
const orders = { last_sync: "2026-10-16T14:00:00.000Z", open_orders: [], executed_orders: [] };

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

const fetchMock = vi.fn();
const calls = () => fetchMock.mock.calls.map(([url, init]) => ({
  url: String(url),
  method: ((init as RequestInit | undefined)?.method ?? "GET").toUpperCase(),
}));
const posts = () => calls().filter((c) => c.method === "POST");

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string) => {
    if (url.startsWith("/api/orders")) return jsonResponse(orders);
    if (url === "/api/journal/sync") return jsonResponse({ imported: 0, skipped: 0 });
    if (url.startsWith("/api/journal")) return jsonResponse({ trades: [] });
    return jsonResponse(portfolio);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("usePortfolio manual sync", () => {
  it("demo: re-reads the snapshot with a GET and never POSTs the producer", async () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
    const { result } = renderHook(() => usePortfolio(true));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    const before = calls().length;

    await act(async () => {
      result.current.syncNow();
    });
    await waitFor(() => expect(calls().length).toBe(before + 1));

    expect(posts()).toHaveLength(0);
    expect(calls().at(-1)).toEqual({ url: "/api/portfolio", method: "GET" });
  });

  it("operator: POSTs exactly /api/portfolio", async () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "");
    const { result } = renderHook(() => usePortfolio(true));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(posts()).toHaveLength(0);

    await act(async () => {
      result.current.syncNow();
    });
    await waitFor(() => expect(result.current.syncing).toBe(false));

    expect(posts()).toEqual([{ url: "/api/portfolio", method: "POST" }]);
  });
});

describe("useOrders manual sync", () => {
  it("demo: re-reads the snapshot with a GET and never POSTs the producer", async () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
    const { result } = renderHook(() => useOrders(true));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    const before = calls().length;

    await act(async () => {
      result.current.syncNow();
    });
    await waitFor(() => expect(calls().length).toBe(before + 1));

    expect(posts()).toHaveLength(0);
    expect(calls().at(-1)).toEqual({ url: "/api/orders", method: "GET" });
  });

  it("operator: POSTs exactly /api/orders", async () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "");
    const { result } = renderHook(() => useOrders(true));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(posts()).toHaveLength(0);

    await act(async () => {
      result.current.syncNow();
    });
    await waitFor(() => expect(result.current.syncing).toBe(false));

    expect(posts()).toEqual([{ url: "/api/orders", method: "POST" }]);
  });
});

describe("useJournal syncWithIB", () => {
  it("demo: never POSTs /api/journal/sync", async () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
    const { result } = renderHook(() => useJournal(true));
    await waitFor(() => expect(result.current.data).not.toBeNull());

    await act(async () => {
      await result.current.syncWithIB();
    });

    expect(posts()).toHaveLength(0);
    expect(calls().some((c) => c.url === "/api/journal/sync")).toBe(false);
  });

  it("operator: POSTs exactly /api/journal/sync", async () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "");
    const { result } = renderHook(() => useJournal(true));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(calls().some((c) => c.url === "/api/journal/sync")).toBe(false);

    await act(async () => {
      await result.current.syncWithIB();
    });

    expect(posts()[0]).toEqual({ url: "/api/journal/sync", method: "POST" });
  });
});
