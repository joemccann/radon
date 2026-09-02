/** @vitest-environment jsdom */
/**
 * useStreaks hook — the fetch owner for GET /api/streaks (T-355). The panel
 * suite mocks this hook, so the wire (exact URL, no-store init), the stale
 * response sequence guard, and the error mapping are asserted here.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useStreaks } from "../lib/useStreaks";
import type { StreaksData } from "../lib/streaks";

function payload(symbol: string): StreaksData {
  return { symbol } as unknown as StreaksData;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function okResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response;
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useStreaks wire contract", () => {
  it("requests the exact /api/streaks URL with cache: no-store and a timeout signal", async () => {
    fetchMock.mockResolvedValue(okResponse(payload("SPY")));
    const { result } = renderHook(() => useStreaks("spy"));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    // Full URL string, never url.includes — a path typo must fail here.
    expect(url).toBe("/api/streaks?symbol=spy");
    expect(init.method).toBe("GET");
    expect(init.cache).toBe("no-store");
    expect(init.signal).toBeInstanceOf(AbortSignal);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data?.symbol).toBe("SPY");
    expect(result.current.error).toBeNull();
  });

  it("refresh() refires the same request", async () => {
    fetchMock.mockResolvedValue(okResponse(payload("SPY")));
    const { result } = renderHook(() => useStreaks("SPY"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/streaks?symbol=SPY");
  });
});

describe("useStreaks stale-response guard", () => {
  it("drops the SPY response when it resolves after the QQQ rerender", async () => {
    const first = deferred<Response>();
    const second = deferred<Response>();
    fetchMock
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);

    const { result, rerender } = renderHook(
      ({ symbol }: { symbol: string }) => useStreaks(symbol),
      { initialProps: { symbol: "SPY" } },
    );
    expect(fetchMock.mock.calls[0][0]).toBe("/api/streaks?symbol=SPY");

    rerender({ symbol: "QQQ" });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/streaks?symbol=QQQ");

    // The second (current) request resolves first…
    await act(async () => {
      second.resolve(okResponse(payload("QQQ")));
    });
    await waitFor(() => expect(result.current.data?.symbol).toBe("QQQ"));

    // …and the abandoned SPY request resolves LAST. It must be dropped.
    await act(async () => {
      first.resolve(okResponse(payload("SPY")));
    });
    expect(result.current.data?.symbol).toBe("QQQ");
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });
});

describe("useStreaks error mapping", () => {
  it("surfaces the route's structured error body", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => ({ error: "IB gateway unreachable" }),
    } as Response);
    const { result } = renderHook(() => useStreaks("SPY"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("IB gateway unreachable");
    expect(result.current.data).toBeNull();
  });

  it("falls back to a status-coded message when the error body is not JSON", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => {
        throw new Error("not json");
      },
    } as unknown as Response);
    const { result } = renderHook(() => useStreaks("SPY"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("Streaks fetch failed (503)");
    expect(result.current.data).toBeNull();
  });

  it("maps a network rejection to its message", async () => {
    fetchMock.mockRejectedValue(new Error("Failed to fetch"));
    const { result } = renderHook(() => useStreaks("SPY"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("Failed to fetch");
    expect(result.current.data).toBeNull();
  });
});
