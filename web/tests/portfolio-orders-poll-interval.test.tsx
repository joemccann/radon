/** @vitest-environment jsdom */

import { createElement, type ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useOrders } from "../lib/useOrders";
import { usePortfolio } from "../lib/usePortfolio";
import { RouteRefreshContext } from "../lib/RouteRefreshContext";
import { SYNC_PENDING_REPOLL_MS } from "../lib/edgeBudget";

const snapshot = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-10-13T14:00:00Z",
  positions: [],
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

function response(status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(snapshot), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

async function advance(ms: number): Promise<void> {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

let visibility: DocumentVisibilityState;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-13T14:00:00Z"));
  visibility = "visible";
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
  fetchMock = vi.fn(async () => response());
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const hooks = [
  { name: "portfolio", useSnapshot: (active: boolean, pollIntervalMs?: number) => usePortfolio(active, { pollIntervalMs }) },
  { name: "orders", useSnapshot: (active: boolean, pollIntervalMs?: number) => useOrders(active, pollIntervalMs) },
];

describe.each(hooks)("$name cached polling cadence", ({ useSnapshot }) => {
  it("polls healthy snapshots every two seconds without automatic broker POSTs", async () => {
    renderHook(() => useSnapshot(true, 2_000));
    await advance(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await advance(1_999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await advance(2_000);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls.every((call) => {
      const options = call[1] as RequestInit | undefined;
      return options?.method === undefined && options?.cache === "no-store";
    })).toBe(true);
  });

  it("retains thirty seconds when the interval is omitted", async () => {
    renderHook(() => useSnapshot(true));
    await advance(0);
    await advance(29_999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("reschedules between thirty seconds and two seconds without duplicate timers", async () => {
    const { rerender } = renderHook(({ interval }) => useSnapshot(true, interval), {
      initialProps: { interval: 30_000 },
    });
    await advance(0);
    await advance(5_000);
    rerender({ interval: 2_000 });
    await advance(1_999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    rerender({ interval: 30_000 });
    await advance(29_999);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await advance(1);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("loads an inactive hook once and stops periodic polling after deactivation", async () => {
    const { rerender } = renderHook(({ active }) => useSnapshot(active, 2_000), {
      initialProps: { active: false },
    });
    await advance(0);
    await advance(60_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    rerender({ active: true });
    await advance(500);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    rerender({ active: false });
    await advance(60_000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("deduplicates a read still in flight across faster polling ticks", async () => {
    let resolveRead!: (value: Response) => void;
    fetchMock.mockImplementation(() => new Promise<Response>((resolve) => { resolveRead = resolve; }));
    renderHook(() => useSnapshot(true, 2_000));
    await advance(6_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { resolveRead(response()); });
    await advance(2_000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("refreshes immediately on navigation even during an ordinary failure retry", async () => {
    let routeKey = "/portfolio";
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(RouteRefreshContext.Provider, { value: routeKey }, children);
    const { rerender } = renderHook(() => useSnapshot(true, 2_000), { wrapper });
    await advance(0);
    fetchMock.mockImplementation(async () => response(503));
    await advance(2_000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    fetchMock.mockImplementation(async () => response());
    routeKey = "/orders";
    rerender();
    await advance(0);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("preserves the pending manual sync repoll delay", async () => {
    const { result } = renderHook(() => useSnapshot(true, 2_000));
    await advance(0);
    fetchMock.mockImplementation(async (_url: string, options?: RequestInit) =>
      response(200, options?.method === "POST" ? { "X-Sync-Pending": "1" } : {}),
    );
    await act(async () => { result.current.syncNow(); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await advance(SYNC_PENDING_REPOLL_MS - 1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await advance(1);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    await advance(2_000);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });
});

it("orders still skips hidden-tab polls with the faster interval", async () => {
  renderHook(() => useOrders(true, 2_000));
  await advance(0);
  visibility = "hidden";
  await advance(10_000);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  visibility = "visible";
  await advance(2_000);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("orders keeps the thirty-second failure baseline and resumes fast polling on recovery", async () => {
  renderHook(() => useOrders(true, 2_000));
  await advance(0);
  fetchMock.mockImplementation(async () => response(503));
  await advance(2_000);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  await advance(59_999);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  await advance(1);
  expect(fetchMock).toHaveBeenCalledTimes(3);
  fetchMock.mockImplementation(async () => response());
  await advance(119_999);
  expect(fetchMock).toHaveBeenCalledTimes(3);
  await advance(1);
  expect(fetchMock).toHaveBeenCalledTimes(4);
  await advance(2_000);
  expect(fetchMock).toHaveBeenCalledTimes(5);
});

it("orders preserves failure backoff when the market interval changes", async () => {
  const { rerender } = renderHook(({ interval }) => useOrders(true, interval), {
    initialProps: { interval: 30_000 },
  });
  await advance(0);
  fetchMock.mockImplementation(async () => response(503));
  await advance(30_000);
  rerender({ interval: 2_000 });
  await advance(59_999);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  await advance(1);
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

it("portfolio retains thirty-second retries after a failed fast read", async () => {
  renderHook(() => usePortfolio(true, { pollIntervalMs: 2_000 }));
  await advance(0);
  fetchMock.mockImplementation(async () => response(503));
  await advance(2_000);
  await advance(29_999);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  fetchMock.mockImplementation(async () => response());
  await advance(1);
  expect(fetchMock).toHaveBeenCalledTimes(3);
  await advance(2_000);
  expect(fetchMock).toHaveBeenCalledTimes(4);
});

it.each([undefined, "1", "60"])("portfolio honors Retry-After %s across interval changes", async (retryAfter) => {
  const { rerender, result } = renderHook(({ interval }) => usePortfolio(true, { pollIntervalMs: interval }), {
    initialProps: { interval: 30_000 },
  });
  await advance(0);
  fetchMock.mockImplementation(async () => response(429, retryAfter ? { "Retry-After": retryAfter } : {}));
  await advance(30_000);
  expect(result.current.error).toBeNull();
  rerender({ interval: 2_000 });
  const retryMs = retryAfter === "60" ? 60_000 : 30_000;
  await advance(retryMs - 1);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  fetchMock.mockImplementation(async () => response());
  await advance(1);
  expect(fetchMock).toHaveBeenCalledTimes(3);
  await advance(2_000);
  expect(fetchMock).toHaveBeenCalledTimes(4);
});

it("portfolio preserves failed-read retries when its interval changes", async () => {
  const { rerender } = renderHook(({ interval }) => usePortfolio(true, { pollIntervalMs: interval }), {
    initialProps: { interval: 30_000 },
  });
  await advance(0);
  fetchMock.mockImplementation(async () => response(503));
  await advance(30_000);
  rerender({ interval: 2_000 });
  await advance(29_999);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  await advance(1);
  expect(fetchMock).toHaveBeenCalledTimes(3);
});
