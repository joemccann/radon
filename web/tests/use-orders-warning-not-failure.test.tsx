/**
 * @vitest-environment jsdom
 *
 * Audit 2026-11-03 §3.5: the poll loop read its failure signal from the same
 * ref that holds the visible `error` text, which also carries sync warnings
 * (X-Sync-Warning) and the edge-pending notice. A 200 snapshot with a warning
 * counted as a failed poll, so the RTH 2s cadence fell to 60s, then 5 min.
 * A warning stays visible in `error`; it is not a failure.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useOrders } from "@/lib/useOrders";

const RTH_INTERVAL_MS = 2_000;
const SNAPSHOT = { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0, last_sync: "2026-11-03T15:00:00Z" };

let getTimes: number[] = [];

function snapshot(headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(SNAPSHOT), {
    status: 200,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  getTimes = [];
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useOrders: a warning is not a failure", () => {
  it("keeps the 2s cadence while every snapshot carries a sync warning", async () => {
    global.fetch = vi.fn(async (_url: unknown, init?: RequestInit) => {
      if (!init?.method || init.method === "GET") getTimes.push(Date.now());
      return snapshot({ "X-Sync-Warning": "IB sync failed - showing latest snapshot" });
    }) as unknown as typeof fetch;

    const { result, unmount } = renderHook(() => useOrders(true, RTH_INTERVAL_MS));
    await advance(0);
    getTimes = [];

    await advance(10 * RTH_INTERVAL_MS);

    expect(result.current.error).toMatch(/showing latest snapshot/);
    expect(getTimes.length).toBeGreaterThanOrEqual(9);
    unmount();
  });

  it("a poll superseded by an edge-pending sync is not counted as a failure", async () => {
    // GETs take 500ms; the POST comes back as Caddy's empty 504 at once. The
    // poll in flight across the sync returns early (stale generation) with the
    // edge-pending notice in `error`, which used to read as a failed poll.
    global.fetch = vi.fn((_url: unknown, init?: RequestInit) => {
      if (init?.method === "POST") {
        return Promise.resolve(new Response("", { status: 504, headers: { "Content-Type": "text/html" } }));
      }
      getTimes.push(Date.now());
      return new Promise<Response>((resolve) => setTimeout(() => resolve(snapshot()), 500));
    }) as unknown as typeof fetch;

    const { result, unmount } = renderHook(() => useOrders(true, RTH_INTERVAL_MS));
    await advance(600);
    await advance(RTH_INTERVAL_MS - 600 + 100); // next poll's GET is now in flight
    await act(async () => {
      result.current.syncNow();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.error).toMatch(/IB sync still running/);
    getTimes = [];

    await advance(20_000);

    // Clean polls resume within the sync-pending repoll, not the 60s rung.
    expect(getTimes.length).toBeGreaterThanOrEqual(2);
    unmount();
  });

  it("a real failure still backs off", async () => {
    global.fetch = vi.fn(async () => {
      getTimes.push(Date.now());
      return new Response(null, { status: 503 });
    }) as unknown as typeof fetch;

    const { unmount } = renderHook(() => useOrders(true, RTH_INTERVAL_MS));
    await advance(0);
    getTimes = [];

    await advance(60_000);

    expect(getTimes.length).toBeLessThanOrEqual(2);
    unmount();
  });
});
