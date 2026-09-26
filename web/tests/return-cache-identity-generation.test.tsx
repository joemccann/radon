/** @vitest-environment jsdom */
import React, { type ReactNode } from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ReturnCacheProvider,
  createReturnCache,
  getReturnCacheGeneration,
  purgeReturnCaches,
  useReturnCache,
} from "../lib/returnCache";
import { useCatalysts } from "../lib/useCatalysts";
import { useNewsfeedPosts } from "../lib/useNewsfeedPosts";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function deferredFetch(body: unknown) {
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const fetchSpy = vi.fn(async () => {
    await gate;
    return new Response(JSON.stringify(body), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchSpy);
  return { fetchSpy, release };
}

const wrapper = ({ children }: { children: ReactNode }) => (
  <ReturnCacheProvider>{children}</ReturnCacheProvider>
);

describe("return cache identity generation", () => {
  it("drops a write captured before a purge and keeps a current one", () => {
    const cache = createReturnCache();
    const stale = getReturnCacheGeneration();
    purgeReturnCaches();
    cache.write("/api/x", { data: 1, fetchedAt: Date.now(), lastSync: null }, stale);
    expect(cache.read("/api/x")).toBeNull();
    cache.write("/api/x", { data: 2, fetchedAt: Date.now(), lastSync: null }, getReturnCacheGeneration());
    expect(cache.read<number>("/api/x")?.data).toBe(2);
  });

  it("useCatalysts does not cache a response that lands after a purge", async () => {
    const { fetchSpy, release } = deferredFetch({ scan_time: "t", catalysts: [] });
    const { result } = renderHook(() => ({ cats: useCatalysts(), cache: useReturnCache() }), { wrapper });
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledWith("/api/catalysts", { cache: "no-store" }));
    act(() => purgeReturnCaches());
    await act(async () => { release(); });
    await waitFor(() => expect(result.current.cats.isLoading).toBe(false));
    expect(result.current.cache?.read("/api/catalysts")).toBeNull();
  });

  it("useNewsfeedPosts does not cache a response that lands after a purge", async () => {
    const { fetchSpy, release } = deferredFetch([]);
    const { result } = renderHook(() => ({ feed: useNewsfeedPosts(), cache: useReturnCache() }), { wrapper });
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    expect(fetchSpy.mock.calls[0][0]).toBe("/api/newsfeed/posts");
    act(() => purgeReturnCaches());
    await act(async () => { release(); });
    await waitFor(() => expect(result.current.feed.loading).toBe(false));
    expect(result.current.cache?.read("/api/newsfeed/posts")).toBeNull();
  });
});
