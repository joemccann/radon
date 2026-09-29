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
  it("honors Retry-After while live price updates rerender the hook", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-28T15:00:00Z"));
    let resolveRequest!: (value: Response) => void;
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveRequest = resolve; }))
      .mockResolvedValue(response({ closes: { AAPL: 99 } }));
    vi.stubGlobal("fetch", fetchMock);
    const { result, rerender } = renderHook(
      ({ last }) => usePreviousClose({ AAPL: { ...price("AAPL"), last } }),
      { initialProps: { last: 100 } },
    );
    rerender({ last: 101 });
    await act(async () => {
      resolveRequest(response({ error: "rate limited" }, 429, { "Retry-After": "30" }));
    });
    for (let second = 1; second < 30; second += 1) {
      await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
      rerender({ last: 101 + second });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    }
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.current.AAPL.close).toBe(99);
  });

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
    expect(fetchMock).toHaveBeenCalledTimes(5);
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

  it.each(["missing", "http", "network"])("preserves exponential backoff during price updates after a %s failure", async (failure) => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-28T15:00:00Z"));
    const fetchMock = vi.fn(() => failure === "network"
      ? Promise.reject(new Error("offline"))
      : Promise.resolve(response({ closes: {} }, failure === "http" ? 503 : 200)));
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = renderHook(({ last }) => usePreviousClose({ AAPL: { ...price("AAPL"), last } }), {
      initialProps: { last: 100 },
    });
    await act(async () => {});
    for (const [index, delay] of [1_000, 2_000, 4_000, 8_000].entries()) {
      await act(async () => { await vi.advanceTimersByTimeAsync(delay - 1); });
      rerender({ last: 101 + index });
      expect(fetchMock).toHaveBeenCalledTimes(index + 1);
      await act(async () => { await vi.advanceTimersByTimeAsync(1); });
      expect(fetchMock).toHaveBeenCalledTimes(index + 2);
    }
    await act(async () => { await vi.advanceTimersByTimeAsync(600_000); });
    rerender({ last: 110 });
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it("keeps one symbol's cooldown when another symbol starts and finishes a request", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-28T15:00:00Z"));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({}, 429, { "Retry-After": "30" }))
      .mockResolvedValueOnce(response({ closes: { MSFT: 98 } }))
      .mockResolvedValueOnce(response({ closes: { AAPL: 99 } }));
    vi.stubGlobal("fetch", fetchMock);
    const initial: Record<string, PriceData> = { AAPL: price("AAPL") };
    const { result, rerender } = renderHook(({ prices }) => usePreviousClose(prices), { initialProps: { prices: initial } });
    await act(async () => {});
    await act(async () => { rerender({ prices: { ...initial, MSFT: price("MSFT") } }); });
    expect(result.current.MSFT.close).toBe(98);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ symbols: ["MSFT"] });
    await act(async () => { await vi.advanceTimersByTimeAsync(29_999); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(result.current.AAPL.close).toBe(99);
    expect(result.current.MSFT.close).toBe(98);
  });

  it.each(["not-a-number", "0", "-1"])("uses the bounded default for Retry-After %s without spending a symbol attempt", async (header) => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-28T15:00:00Z"));
    const fetchMock = vi.fn().mockResolvedValue(response({}, 429, { "Retry-After": header }));
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => usePreviousClose({ AAPL: price("AAPL") }));
    await act(async () => {});
    for (let retry = 1; retry <= 6; retry += 1) {
      await act(async () => { await vi.advanceTimersByTimeAsync(59_999); });
      expect(fetchMock).toHaveBeenCalledTimes(retry);
      await act(async () => { await vi.advanceTimersByTimeAsync(1); });
      expect(fetchMock).toHaveBeenCalledTimes(retry + 1);
    }
  });

  it.each([false, true])("cleans up pending work on unmount (response already received: %s)", async (received) => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-28T15:00:00Z"));
    let resolveRequest!: (value: Response) => void;
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { resolveRequest = resolve; }));
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = renderHook(() => usePreviousClose({ AAPL: price("AAPL") }));
    if (received) await act(async () => { resolveRequest(response({ closes: {} })); });
    unmount();
    if (!received) await act(async () => { resolveRequest(response({}, 503)); });
    expect(vi.getTimerCount()).toBe(0);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("ignores a prior session's late response", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-28T15:00:00Z"));
    let resolveOld!: (value: Response) => void;
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce(response({ closes: { AAPL: 102 } }));
    vi.stubGlobal("fetch", fetchMock);
    const prices = { AAPL: price("AAPL") };
    const { result, rerender } = renderHook(() => usePreviousClose(prices));
    vi.setSystemTime(new Date("2026-09-29T15:00:00Z"));
    await act(async () => { rerender(); });
    expect(result.current.AAPL.close).toBe(102);
    await act(async () => { resolveOld(response({ closes: { AAPL: 99 } })); });
    expect(result.current.AAPL.close).toBe(102);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("retries only the missing part of a successful response after its cooldown", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-28T15:00:00Z"));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ closes: { AAPL: 0, MSFT: 98 } }))
      .mockResolvedValueOnce(response({ closes: { AAPL: 99 } }));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => usePreviousClose({ AAPL: price("AAPL"), MSFT: price("MSFT") }));
    await act(async () => {});
    expect(result.current.MSFT.close).toBe(98);
    expect(result.current.AAPL.close).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ symbols: ["AAPL"] });
    expect(result.current.AAPL.close).toBe(99);
  });

  it("preserves a live close that arrives after the backfill", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-28T15:00:00Z"));
    const fetchMock = vi.fn().mockResolvedValue(response({ closes: { AAPL: 99 } }));
    vi.stubGlobal("fetch", fetchMock);
    const initial = { ...price("AAPL"), close: 0 };
    const { result, rerender } = renderHook(({ value }) => usePreviousClose({ AAPL: value }), { initialProps: { value: initial } });
    await act(async () => {});
    expect(result.current.AAPL.close).toBe(99);
    const live = { ...initial, close: 101 };
    rerender({ value: live });
    expect(result.current.AAPL).toBe(live);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

it("REL-295 keeps Retry-After across price renders while a response is pending", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-29T15:00:00Z"));
  let release!: (value: Response) => void;
  const fetchMock = vi.fn()
    .mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve; }))
    .mockResolvedValue(response({ closes: { AAPL: 99 } }));
  vi.stubGlobal("fetch", fetchMock);
  const { result, rerender } = renderHook(({ last }) => usePreviousClose({
    AAPL: { ...price("AAPL"), last },
  }), { initialProps: { last: 100 } });
  rerender({ last: 101 });
  await act(async () => { release(response({}, 429, { "Retry-After": "30" })); });
  rerender({ last: 102 });
  await act(async () => { await vi.advanceTimersByTimeAsync(29_000); });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(result.current.AAPL.close).toBe(99);
});

it("REL-295 drops a late response after unmount without arming a retry", async () => {
  vi.useFakeTimers();
  let release!: (value: Response) => void;
  const fetchMock = vi.fn(() => new Promise<Response>(resolve => { release = resolve; }));
  vi.stubGlobal("fetch", fetchMock);
  const { unmount } = renderHook(() => usePreviousClose({ AAPL: price("AAPL") }));
  unmount();
  await act(async () => { release(response({}, 429, { "Retry-After": "30" })); });
  expect(vi.getTimerCount()).toBe(0);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("REL-295 rejects a previous session's late successful close", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-29T15:00:00Z"));
  const releases: ((value: Response) => void)[] = [];
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { releases.push(resolve); })));
  const { result, rerender } = renderHook(() => usePreviousClose({ AAPL: price("AAPL") }));
  vi.setSystemTime(new Date("2026-09-30T15:00:00Z"));
  rerender();
  await act(async () => { releases[1](response({ closes: { AAPL: 98 } })); });
  await act(async () => { releases[0](response({ closes: { AAPL: 99 } })); });
  expect(result.current.AAPL.close).toBe(98);
});
