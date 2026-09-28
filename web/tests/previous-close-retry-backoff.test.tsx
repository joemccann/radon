/** @vitest-environment jsdom */
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePreviousClose } from "@/lib/usePreviousClose";
import type { PriceData } from "@/lib/pricesProtocol";

function response(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function price(symbol: string): PriceData {
  return {
    symbol, last: 100, lastIsCalculated: false, bid: null, ask: null,
    bidSize: null, askSize: null, volume: null, high: null, low: null,
    open: null, close: null, week52High: null, week52Low: null,
    avgVolume: null, delta: null, gamma: null, theta: null, vega: null,
    impliedVol: null, undPrice: null, timestamp: "2026-09-28T15:00:00Z",
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("usePreviousClose retry pacing", () => {
  it("backs off and stops retrying a symbol no source can serve", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-28T15:00:00Z"));
    const fetchMock = vi.fn(() => Promise.resolve(response({ closes: {} })));
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => usePreviousClose({ ZZZZ: price("ZZZZ") }));
    for (let second = 0; second < 600; second += 1) {
      await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    }
    // The route allows 20 requests a minute; a 1 s fixed retry sent ~600 here.
    expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(6);
    for (const [url, init] of fetchMock.mock.calls as unknown as [string, RequestInit][]) {
      expect(url).toBe("/api/previous-close");
      expect(init.method).toBe("POST");
      expect(JSON.parse(String(init.body))).toEqual({ symbols: ["ZZZZ"] });
    }
  });

  it("waits out Retry-After on a 429 before asking again", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-28T15:00:00Z"));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ error: "rate limited" }, 429, { "Retry-After": "30" }))
      .mockResolvedValueOnce(response({ closes: { AAPL: 99 } }));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => usePreviousClose({ AAPL: price("AAPL") }));
    await act(async () => { await vi.advanceTimersByTimeAsync(29_000); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.current.AAPL?.close).toBe(99);
  });
});
