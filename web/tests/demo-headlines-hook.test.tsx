/**
 * @vitest-environment jsdom
 *
 * The demo UI is hosted by Vercel, so it has no same-origin WebSocket upgrade
 * route. Demo headlines must use the authenticated HTTP snapshot transport
 * instead of attempting /ws-headlines and dropping into the unavailable state.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(public url: string) {
    MockWebSocket.instances.push(this);
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }
}

const SNAPSHOT = {
  items: [
    {
      kind: "headline",
      id: "demo-1",
      time: "2026-09-04T18:47:44.000Z",
      important: true,
      content: "Demo headline transport is current.",
      impact: [{ symbol: "SPX", impact: "bullish" }],
    },
  ],
};

beforeEach(() => {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => SNAPSHOT,
  }));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("useHeadlines demo transport", () => {
  it("loads a no-store snapshot without opening an unavailable WebSocket", async () => {
    const { useHeadlines } = await import("../lib/useHeadlines");
    const rendered = renderHook(() => useHeadlines());

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(fetch).toHaveBeenCalledWith("/api/headlines", expect.objectContaining({
      cache: "no-store",
    }));
    expect(MockWebSocket.instances).toHaveLength(0);
    expect(rendered.result.current.status).toBe("live");
    expect(rendered.result.current.items).toEqual(SNAPSHOT.items);

    rendered.unmount();
  });

  it("polls again and preserves the last snapshot when a refresh fails", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => SNAPSHOT })
      .mockRejectedValueOnce(new Error("offline"));
    vi.stubGlobal("fetch", fetchMock);
    const { useHeadlines } = await import("../lib/useHeadlines");
    const rendered = renderHook(() => useHeadlines());

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(rendered.result.current.status).toBe("live");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(rendered.result.current.status).toBe("down");
    expect(rendered.result.current.items).toEqual(SNAPSHOT.items);
    rendered.unmount();
  });

  it("renders a stale fallback snapshot as degraded instead of live", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ...SNAPSHOT, degraded: true }),
    }));
    const { useHeadlines } = await import("../lib/useHeadlines");
    const rendered = renderHook(() => useHeadlines());

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(rendered.result.current.items).toEqual(SNAPSHOT.items);
    expect(rendered.result.current.status).toBe("down");
    rendered.unmount();
  });

  it("aborts an in-flight snapshot when the dashboard unmounts", async () => {
    const fetchMock = vi.fn().mockReturnValue(new Promise(() => {}));
    vi.stubGlobal("fetch", fetchMock);
    const { useHeadlines } = await import("../lib/useHeadlines");
    const rendered = renderHook(() => useHeadlines());
    const options = fetchMock.mock.calls[0]?.[1] as RequestInit;

    rendered.unmount();

    expect(options.signal?.aborted).toBe(true);
  });
});
