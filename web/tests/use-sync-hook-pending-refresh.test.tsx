/**
 * @vitest-environment jsdom
 *
 * REL-236 / R-640: a fill event landing while a POST sync is already in
 * flight used to be silently dropped by the R-106 in-flight dedup — the exec
 * id was already in the fill-toast seen set, so it was never re-attempted and
 * the positions table stayed stale under a FILLED toast for a full producer
 * period. A mid-flight `syncNow()` must queue exactly ONE follow-up POST,
 * re-fired when the in-flight request settles.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useSyncHook } from "../lib/useSyncHook";

type Payload = { last_sync: string };

const PAYLOAD: Payload = { last_sync: "2026-09-05T10:00:00Z" };

function jsonResponse(body: Payload) {
  return {
    ok: true,
    headers: new Headers(),
    async json() {
      return body;
    },
  };
}

type FetchCall = { url: string; method: string };

function deferredPostFetch() {
  const calls: FetchCall[] = [];
  const postResolvers: Array<() => void> = [];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method });
    if (method === "POST") {
      return new Promise((resolve) => {
        postResolvers.push(() => resolve(jsonResponse(PAYLOAD)));
      });
    }
    return Promise.resolve(jsonResponse(PAYLOAD));
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, postResolvers };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// T-480: drain any queued follow-up on FAKE timers so the "nothing fired"
// window cannot lose a wall-clock race to React's commit/effect flush.
async function flushTimersFake() {
  vi.useFakeTimers();
  try {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
  } finally {
    vi.useRealTimers();
  }
}

describe("useSyncHook queues a sync requested while a POST is in flight (R-640)", () => {
  it("re-fires exactly one follow-up POST after the in-flight POST settles", async () => {
    const { calls, postResolvers } = deferredPostFetch();

    const { result } = renderHook(() =>
      useSyncHook<Payload>({ endpoint: "/api/portfolio" }, true),
    );

    // Mount: initial GET read, then the auto first POST (held open).
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[0]).toEqual({ url: "/api/portfolio", method: "GET" });
    expect(calls[1]).toEqual({ url: "/api/portfolio", method: "POST" });

    // Two fill events land while the POST is still in flight.
    act(() => {
      result.current.syncNow();
      result.current.syncNow();
    });
    // The in-flight dedup must not fire concurrent POSTs...
    expect(calls).toHaveLength(2);

    // ...but settling the in-flight POST must re-fire exactly ONE follow-up.
    await act(async () => {
      postResolvers[0]();
    });
    await waitFor(() => expect(calls).toHaveLength(3));
    expect(calls[2]).toEqual({ url: "/api/portfolio", method: "POST" });

    // Settling the follow-up fires nothing further: the pending flag was
    // consumed, and two mid-flight events coalesced into one refresh.
    await act(async () => {
      postResolvers[1]();
    });
    await flushTimersFake();
    expect(calls).toHaveLength(3);
  });

  it("does not fire a follow-up when nothing arrived mid-flight", async () => {
    const { calls, postResolvers } = deferredPostFetch();

    renderHook(() => useSyncHook<Payload>({ endpoint: "/api/portfolio" }, true));

    await waitFor(() => expect(calls).toHaveLength(2));
    await act(async () => {
      postResolvers[0]();
    });
    await flushTimersFake();
    expect(calls).toHaveLength(2);
  });
});
