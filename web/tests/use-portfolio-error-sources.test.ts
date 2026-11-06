// @vitest-environment jsdom
/**
 * Audit 2026-11-03 §3.5 `usePortfolio`: one `error` string carried four
 * sources (cached GET failure, X-Sync-Warning, manual sync failure, edge
 * pending). Any clean GET called setError(null), so during market hours the
 * 2s snapshot poll erased a failed manual sync's message before the operator
 * could read it, even though the snapshot on screen was the same one the sync
 * failed to replace.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";

import { RouteRefreshContext } from "../lib/RouteRefreshContext";
import { portfolioErrorMessage, portfolioErrorTransition, usePortfolio } from "../lib/usePortfolio";

const base = {
  bankroll: 100_000,
  peak_value: 100_000,
  positions: [],
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
};
const OLD = "2026-08-24T14:00:00.000Z";
const NEW = "2026-08-24T14:05:00.000Z";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
}

describe("usePortfolio keeps a failed manual sync visible", () => {
  const fetchMock = vi.fn();
  let snapshotLastSync = OLD;

  beforeEach(() => {
    snapshotLastSync = OLD;
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") return jsonResponse({ error: "IB gateway unreachable" }, { status: 502, headers: { "content-type": "application/json" } });
      return jsonResponse({ ...base, last_sync: snapshotLastSync });
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("a clean GET of the SAME snapshot does not clear it; a newer snapshot does", async () => {
    let routeKey = "/portfolio";
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(RouteRefreshContext.Provider, { value: routeKey }, children);
    const { result, rerender } = renderHook(() => usePortfolio(true), { wrapper });
    await waitFor(() => expect(result.current.data).not.toBeNull());

    await act(async () => {
      result.current.syncNow();
    });
    await waitFor(() => expect(result.current.error).toBe("IB gateway unreachable"));

    const getsBefore = fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method !== "POST").length;
    routeKey = "/orders";
    rerender();
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method !== "POST").length)
        .toBeGreaterThan(getsBefore));
    await act(async () => { await Promise.resolve(); });
    expect(result.current.error).toBe("IB gateway unreachable");

    // The server timer refreshed the snapshot: the failed manual sync is moot.
    snapshotLastSync = NEW;
    routeKey = "/portfolio";
    rerender();
    await waitFor(() => expect(result.current.lastSync).toBe(NEW));
    expect(result.current.error).toBeNull();
  });
});

describe("portfolioErrorTransition", () => {
  const empty = { read: null, warning: null, sync: null };

  it("keeps each source in its own slot and reports the most actionable", () => {
    let s = portfolioErrorTransition(empty, { type: "read-failed", message: "read down" });
    expect(portfolioErrorMessage(s)).toBe("read down");
    s = portfolioErrorTransition(s, { type: "sync-failed", message: "sync down", snapshotAt: OLD });
    expect(portfolioErrorMessage(s)).toBe("sync down");
    s = portfolioErrorTransition(s, { type: "snapshot", lastSync: OLD, warning: null, fromSync: false });
    expect(s.read).toBeNull();
    expect(portfolioErrorMessage(s)).toBe("sync down");
    s = portfolioErrorTransition(s, { type: "snapshot", lastSync: OLD, warning: "partial", fromSync: true });
    expect(s.sync).toBeNull();
    expect(portfolioErrorMessage(s)).toBe("partial");
  });

  it("an edge-pending sync clears once a newer snapshot lands", () => {
    let s = portfolioErrorTransition(empty, { type: "sync-pending", message: "still running", snapshotAt: OLD });
    s = portfolioErrorTransition(s, { type: "snapshot", lastSync: OLD, warning: null, fromSync: false });
    expect(portfolioErrorMessage(s)).toBe("still running");
    s = portfolioErrorTransition(s, { type: "snapshot", lastSync: NEW, warning: null, fromSync: false });
    expect(portfolioErrorMessage(s)).toBeNull();
  });
});
