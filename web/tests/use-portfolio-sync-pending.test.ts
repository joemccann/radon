// @vitest-environment jsdom
/**
 * The sync POST used to wait 42s in the browser behind a 30s edge, so it
 * could only ever see Caddy's raw 504 and rendered it as a red "Sync failed".
 * The route now answers inside the edge with the Turso snapshot and
 * X-Sync-Pending when the IB sync is still running server-side; the hook
 * re-reads the snapshot soon instead of reporting a failure.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

import { BROWSER_PRODUCER_SYNC_TIMEOUT_MS } from "../lib/edgeBudget";
import { SYNC_PENDING_REPOLL_MS, usePortfolio } from "../lib/usePortfolio";

const portfolio = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-10-08T14:00:00.000Z",
  positions: [],
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
};

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

describe("usePortfolio sync POST under the edge budget", () => {
  it("aborts the sync POST before Caddy's 30s edge bound", async () => {
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout");
    fetchMock.mockImplementation(async () => jsonResponse(portfolio));
    const { result } = renderHook(() => usePortfolio(true));
    await waitFor(() => expect(result.current.data).not.toBeNull());

    await act(async () => {
      result.current.syncNow();
    });
    await waitFor(() => expect(result.current.syncing).toBe(false));

    expect(calls()).toContainEqual({ url: "/api/portfolio", method: "POST" });
    expect(timeoutSpy).toHaveBeenCalledWith(BROWSER_PRODUCER_SYNC_TIMEOUT_MS);
    expect(BROWSER_PRODUCER_SYNC_TIMEOUT_MS).toBeLessThan(30_000);
  });

  it("re-reads the snapshot soon after the route reports the sync still running", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return jsonResponse(portfolio, {
          headers: {
            "content-type": "application/json",
            "X-Sync-Warning": "IB sync still running - serving latest Turso snapshot",
            "X-Sync-Pending": "1",
          },
        });
      }
      return jsonResponse(portfolio);
    });
    const { result } = renderHook(() => usePortfolio(true));
    await waitFor(() => expect(result.current.data).not.toBeNull());

    await act(async () => {
      result.current.syncNow();
    });
    await waitFor(() => expect(result.current.syncing).toBe(false));
    const getsAfterPost = () => calls().slice(calls().findIndex((c) => c.method === "POST") + 1)
      .filter((c) => c.method === "GET" && c.url === "/api/portfolio");
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
      return jsonResponse(portfolio);
    });
    const { result } = renderHook(() => usePortfolio(true));
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
