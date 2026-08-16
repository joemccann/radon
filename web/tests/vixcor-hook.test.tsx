/**
 * @vitest-environment jsdom
 *
 * useVixcor() — GET-only polling hook over /api/vixcor (useCor mold via
 * useSyncHook). No scan POST exists: radon-vixcor.timer owns refreshes, so
 * every request is a GET with cache: no-store, the poll cadence is hourly
 * (3_600_000 ms), and lastSync is read off the payload's scan_time via
 * extractTimestamp.
 *
 * Spec: docs/indicators/vixcor.md section G.3.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const SCAN_TIME = "2026-08-16T02:35:11Z";

function buildVixcorPayload(overrides: Record<string, unknown> = {}) {
  return {
    scan_time: SCAN_TIME,
    status: "ok",
    as_of: "2026-08-14",
    lag_sessions: 0,
    window: 20,
    count: 5171,
    corr_count: 5152,
    current: {
      date: "2026-08-14",
      vix_close: 14.25,
      cor3m_close: 12.34,
      corr20: 0.014969,
      change_1d: -0.0413,
      percentile: 0.0181,
      regime: "DECOUPLED",
      vix_cov_20d: 0.0512,
      episode: null,
    },
    stats: null,
    episodes: [],
    forward_stats: null,
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

describe("useVixcor", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.useRealTimers();
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("GETs /api/vixcor with cache no-store and exposes the payload", async () => {
    const payload = buildVixcorPayload();
    const fetchSpy = vi.fn(async () => jsonResponse(payload));
    global.fetch = fetchSpy as typeof fetch;
    const { useVixcor } = await import("@/lib/useVixcor");

    const { result } = renderHook(() => useVixcor());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/vixcor",
      expect.objectContaining({ method: "GET", cache: "no-store" }),
    );
    expect(result.current.data?.current?.corr20).toBe(0.014969);
    expect(result.current.error).toBeNull();
  });

  it("never issues a POST (hasPost:false — the timer owns refreshes)", async () => {
    const fetchSpy = vi.fn(async () => jsonResponse(buildVixcorPayload()));
    global.fetch = fetchSpy as typeof fetch;
    const { useVixcor } = await import("@/lib/useVixcor");

    const { result } = renderHook(() => useVixcor());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      result.current.syncNow();
    });
    await waitFor(() => expect(fetchSpy.mock.calls.length).toBeGreaterThan(1));

    for (const [url, init] of fetchSpy.mock.calls) {
      expect(url).toBe("/api/vixcor");
      expect((init as RequestInit | undefined)?.method).toBe("GET");
      expect((init as RequestInit | undefined)?.cache).toBe("no-store");
    }
  });

  it("reads lastSync from the payload's scan_time (extractTimestamp)", async () => {
    const fetchSpy = vi.fn(async () => jsonResponse(buildVixcorPayload()));
    global.fetch = fetchSpy as typeof fetch;
    const { useVixcor } = await import("@/lib/useVixcor");

    const { result } = renderHook(() => useVixcor());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.lastSync).toBe(SCAN_TIME);
  });

  it("polls hourly: nothing at 30 minutes, one more GET at 60", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchSpy = vi.fn(async () => jsonResponse(buildVixcorPayload()));
    global.fetch = fetchSpy as typeof fetch;
    const { useVixcor } = await import("@/lib/useVixcor");

    const { result } = renderHook(() => useVixcor());
    await waitFor(() => expect(result.current.loading).toBe(false));
    const afterMount = fetchSpy.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_800_000);
    });
    expect(fetchSpy.mock.calls.length).toBe(afterMount);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_800_000);
    });
    expect(fetchSpy.mock.calls.length).toBe(afterMount + 1);
  });

  it("surfaces an error when the route fails and nothing is cached", async () => {
    const fetchSpy = vi.fn(async () => jsonResponse({ error: "boom" }, 500));
    global.fetch = fetchSpy as typeof fetch;
    const { useVixcor } = await import("@/lib/useVixcor");

    const { result } = renderHook(() => useVixcor());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.data).toBeNull();
    expect(result.current.error).toBeTruthy();
  });

  it("treats the 200 missing:true contract as data, not an error", async () => {
    const missing = {
      missing: true,
      status: "missing",
      scan_time: null,
      as_of: null,
      count: 0,
      series: [],
      episodes: [],
      current: null,
      stats: null,
      forward_stats: null,
    };
    const fetchSpy = vi.fn(async () => jsonResponse(missing));
    global.fetch = fetchSpy as typeof fetch;
    const { useVixcor } = await import("@/lib/useVixcor");

    const { result } = renderHook(() => useVixcor());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.error).toBeNull();
    expect(result.current.data?.missing).toBe(true);
    expect(result.current.lastSync).toBeNull();
  });
});
