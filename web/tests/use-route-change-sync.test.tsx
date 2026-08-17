/**
 * @vitest-environment jsdom
 *
 * In-app navigation must re-read cached snapshots immediately. The shell
 * often stays mounted (same client component, pathname change), so the
 * mount-only GET/POST never re-runs and the UI sits on the last poll.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { RouteRefreshContext } from "../lib/RouteRefreshContext";
import { useOrders } from "../lib/useOrders";
import { usePortfolio } from "../lib/usePortfolio";
import { useSyncHook } from "../lib/useSyncHook";
import type { OrdersData, PortfolioData } from "../lib/types";

const portfolio: PortfolioData = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-08-17T14:00:00Z",
  positions: [],
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
};

const orders: OrdersData = {
  last_sync: "2026-08-17T14:00:00Z",
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function methodOf(call: unknown[]): string {
  const options = (call[1] ?? {}) as RequestInit;
  return (options.method ?? "GET").toUpperCase();
}

function wrapperFor(routeKey: { current: string }) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <RouteRefreshContext.Provider value={routeKey.current}>
        {children}
      </RouteRefreshContext.Provider>
    );
  };
}

describe("route-change fresh sync", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("re-GETs portfolio immediately when the pathname changes", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(portfolio));
    vi.stubGlobal("fetch", fetchMock);
    const routeKey = { current: "/dashboard" };

    const { result, rerender } = renderHook(() => usePortfolio(true), {
      wrapper: wrapperFor(routeKey),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET"]);

    routeKey.current = "/portfolio";
    rerender();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET", "GET"]);
  });

  it("re-GETs orders immediately when the pathname changes", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(orders));
    vi.stubGlobal("fetch", fetchMock);
    const routeKey = { current: "/dashboard" };

    const { result, rerender } = renderHook(() => useOrders(true), {
      wrapper: wrapperFor(routeKey),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET"]);

    routeKey.current = "/orders";
    rerender();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(methodOf(fetchMock.mock.calls[1])).toBe("GET");
  });

  it("re-GETs a GET-only sync hook on pathname change without waiting for the interval", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ scan_time: "2026-08-17T14:00:00Z", value: 1 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const routeKey = { current: "/regime/cri" };

    const { result, rerender } = renderHook(
      () =>
        useSyncHook<{ scan_time: string; value: number }>(
          {
            endpoint: "/api/vixcor",
            hasPost: false,
            interval: 5 * 60_000,
            extractTimestamp: (data) => data.scan_time,
          },
          true,
        ),
      { wrapper: wrapperFor(routeKey) },
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.data?.value).toBe(1);
    expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET"]);

    routeKey.current = "/regime/vixcor";
    rerender();

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET", "GET"]);
  });

  it("re-POSTs a producer hook when it becomes active again after the first sync", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ scan_time: "2026-08-17T14:00:00Z", value: 1 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result, rerender } = renderHook(
      ({ active }: { active: boolean }) =>
        useSyncHook<{ scan_time: string; value: number }>(
          {
            endpoint: "/api/scanner",
            extractTimestamp: (data) => data.scan_time,
            interval: 0,
          },
          active,
        ),
      { initialProps: { active: true } },
    );

    await waitFor(() => expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET", "POST"]));
    expect(result.current.data?.value).toBe(1);

    rerender({ active: false });
    rerender({ active: true });

    await waitFor(() => expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET", "POST", "POST"]));
  });
});
