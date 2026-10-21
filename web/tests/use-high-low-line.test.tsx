/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useHighLowLine } from "../lib/useHighLowLine";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("private common-stock breadth snapshot polling", () => {
  it("reads without browser caching, waits an hour, and keeps manual refresh GET-only", async () => {
    vi.useFakeTimers();
    const stamp = new Date().toISOString();
    const fetcher = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ scan_time: stamp, missing: false })));
    vi.stubGlobal("fetch", fetcher);
    const { result, unmount } = renderHook(() => useHighLowLine());
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.lastSync).toBe(stamp);
    expect(result.current.loading).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(60 * 60_000 - 1); });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(async () => { result.current.syncNow(); });
    expect(fetcher).toHaveBeenCalledTimes(3);
    for (const [url, init] of fetcher.mock.calls) {
      expect(url).toBe("/api/high-low-line");
      expect(init).toMatchObject({ method: "GET", cache: "no-store" });
    }
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(60 * 60_000); });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("leaves an unavailable sample clock empty", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ scan_time: null, missing: true }))));
    const { result } = renderHook(() => useHighLowLine());
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.lastSync).toBeNull();
    expect(result.current.data).toMatchObject({ missing: true });
  });
  it("retries a denied or failed load only with GET and recovers the sample clock", async () => {
    vi.useFakeTimers();
    const stamp = new Date().toISOString();
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: "Forbidden" }), { status: 403 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ scan_time: stamp, missing: false })));
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(() => useHighLowLine());
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.error).toBeTruthy();
    expect(result.current.lastSync).toBeNull();
    await act(async () => { result.current.syncNow(); });
    expect(result.current.error).toBeNull();
    expect(result.current.lastSync).toBe(stamp);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls.every(([, init]) => init.method === "GET" && init.cache === "no-store")).toBe(true);
  });

});
