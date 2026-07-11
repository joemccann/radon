/**
 * @vitest-environment jsdom
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useOrders } from "../lib/useOrders";
import { usePortfolio } from "../lib/usePortfolio";
import type { OrdersData, PortfolioData } from "../lib/types";

const portfolio: PortfolioData = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-07-10T20:00:00Z",
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
  last_sync: "2026-07-10T20:00:00Z",
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

function response(body: unknown, warning?: string): Response {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (warning) headers.set("X-Sync-Warning", warning);
  return new Response(JSON.stringify(body), { status: 200, headers });
}

function methodOf(call: unknown[]): string {
  const options = (call[1] ?? {}) as RequestInit;
  return (options.method ?? "GET").toUpperCase();
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("cached polling does not amplify IB live sync", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("two active portfolio hooks poll cached GET and never auto-POST", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(portfolio));
    vi.stubGlobal("fetch", fetchMock);

    const first = renderHook(() => usePortfolio(true));
    const second = renderHook(() => usePortfolio(true));
    await flush();

    expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET", "GET"]);

    await act(async () => {
      vi.advanceTimersByTime(30_000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET", "GET", "GET", "GET"]);
    first.unmount();
    second.unmount();
  });

  it("two active orders hooks poll cached GET and never auto-POST", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(orders));
    vi.stubGlobal("fetch", fetchMock);

    const first = renderHook(() => useOrders(true));
    const second = renderHook(() => useOrders(true));
    await flush();

    expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET", "GET"]);

    await act(async () => {
      vi.advanceTimersByTime(30_000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET", "GET", "GET", "GET"]);
    first.unmount();
    second.unmount();
  });

  it("portfolio manual sync keeps a stale fallback visible as degraded", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(portfolio))
      .mockResolvedValueOnce(response(portfolio, "Live IB sync failed; serving latest Turso snapshot"));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => usePortfolio(false));
    await flush();
    act(() => result.current.syncNow());
    await flush();

    expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET", "POST"]);
    expect(result.current.data?.last_sync).toBe(portfolio.last_sync);
    expect(result.current.error).toContain("Live IB sync failed");
  });

  it("portfolio cached GET warning remains visible as degraded", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response(portfolio, "Portfolio snapshot is stale; live sync was not requested"),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => usePortfolio(false));
    await flush();

    expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET"]);
    expect(result.current.error).toContain("snapshot is stale");
  });

  it("orders manual sync keeps a stale fallback visible as degraded", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(orders))
      .mockResolvedValueOnce(response(orders, "IB refresh failed; serving latest Turso snapshot"));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useOrders(false));
    await flush();
    act(() => result.current.syncNow());
    await flush();

    expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET", "POST"]);
    expect(result.current.data?.last_sync).toBe(orders.last_sync);
    expect(result.current.error).toContain("IB refresh failed");
  });

  it("a slow cached portfolio read cannot overwrite a newer manual sync", async () => {
    let resolveCached!: (value: Response) => void;
    const newer = { ...portfolio, last_sync: "2026-07-10T20:01:00Z", bankroll: 110_000 };
    const fetchMock = vi.fn()
      .mockReturnValueOnce(new Promise<Response>((resolve) => { resolveCached = resolve; }))
      .mockResolvedValueOnce(response(newer));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => usePortfolio(false));
    await flush();
    act(() => result.current.syncNow());
    await flush();
    expect(result.current.data?.last_sync).toBe(newer.last_sync);

    resolveCached(response(portfolio));
    await flush();
    expect(result.current.data?.last_sync).toBe(newer.last_sync);
  });

  it("a slow cached orders read cannot overwrite a newer manual sync", async () => {
    let resolveCached!: (value: Response) => void;
    const newer = { ...orders, last_sync: "2026-07-10T20:01:00Z", open_count: 1 };
    const fetchMock = vi.fn()
      .mockReturnValueOnce(new Promise<Response>((resolve) => { resolveCached = resolve; }))
      .mockResolvedValueOnce(response(newer));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useOrders(false));
    await flush();
    act(() => result.current.syncNow());
    await flush();
    expect(result.current.data?.last_sync).toBe(newer.last_sync);

    resolveCached(response(orders));
    await flush();
    expect(result.current.data?.last_sync).toBe(newer.last_sync);
  });

  it("does not re-arm portfolio polling when an in-flight poll settles after unmount", async () => {
    let resolvePoll!: (value: Response) => void;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(portfolio))
      .mockReturnValueOnce(new Promise<Response>((resolve) => { resolvePoll = resolve; }));
    vi.stubGlobal("fetch", fetchMock);

    const hook = renderHook(() => usePortfolio(true));
    await flush();
    await act(async () => {
      vi.advanceTimersByTime(30_000);
      await Promise.resolve();
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);

    hook.unmount();
    resolvePoll(response(portfolio));
    await flush();
    await act(async () => {
      vi.advanceTimersByTime(30_000);
      await Promise.resolve();
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not re-arm orders polling when an in-flight poll settles after unmount", async () => {
    let resolvePoll!: (value: Response) => void;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(orders))
      .mockReturnValueOnce(new Promise<Response>((resolve) => { resolvePoll = resolve; }));
    vi.stubGlobal("fetch", fetchMock);

    const hook = renderHook(() => useOrders(true));
    await flush();
    await act(async () => {
      vi.advanceTimersByTime(30_000);
      await Promise.resolve();
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);

    hook.unmount();
    resolvePoll(response(orders));
    await flush();
    await act(async () => {
      vi.advanceTimersByTime(30_000);
      await Promise.resolve();
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
