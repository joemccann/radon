/**
 * @vitest-environment jsdom
 *
 * Data hooks emit offline signals: offline-served when a response carries the
 * SW's X-Radon-Offline marker (payload still rendered), fetch-failure when the
 * network fetch rejects (prior data retained), fetch-success on any genuine
 * network response. fetch stubbed per service-health-transport-ui pattern.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { subscribeOfflineSignals, type BusSignal } from "../lib/offline/offlineSignals";
import { usePortfolio } from "../lib/usePortfolio";
import { useSyncHook } from "../lib/useSyncHook";

const CACHED_AT = 1_700_000_000_000;
const CACHED_AT_ISO = new Date(CACHED_AT).toISOString();

function jsonResponse(body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

const portfolioBody = { positions: [], last_sync: new Date(CACHED_AT).toISOString() };
const scannerBody = { signals: [{ ticker: "AAPL" }] };

let signals: BusSignal[] = [];
let unsubscribe: () => void = () => {};

function signalTypes(): string[] {
  return signals.map((s) => s.type);
}

beforeEach(() => {
  signals = [];
  unsubscribe = subscribeOfflineSignals((s) => signals.push(s));
});

afterEach(() => {
  unsubscribe();
  vi.unstubAllGlobals();
});

describe("usePortfolio offline signals", () => {
  it("offline-served response emits the signal AND populates data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          jsonResponse(portfolioBody, {
            "X-Radon-Offline": "1",
            "X-Radon-Cached-At": CACHED_AT_ISO,
          }),
        ),
      ),
    );
    const { result } = renderHook(() => usePortfolio(false));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(signals).toContainEqual({ type: "offline-served", cachedAt: CACHED_AT });
    expect(signalTypes()).not.toContain("fetch-success");
  });

  it("fetch rejection after loaded data emits fetch-failure and retains data", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(portfolioBody))
      .mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => usePortfolio(false));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    // Drive a second read via the visibilitychange refresh path.
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(signalTypes()).toContain("fetch-failure"));
    expect(result.current.data).not.toBeNull();
  });

  it("plain 200 without markers emits fetch-success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(portfolioBody))),
    );
    const { result } = renderHook(() => usePortfolio(false));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(signalTypes()).toContain("fetch-success");
    expect(signalTypes()).not.toContain("offline-served");
  });
});

describe("useSyncHook offline signals", () => {
  const config = { endpoint: "/api/scanner", hasPost: false, interval: 0 } as const;

  it("offline-served response emits the signal AND populates data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          jsonResponse(scannerBody, {
            "X-Radon-Offline": "1",
            "X-Radon-Cached-At": CACHED_AT_ISO,
          }),
        ),
      ),
    );
    const { result } = renderHook(() => useSyncHook<typeof scannerBody>(config, true));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(signals).toContainEqual({ type: "offline-served", cachedAt: CACHED_AT });
    expect(signalTypes()).not.toContain("fetch-success");
  });

  it("fetch rejection after loaded data emits fetch-failure and retains data", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(scannerBody))
      .mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useSyncHook<typeof scannerBody>(config, true));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    // GET-only hooks no longer re-GET immediately after mount; drive a second
    // read via syncNow so the rejection path still exercises offline signals.
    act(() => {
      result.current.syncNow();
    });
    await waitFor(() => expect(signalTypes()).toContain("fetch-failure"));
    expect(result.current.data).not.toBeNull();
  });

  it("plain 200 without markers emits fetch-success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(scannerBody))),
    );
    const { result } = renderHook(() => useSyncHook<typeof scannerBody>(config, true));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(signalTypes()).toContain("fetch-success");
    expect(signalTypes()).not.toContain("offline-served");
  });
});
