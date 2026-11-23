/**
 * @vitest-environment jsdom
 *
 * useIvRank() — GET-only polling hook over /api/ivrank (useSyncHook).
 * No scan POST exists: radon-ivrank.timer owns refreshes every 5 minutes
 * in ET trading hours, so every request is a GET with cache: no-store
 * and the poll cadence is 300_000 ms. T-596.
 *
 * Spec: docs/indicators/ivrank.md.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const SCAN_TIME = "2026-08-26T22:10:11Z";

function buildIvRankPayload(overrides: Record<string, unknown> = {}) {
  return {
    scan_time: SCAN_TIME,
    status: "ok",
    source: "ib",
    as_of: "2026-08-26",
    count: 1,
    current: { date: "2026-08-26", iv: 0.122, iv_rank: 10.6, regime: "SUPPRESSED" },
    uw_check: null,
    stats: null,
    series: [],
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("useIvRank", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.useRealTimers();
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("GETs /api/ivrank with cache no-store and exposes the payload", async () => {
    const payload = buildIvRankPayload();
    const fetchSpy = vi.fn(async () => jsonResponse(payload));
    global.fetch = fetchSpy as typeof fetch;
    const { useIvRank } = await import("@/lib/useIvRank");

    const { result } = renderHook(() => useIvRank());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/ivrank",
      expect.objectContaining({ method: "GET", cache: "no-store" }),
    );
    expect(result.current.data?.current?.iv_rank).toBe(10.6);
    expect(result.current.error).toBeNull();
  });

  it("polls on the intraday writer cadence: nothing at 2.5 minutes, one more GET at 5", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchSpy = vi.fn(async () => jsonResponse(buildIvRankPayload()));
    global.fetch = fetchSpy as typeof fetch;
    const { useIvRank } = await import("@/lib/useIvRank");

    const { result } = renderHook(() => useIvRank());
    await waitFor(() => expect(result.current.loading).toBe(false));
    const afterMount = fetchSpy.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(150_000);
    });
    expect(fetchSpy.mock.calls.length).toBe(afterMount);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(150_000);
    });
    expect(fetchSpy.mock.calls.length).toBe(afterMount + 1);
  });
});
