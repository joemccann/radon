// @vitest-environment jsdom
/**
 * A 429 on POST /api/portfolio means a sibling tab or device already spent
 * this minute's `portfolio-sync` budget — the snapshot it produced arrives on
 * the next GET poll. Until 2026-08-24 the hook threw the body's "Too Many
 * Requests" into `error`, which WorkspaceShell rendered as "Live data
 * degraded" (963 times that day). It is coalescence, not degradation.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";

import { RouteRefreshContext } from "../lib/RouteRefreshContext";
import { usePortfolio } from "../lib/usePortfolio";

const portfolio = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-08-24T14:00:00.000Z",
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

describe("usePortfolio sync POST rate-limited", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return jsonResponse({ error: "Too Many Requests" }, { status: 429, headers: { "Retry-After": "37" } });
      }
      return jsonResponse(portfolio);
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("keeps the snapshot and reports no error when the sync POST is 429", async () => {
    const { result } = renderHook(() => usePortfolio(true));
    await waitFor(() => expect(result.current.data).not.toBeNull());

    await act(async () => {
      result.current.syncNow();
    });
    await waitFor(() => expect(result.current.syncing).toBe(false));

    expect(result.current.error).toBeNull();
    expect(result.current.data?.last_sync).toBe(portfolio.last_sync);
    expect(fetchMock.mock.calls.filter(([, init]) => (init as RequestInit)?.method === "POST")).toHaveLength(1);
  });

  it("still surfaces a genuine sync failure", async () => {
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") return jsonResponse({ error: "IB gateway unreachable" }, { status: 502 });
      return jsonResponse(portfolio);
    });
    const { result } = renderHook(() => usePortfolio(true));
    await waitFor(() => expect(result.current.data).not.toBeNull());

    await act(async () => {
      result.current.syncNow();
    });

    await waitFor(() => expect(result.current.error).toBe("IB gateway unreachable"));
  });
});

describe("usePortfolio cached GET rate-limited", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("keeps the snapshot on screen without an error when a later GET is 429", async () => {
    let gets = 0;
    fetchMock.mockImplementation(async () => {
      gets += 1;
      if (gets === 1) return jsonResponse(portfolio);
      return jsonResponse({ error: "Too Many Requests" }, { status: 429, headers: { "Retry-After": "12" } });
    });
    let routeKey = "/portfolio";
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(RouteRefreshContext.Provider, { value: routeKey }, children);
    const { result, rerender } = renderHook(() => usePortfolio(true), { wrapper });
    await waitFor(() => expect(result.current.data).not.toBeNull());

    // A client navigation re-reads the snapshot immediately.
    routeKey = "/orders";
    rerender();
    await waitFor(() => expect(gets).toBeGreaterThanOrEqual(2));

    expect(result.current.error).toBeNull();
    expect(result.current.data?.last_sync).toBe(portfolio.last_sync);
  });

  it("still reports a 429 on a cold tab with nothing to show", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse({ error: "Too Many Requests" }, { status: 429, headers: { "Retry-After": "12" } }),
    );
    const { result } = renderHook(() => usePortfolio(true));

    await waitFor(() => expect(result.current.error).toBe("Failed to fetch portfolio (429)"));
    expect(result.current.data).toBeNull();
  });
});
