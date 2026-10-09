// @vitest-environment jsdom
/**
 * REL-323 / R-734: POST /api/orders now answers inside the edge with the Turso
 * snapshot and X-Sync-Pending when IB refresh is still running server-side.
 * usePortfolio re-reads soon; useOrders used to paint the warning as a stuck
 * error and wait for the 30s poll, so cancel/modify saw the pre-refresh book.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

import { BROWSER_PRODUCER_SYNC_TIMEOUT_MS } from "../lib/edgeBudget";
import { SYNC_PENDING_REPOLL_MS } from "../lib/usePortfolio";
import { useOrders } from "../lib/useOrders";

const orders = { orders: [], last_sync: "2026-10-08T14:00:00.000Z" };

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
}

const fetchMock = vi.fn();

function calls(): Array<{ url: string; method: string }> {
  return fetchMock.mock.calls.map(([url, init]) => ({
    url: String(url),
    method: (init as RequestInit | undefined)?.method ?? "GET",
  }));
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  fetchMock.mockReset();
});

describe("useOrders sync POST pending refresh", () => {
  it("aborts the refresh POST before Caddy's 30s edge bound", async () => {
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout");
    fetchMock.mockImplementation(async () => jsonResponse(orders));
    const { result } = renderHook(() => useOrders(true));
    await waitFor(() => expect(result.current.data).not.toBeNull());

    await act(async () => {
      result.current.syncNow();
    });
    await waitFor(() => expect(result.current.syncing).toBe(false));

    expect(calls()).toContainEqual({ url: "/api/orders", method: "POST" });
    expect(timeoutSpy).toHaveBeenCalledWith(BROWSER_PRODUCER_SYNC_TIMEOUT_MS);
    expect(BROWSER_PRODUCER_SYNC_TIMEOUT_MS).toBeLessThan(30_000);
  });

  it("re-reads the snapshot soon after the route reports the sync still running", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return jsonResponse(orders, {
          headers: {
            "content-type": "application/json",
            "X-Sync-Warning": "IB sync still running - serving latest Turso snapshot",
            "X-Sync-Pending": "1",
          },
        });
      }
      return jsonResponse(orders);
    });
    const { result } = renderHook(() => useOrders(true));
    await waitFor(() => expect(result.current.data).not.toBeNull());

    await act(async () => {
      result.current.syncNow();
    });
    await waitFor(() => expect(result.current.syncing).toBe(false));
    const getsAfterPost = () => calls().slice(calls().findIndex((c) => c.method === "POST") + 1)
      .filter((c) => c.method === "GET" && c.url === "/api/orders");
    expect(getsAfterPost()).toHaveLength(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SYNC_PENDING_REPOLL_MS);
    });

    await waitFor(() => expect(getsAfterPost().length).toBeGreaterThanOrEqual(1));
    expect(SYNC_PENDING_REPOLL_MS).toBeLessThan(30_000);
  });

  it("treats an opaque edge 504 as a pending sync and re-reads the snapshot", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return new Response("<html>504 Gateway Timeout</html>", { status: 504, headers: { "content-type": "text/html" } });
      }
      return jsonResponse(orders);
    });
    const { result } = renderHook(() => useOrders(true));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    const getsBefore = calls().filter((c) => c.method === "GET").length;

    await act(async () => {
      result.current.syncNow();
    });
    await waitFor(() => expect(result.current.syncing).toBe(false));

    expect(result.current.error).not.toBe("Sync failed");
    expect(result.current.error).toBe("IB sync still running - showing latest snapshot");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SYNC_PENDING_REPOLL_MS);
    });
    await waitFor(() => expect(calls().filter((c) => c.method === "GET").length).toBeGreaterThan(getsBefore));
  });
});
