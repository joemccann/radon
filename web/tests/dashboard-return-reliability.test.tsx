/**
 * @vitest-environment jsdom
 *
 * Returning to a page must not refetch a fresh snapshot, must not fall
 * through to /data/posts.json, and must not mint a second websocket ticket
 * while the first POST is still aborting.
 */
import { act, cleanup, render, renderHook, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReturnCacheProvider } from "@/lib/returnCache";
import { useSyncHook } from "@/lib/useSyncHook";
import { useNewsfeedPosts } from "@/lib/useNewsfeedPosts";
import { useCatalysts } from "@/lib/useCatalysts";

const REGIME = { scan_time: "2026-09-22T14:00:00Z", vix: 18 };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function methods(fetchMock: ReturnType<typeof vi.fn>): string[] {
  return fetchMock.mock.calls.map((call) => String((call[1] as RequestInit | undefined)?.method ?? "GET"));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function Probe({ endpoint }: { endpoint: string }) {
  const state = useSyncHook<typeof REGIME>({
    endpoint,
    interval: 60_000,
    hasPost: true,
  }, true);
  return createElement("span", { "data-vix": state.data?.vix ?? "none" });
}

function CacheHarness({ show, endpoint }: { show: boolean; endpoint: string }) {
  return createElement(
    ReturnCacheProvider,
    null,
    show ? createElement(Probe, { endpoint }) : null,
  );
}

describe("return cache", () => {
  it("does not GET or POST again when the same endpoint remounts inside the poll window", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(REGIME));
    vi.stubGlobal("fetch", fetchMock);

    const view = render(createElement(CacheHarness, { show: true, endpoint: "/api/regime" }));
    await waitFor(() => expect(methods(fetchMock)).toEqual(["GET"]));
    view.rerender(createElement(CacheHarness, { show: false, endpoint: "/api/regime" }));
    view.rerender(createElement(CacheHarness, { show: true, endpoint: "/api/regime" }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(methods(fetchMock)).toEqual(["GET"]);
    expect(view.container.querySelector("[data-vix]")?.getAttribute("data-vix")).toBe("18");
  });

  it("keeps the last good snapshot when a later refetch fails", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(REGIME))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);

    const view = render(createElement(CacheHarness, { show: true, endpoint: "/api/gex" }));
    await waitFor(() => expect(view.container.querySelector("[data-vix]")?.getAttribute("data-vix")).toBe("18"));
    view.rerender(createElement(CacheHarness, { show: false, endpoint: "/api/gex" }));

    vi.spyOn(Date, "now").mockReturnValue(Date.now() + 61_000);
    view.rerender(createElement(CacheHarness, { show: true, endpoint: "/api/gex" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(view.container.querySelector("[data-vix]")?.getAttribute("data-vix")).toBe("18");
  });
});

describe("newsfeed posts fallback", () => {
  it("does not request /data/posts.json when the posts route returns 502", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      urls.push(String(url));
      return new Response("bad gateway", { status: 502 });
    }));

    renderHook(() => useNewsfeedPosts());
    await waitFor(() => expect(urls).toContain("/api/newsfeed/posts"));
    expect(urls).not.toContain("/data/posts.json");
  });
});

describe("catalysts visibility", () => {
  it("does not refetch on visibility while the snapshot is still inside the poll window", async () => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    const fetchMock = vi.fn(async () => jsonResponse({
      scan_time: "2026-09-22T14:00:00Z",
      count: 0,
      catalysts: [],
    }));
    vi.stubGlobal("fetch", fetchMock);

    function Probe() {
      useCatalysts(true);
      return null;
    }

    render(createElement(ReturnCacheProvider, null, createElement(Probe)));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("mount does not start a scan POST", () => {
  it("leaves the producer to the GET route", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(REGIME));
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => useSyncHook<typeof REGIME>({
      endpoint: "/api/regime",
      interval: 60_000,
      hasPost: true,
    }, true));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(methods(fetchMock)).toEqual(["GET"]);
  });
});

describe("websocket ticket backoff", () => {
  it("aborts the in-flight POST and does not start another while backing off", async () => {
    vi.resetModules();
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
      });
    }));
    vi.stubGlobal("fetch", fetchMock);

    const { getWsTicket, TicketBackoffError, resetTicketBackoffForTests } = await import("@/lib/wsTicket");
    resetTicketBackoffForTests();
    const controller = new AbortController();
    const first = getWsTicket("clerk-token", controller.signal);
    controller.abort();
    await expect(first).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await expect(getWsTicket("clerk-token")).rejects.toBeInstanceOf(TicketBackoffError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("headlines socket ownership", () => {
  it("keeps the socket open when the feed unmounts", async () => {
    const sockets: Array<{ readyState: number; close: () => void }> = [];
    class MockSocket {
      readyState = 0;
      onopen: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onclose: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(public url: string) {
        sockets.push(this);
      }
      close() {
        this.readyState = 3;
        this.onclose?.();
      }
    }
    vi.stubGlobal("WebSocket", MockSocket);

    const { HeadlinesProvider, useHeadlines } = await import("@/lib/useHeadlines");
    function Feed() {
      useHeadlines();
      return null;
    }
    const view = render(createElement(HeadlinesProvider, null, createElement(Feed)));
    await waitFor(() => expect(sockets).toHaveLength(1));
    view.rerender(createElement(HeadlinesProvider, null));
    expect(sockets).toHaveLength(1);
    expect(sockets[0].readyState).not.toBe(3);
  });
});
