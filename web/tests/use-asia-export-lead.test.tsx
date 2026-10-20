/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useAsiaExportLead } from "../lib/useAsiaExportLead";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("official export snapshot polling", () => {
  it("reads without browser caching, waits an hour, and keeps manual refresh GET-only", async () => {
    vi.useFakeTimers();
    const stamp = new Date().toISOString();
    const fetcher = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ scan_time: stamp, missing: false })));
    vi.stubGlobal("fetch", fetcher);
    const { result, unmount } = renderHook(() => useAsiaExportLead());
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
      expect(url).toBe("/api/asia-export-lead");
      expect(init).toMatchObject({ method: "GET", cache: "no-store" });
    }
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(60 * 60_000); });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("leaves an unavailable sample clock empty", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ scan_time: null, missing: true }))));
    const { result } = renderHook(() => useAsiaExportLead());
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.lastSync).toBeNull();
    expect(result.current.data).toMatchObject({ missing: true });
  });
});
