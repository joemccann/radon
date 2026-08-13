/** @vitest-environment jsdom */
import { act, renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useColumnVisibility } from "@/lib/useColumnVisibility";
import {
  INDEX_FALLBACK_REFRESH_MS,
  useIndexQuoteFallback,
} from "@/lib/useIndexQuoteFallback";
import { useInformedFlow } from "@/lib/useInformedFlow";
import { usePreviousClose } from "@/lib/usePreviousClose";
import { useTickerFlowReport } from "@/lib/useTickerFlowReport";
import type { PriceData } from "@/lib/pricesProtocol";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function price(symbol: string): PriceData {
  return {
    symbol, last: 100, lastIsCalculated: false, bid: null, ask: null,
    bidSize: null, askSize: null, volume: null, high: null, low: null,
    open: null, close: null, week52High: null, week52Low: null,
    avgVolume: null, delta: null, gamma: null, theta: null, vega: null,
    impliedVol: null, undPrice: null, timestamp: "2026-08-13T15:00:00Z",
  };
}

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("request identity and client cache remediation", () => {
  it("ignores an out-of-order informed-flow response after ticker change", async () => {
    const aapl = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn((url: string) =>
      url.includes("AAPL") ? aapl.promise : Promise.resolve(response({
        ticker: "MSFT", congress_trades: [], insider_trades: [], institutional_summary: null,
      }))));
    const { result, rerender } = renderHook(({ ticker }) => useInformedFlow(ticker), {
      initialProps: { ticker: "AAPL" },
    });
    rerender({ ticker: "MSFT" });
    await waitFor(() => expect(result.current.data?.ticker).toBe("MSFT"));
    aapl.resolve(response({
      ticker: "AAPL", congress_trades: [], insider_trades: [], institutional_summary: null,
    }));
    await act(async () => { await Promise.resolve(); });
    expect(result.current.data?.ticker).toBe("MSFT");
  });

  it("hides the previous flow report immediately on ticker switch", async () => {
    const msft = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn((url: string) => url.includes("MSFT")
      ? msft.promise
      : Promise.resolve(response({ ticker: "AAPL", fetched_at: new Date().toISOString() }))));
    const { result, rerender } = renderHook(({ ticker }) => useTickerFlowReport(ticker), {
      initialProps: { ticker: "AAPL" },
    });
    await waitFor(() => expect(result.current.data?.ticker).toBe("AAPL"));
    rerender({ ticker: "MSFT" });
    await waitFor(() => expect(result.current.status).toBe("loading"));
    expect(result.current.data).toBeNull();
    msft.resolve(response({ ticker: "MSFT", fetched_at: new Date().toISOString() }));
  });

  it("hydrates saved column visibility only after the matching initial render", async () => {
    localStorage.setItem("radon:columns:orders", JSON.stringify({ price: false }));
    function Probe() {
      const { visible } = useColumnVisibility("orders", { symbol: true, price: true });
      return <span>{String(visible.price)}</span>;
    }
    expect(renderToString(<Probe />)).toContain("true");
    const { result } = renderHook(() => useColumnVisibility("orders", { symbol: true, price: true }));
    await waitFor(() => expect(result.current.visible.price).toBe(false));
  });

  it("refreshes index fallback quotes while the missing symbol set is stable", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ price: { ...price("VXN"), last: 30 } }))
      .mockResolvedValueOnce(response({ price: { ...price("VXN"), last: 31 } }));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useIndexQuoteFallback(["VXN"]));
    await act(async () => { await vi.runOnlyPendingTimersAsync(); });
    expect(result.current.VXN?.last).toBe(31);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(INDEX_FALLBACK_REFRESH_MS).toBe(60_000);
  });

  it("expires a stale index fallback when a refresh can no longer prove it", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ price: { ...price("VXN"), last: 30 } }))
      .mockResolvedValueOnce(response({ error: "down" }, 503));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useIndexQuoteFallback(["VXN"]));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(result.current.VXN?.last).toBe(30);
    await act(async () => { await vi.advanceTimersByTimeAsync(INDEX_FALLBACK_REFRESH_MS); });
    expect(result.current.VXN).toBeUndefined();
  });

  it("retries non-OK and partial previous-close responses", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ error: "down" }, 503))
      .mockResolvedValueOnce(response({ closes: {} }))
      .mockResolvedValueOnce(response({ closes: { AAPL: 99 } }));
    vi.stubGlobal("fetch", fetchMock);
    const prices = { AAPL: price("AAPL") };
    const { result, rerender } = renderHook(({ values }) => usePreviousClose(values), {
      initialProps: { values: prices },
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    await act(async () => { await Promise.resolve(); });
    expect(result.current.AAPL?.close).toBe(99);
    rerender({ values: { ...prices } });
  });

  it("invalidates backfilled closes when the expected ET session rolls over", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-13T18:00:00Z"));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ closes: { AAPL: 99 } }))
      .mockResolvedValueOnce(response({ closes: { AAPL: 101 } }));
    vi.stubGlobal("fetch", fetchMock);
    const prices = { AAPL: price("AAPL") };
    const { result, rerender } = renderHook(({ values }) => usePreviousClose(values), {
      initialProps: { values: prices },
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(result.current.AAPL?.close).toBe(99);

    vi.setSystemTime(new Date("2026-08-14T18:00:00Z"));
    rerender({ values: { ...prices } });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.current.AAPL?.close).toBe(101);
  });
});
