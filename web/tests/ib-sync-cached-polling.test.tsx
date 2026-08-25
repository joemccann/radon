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

function rateLimitedResponse(retryAfterSeconds: number): Response {
  return new Response(JSON.stringify({ error: "Too Many Requests" }), {
    status: 429,
    headers: {
      "Content-Type": "application/json",
      "Retry-After": String(retryAfterSeconds),
    },
  });
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

  it("paints a server-seeded portfolio synchronously and starts no-store polling after 30 seconds", async () => {
    const refreshed = { ...portfolio, bankroll: 101_000 };
    const fetchMock = vi.fn().mockResolvedValue(response(refreshed));
    vi.stubGlobal("fetch", fetchMock);

    const hook = renderHook(() => usePortfolio(true, {
      initialSnapshot: {
        data: portfolio,
        warning: "Turso read failed; serving last in-memory portfolio snapshot",
      },
    }));

    expect(hook.result.current.loading).toBe(false);
    expect(hook.result.current.data).toEqual(portfolio);
    expect(hook.result.current.error).toContain("Turso read failed");
    expect(fetchMock).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(29_999);
      await Promise.resolve();
    });
    expect(fetchMock).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(1);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/portfolio",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(hook.result.current.data?.bankroll).toBe(101_000);
    hook.unmount();
  });

  it("requests entry-date enrichment only when explicitly enabled", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(portfolio));
    vi.stubGlobal("fetch", fetchMock);

    const hook = renderHook(() => usePortfolio(false, { includeEntryDates: true }));
    await flush();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/portfolio?include=entry-dates",
      expect.objectContaining({ cache: "no-store" }),
    );
    hook.unmount();
  });

  it("starts an enriched read immediately and ignores a pending base response on endpoint change", async () => {
    let resolveBase!: (value: Response) => void;
    const baseResponse = new Promise<Response>((resolve) => {
      resolveBase = resolve;
    });
    const enriched = {
      ...portfolio,
      bankroll: 102_000,
      trade_log_dates: { AAPL: "2026-08-25" },
      contract_open_dates: { "AAPL|20260918|C|250": "2026-08-25" },
    };
    const fetchMock = vi.fn((url: string) => url.includes("include=entry-dates")
      ? Promise.resolve(response(enriched))
      : baseResponse);
    vi.stubGlobal("fetch", fetchMock);

    const hook = renderHook(
      ({ includeEntryDates }: { includeEntryDates: boolean }) =>
        usePortfolio(false, { includeEntryDates }),
      { initialProps: { includeEntryDates: false } },
    );
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/portfolio");

    hook.rerender({ includeEntryDates: true });
    await flush();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/portfolio?include=entry-dates");
    expect(hook.result.current.data?.bankroll).toBe(102_000);
    expect(hook.result.current.data?.trade_log_dates).toEqual({ AAPL: "2026-08-25" });

    resolveBase(response({ ...portfolio, bankroll: 99_000 }));
    await flush();

    expect(hook.result.current.data?.bankroll).toBe(102_000);
    expect(hook.result.current.data?.contract_open_dates).toEqual({
      "AAPL|20260918|C|250": "2026-08-25",
    });
    hook.unmount();
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

  it("clears a transient portfolio warning after a clean poll of the same snapshot", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(portfolio, "Turso read failed; serving last in-memory portfolio snapshot"))
      .mockResolvedValueOnce(response(portfolio));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => usePortfolio(true));
    await flush();
    expect(result.current.error).toContain("Turso read failed");

    await act(async () => {
      vi.advanceTimersByTime(30_000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.data?.last_sync).toBe(portfolio.last_sync);
    expect(result.current.error).toBeNull();
  });

  it("clears a transient orders warning after a clean poll of the same snapshot", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(orders, "Turso read failed; serving cached orders"))
      .mockResolvedValueOnce(response(orders));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useOrders(true));
    await flush();
    expect(result.current.error).toContain("Turso read failed");

    await act(async () => {
      vi.advanceTimersByTime(30_000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.data?.last_sync).toBe(orders.last_sync);
    expect(result.current.error).toBeNull();
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

  it("honors Retry-After before polling portfolio again after a 429", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(rateLimitedResponse(60))
      .mockResolvedValueOnce(response(portfolio));
    vi.stubGlobal("fetch", fetchMock);

    const hook = renderHook(() => usePortfolio(true));
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(59_999);
      await Promise.resolve();
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(1);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    hook.unmount();
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
