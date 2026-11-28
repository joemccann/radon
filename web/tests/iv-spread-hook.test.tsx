/**
 * @vitest-environment jsdom
 *
 * useIvSpread() — GET-only polling hook over /api/iv-spread (useSyncHook).
 * No scan POST exists: radon-iv-spread.timer owns refreshes every 5 minutes
 * in ET trading hours, so every request is a GET with cache: no-store
 * and the poll cadence is 300_000 ms. T-600.
 *
 * Spec: docs/indicators/iv-spread.md.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const SCAN_TIME = "2026-08-26T15:00:30Z";

function buildIvSpreadPayload() {
  return {
    scan_time: SCAN_TIME,
    status: "ok",
    source: "ib",
    as_of: "2026-08-26",
    count: 1,
    current: {
      date: "2026-08-26",
      spx_iv: 0.121,
      ndx_iv: 0.176,
      spread: 5.48,
      z_score: 0.1,
      pctile: 59,
      change_1d: 0.36,
      regime: "NORMAL",
    },
    stats: null,
    excluded: [],
    series: [],
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("useIvSpread", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.useRealTimers();
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("GETs /api/iv-spread with cache no-store and exposes the payload", async () => {
    const payload = buildIvSpreadPayload();
    const fetchSpy = vi.fn(async () => jsonResponse(payload));
    global.fetch = fetchSpy as typeof fetch;
    const { useIvSpread } = await import("@/lib/useIvSpread");

    const { result } = renderHook(() => useIvSpread());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/iv-spread",
      expect.objectContaining({ method: "GET", cache: "no-store" }),
    );
    expect(result.current.data?.current?.spread).toBe(5.48);
    expect(result.current.error).toBeNull();
  });

  it("polls on the intraday writer cadence: nothing at 2.5 minutes, one more GET at 5", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchSpy = vi.fn(async () => jsonResponse(buildIvSpreadPayload()));
    global.fetch = fetchSpy as typeof fetch;
    const { useIvSpread } = await import("@/lib/useIvSpread");

    const { result } = renderHook(() => useIvSpread());
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
