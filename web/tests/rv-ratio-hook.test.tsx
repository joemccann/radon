/**
 * @vitest-environment jsdom
 *
 * useRvRatio(symbol) — bespoke per-symbol fetch hook (useOptionsExposure
 * mold) with the synchronous-scan swap:
 *
 *   - GET on mount / symbol change, cache no-store, abort prior in-flight
 *   - missing or stale snapshot -> scanning:true, ONE POST; the POST
 *     response IS the fresh payload (no polling loop exists)
 *   - stale-but-present data keeps rendering underneath the scan; scan
 *     errors are suppressed while data is displayed
 *   - no repeat POST while a scan is in flight (refresh() is guarded)
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildMissingRvRatioPayload,
  buildPartialRvRatioFixture,
} from "./rv-ratio-fixture";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Deferred = {
  promise: Promise<Response>;
  resolve: (response: Response) => void;
  reject: (reason: unknown) => void;
};

function deferred(): Deferred {
  let resolve!: (response: Response) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<Response>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useRvRatio", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("serves a current snapshot with a single GET and never POSTs", async () => {
    const current = { ...buildPartialRvRatioFixture(), stale: false };
    const fetchSpy = vi.fn(async () => jsonResponse(current));
    global.fetch = fetchSpy as typeof fetch;
    const { useRvRatio } = await import("@/lib/useRvRatio");

    const { result } = renderHook(() => useRvRatio("smh"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/options/rv-ratio?symbol=SMH",
      expect.objectContaining({ cache: "no-store", signal: expect.any(AbortSignal) }),
    );
    expect(result.current.data?.symbol).toBe("SMH");
    expect(result.current.scanning).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("keeps stale data rendering underneath one POST, then swaps in the fresh payload", async () => {
    const stale = { ...buildPartialRvRatioFixture({ endSessionsAgo: 5 }), stale: true };
    const fresh = { ...buildPartialRvRatioFixture(), stale: false };
    const scan = deferred();
    const fetchSpy = vi.fn((_url: string, init?: RequestInit) =>
      init?.method === "POST" ? scan.promise : Promise.resolve(jsonResponse(stale)),
    );
    global.fetch = fetchSpy as typeof fetch;
    const { useRvRatio } = await import("@/lib/useRvRatio");

    const { result } = renderHook(() => useRvRatio("SMH"));
    await waitFor(() => expect(result.current.scanning).toBe(true));

    // Stale snapshot stays on screen while the scan runs.
    expect(result.current.data?.coverage.last_date).toBe(stale.coverage.last_date);
    expect(result.current.loading).toBe(false);

    await act(async () => scan.resolve(jsonResponse(fresh)));
    await waitFor(() => expect(result.current.scanning).toBe(false));

    expect(result.current.data?.coverage.last_date).toBe(fresh.coverage.last_date);
    const postCalls = fetchSpy.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(postCalls).toHaveLength(1);
    expect(postCalls[0][0]).toBe("/api/options/rv-ratio?symbol=SMH");
    expect(postCalls[0][1]).toMatchObject({ cache: "no-store" });
  });

  it("auto-scans a missing symbol: data null while scanning, payload lands on completion", async () => {
    const fresh = { ...buildPartialRvRatioFixture(), stale: false };
    const scan = deferred();
    const fetchSpy = vi.fn((_url: string, init?: RequestInit) =>
      init?.method === "POST"
        ? scan.promise
        : Promise.resolve(jsonResponse(buildMissingRvRatioPayload("SMH"))),
    );
    global.fetch = fetchSpy as typeof fetch;
    const { useRvRatio } = await import("@/lib/useRvRatio");

    const { result } = renderHook(() => useRvRatio("SMH"));
    await waitFor(() => expect(result.current.scanning).toBe(true));

    expect(result.current.data).toBeNull();
    expect(result.current.error).toBeNull();

    await act(async () => scan.resolve(jsonResponse(fresh)));
    await waitFor(() => expect(result.current.data?.symbol).toBe("SMH"));
    expect(result.current.scanning).toBe(false);
  });

  it("never fires a second POST while a scan is in flight", async () => {
    const stale = { ...buildPartialRvRatioFixture({ endSessionsAgo: 5 }), stale: true };
    const scan = deferred(); // never resolves during the test
    const fetchSpy = vi.fn((_url: string, init?: RequestInit) =>
      init?.method === "POST" ? scan.promise : Promise.resolve(jsonResponse(stale)),
    );
    global.fetch = fetchSpy as typeof fetch;
    const { useRvRatio } = await import("@/lib/useRvRatio");

    const { result } = renderHook(() => useRvRatio("SMH"));
    await waitFor(() => expect(result.current.scanning).toBe(true));

    await act(async () => {
      void result.current.refresh();
    });

    const postCalls = fetchSpy.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(postCalls).toHaveLength(1);
  });

  it("aborts the in-flight request on symbol change and unmount", async () => {
    const signals: AbortSignal[] = [];
    global.fetch = vi.fn((_url: string, init?: RequestInit) => {
      signals.push(init?.signal as AbortSignal);
      return new Promise<Response>(() => undefined);
    }) as typeof fetch;
    const { useRvRatio } = await import("@/lib/useRvRatio");

    const { rerender, unmount } = renderHook(
      ({ symbol }) => useRvRatio(symbol),
      { initialProps: { symbol: "SMH" } },
    );
    rerender({ symbol: "MU" });
    expect(signals[0].aborted).toBe(true);
    unmount();
    expect(signals[1].aborted).toBe(true);
  });

  it("aborts an in-flight scan POST on unmount", async () => {
    const stale = { ...buildPartialRvRatioFixture({ endSessionsAgo: 5 }), stale: true };
    const postSignals: AbortSignal[] = [];
    global.fetch = vi.fn((_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        postSignals.push(init.signal as AbortSignal);
        return new Promise<Response>(() => undefined);
      }
      return Promise.resolve(jsonResponse(stale));
    }) as typeof fetch;
    const { useRvRatio } = await import("@/lib/useRvRatio");

    const { result, unmount } = renderHook(() => useRvRatio("SMH"));
    await waitFor(() => expect(result.current.scanning).toBe(true));

    unmount();
    expect(postSignals[0].aborted).toBe(true);
  });

  it("surfaces a sanitized error when the GET fails with nothing displayed", async () => {
    global.fetch = vi.fn(async () =>
      new Response("secret provider traceback", { status: 502 }),
    ) as typeof fetch;
    const { useRvRatio } = await import("@/lib/useRvRatio");

    const { result } = renderHook(() => useRvRatio("SMH"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.data).toBeNull();
    expect(result.current.error).toBe("RV ratio is temporarily unavailable (HTTP 502)");
    expect(result.current.error).not.toContain("traceback");
  });

  it("suppresses scan errors while stale data is displayed", async () => {
    const stale = { ...buildPartialRvRatioFixture({ endSessionsAgo: 5 }), stale: true };
    const fetchSpy = vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "POST"
        ? new Response("upstream scan blew up", { status: 504 })
        : jsonResponse(stale),
    );
    global.fetch = fetchSpy as typeof fetch;
    const { useRvRatio } = await import("@/lib/useRvRatio");

    const { result } = renderHook(() => useRvRatio("SMH"));
    const postCalls = () => fetchSpy.mock.calls.filter(([, init]) => init?.method === "POST");
    await waitFor(() => expect(postCalls()).toHaveLength(1));
    await waitFor(() => expect(result.current.scanning).toBe(false));

    expect(result.current.data?.coverage.last_date).toBe(stale.coverage.last_date);
    expect(result.current.error).toBeNull();
  });

  it("surfaces the scan error when a missing symbol's scan fails (nothing displayed)", async () => {
    const fetchSpy = vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "POST"
        ? new Response("boom", { status: 503 })
        : jsonResponse(buildMissingRvRatioPayload("SMH")),
    );
    global.fetch = fetchSpy as typeof fetch;
    const { useRvRatio } = await import("@/lib/useRvRatio");

    const { result } = renderHook(() => useRvRatio("SMH"));
    const postCalls = () => fetchSpy.mock.calls.filter(([, init]) => init?.method === "POST");
    await waitFor(() => expect(postCalls()).toHaveLength(1));
    await waitFor(() => expect(result.current.error).toBe("RV ratio scan failed (HTTP 503)"));

    expect(result.current.data).toBeNull();
    expect(result.current.scanning).toBe(false);
  });
});
