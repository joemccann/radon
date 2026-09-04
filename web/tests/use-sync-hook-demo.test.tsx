/**
 * @vitest-environment jsdom
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useSyncHook } from "@/lib/useSyncHook";

type Payload = { scan_time: string; stale: boolean };

function response(): Response {
  return new Response(JSON.stringify({ scan_time: "2031-02-10T18:45:00.000Z", stale: true }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function methodOf(call: unknown[]): string {
  return String(((call[1] ?? {}) as RequestInit).method ?? "GET").toUpperCase();
}

async function flushRequests(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useSyncHook in the demo deployment", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("performs one initial GET with no automatic POST, poll, or stale retry", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => response());
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useSyncHook<Payload>({
      endpoint: "/api/regime",
      interval: 100,
      hasPost: true,
      shouldRetry: (payload) => payload.stale,
      retryIntervalMs: 50,
      retryMethod: "POST",
    }, true));

    await flushRequests();
    expect(result.current.loading).toBe(false);
    expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET"]);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60 * 60_000);
    });
    expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET"]);
  });

  it("turns manual sync into a single GET", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => response());
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useSyncHook<Payload>({
      endpoint: "/api/gex",
      interval: 100,
      hasPost: true,
    }, true));

    await flushRequests();
    act(() => result.current.syncNow());
    await flushRequests();

    expect(fetchMock.mock.calls.map(methodOf)).toEqual(["GET", "GET"]);
  });
});
