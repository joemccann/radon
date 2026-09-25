/** @vitest-environment jsdom */
import React from "react";
import { act, cleanup, render, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReturnCacheProvider, purgeReturnCaches } from "../lib/returnCache";
import { useSyncHook } from "../lib/useSyncHook";
import { resetBookmarksCache, useBookmarks } from "../lib/useBookmarks";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("REL-284 account identity isolation", () => {
  it("does not cache a prior identity GET that settles after purge and remount", async () => {
    const first = deferred<Response>();
    const second = deferred<Response>();
    const fetchMock = vi.fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    vi.stubGlobal("fetch", fetchMock);

    function Snapshot() {
      const state = useSyncHook<{ owner: string }>({ endpoint: "/api/account", interval: 60_000 }, true);
      return <output>{state.data?.owner ?? "empty"}</output>;
    }
    function Harness({ identity, mounted }: { identity: string; mounted: boolean }) {
      return <ReturnCacheProvider>{mounted ? <Snapshot key={identity} /> : null}</ReturnCacheProvider>;
    }

    const view = render(<Harness identity="a" mounted />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    act(() => purgeReturnCaches());
    view.rerender(<Harness identity="b" mounted={false} />);
    await act(async () => { first.resolve(response({ owner: "account-a" })); });
    view.rerender(<Harness identity="b" mounted />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    expect(view.getByText("empty")).toBeTruthy();
    await act(async () => { second.resolve(response({ owner: "account-b" })); });
    await waitFor(() => expect(view.getByText("account-b")).toBeTruthy());
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not run queued bookmark writes or let an old completion replace the new cache", async () => {
    const oldPost = deferred<Response>();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? "GET") === "POST") return oldPost.promise;
      return Promise.resolve(response({ bookmarks: [{ id: "b", post_id: "account-b", snapshot: null, saved_at: "now" }] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    resetBookmarksCache();
    const { result } = renderHook(() => useBookmarks());
    await waitFor(() => expect(result.current.bookmarks.map((b) => b.post_id)).toEqual(["account-b"]));

    let firstWrite!: Promise<void>;
    let queuedWrite!: Promise<void>;
    act(() => {
      firstWrite = result.current.toggleBookmark({ id: "old-one" });
      queuedWrite = result.current.toggleBookmark({ id: "old-two" });
    });
    await waitFor(() => expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1));

    act(() => resetBookmarksCache());
    await waitFor(() => expect(result.current.bookmarks.map((b) => b.post_id)).toEqual(["account-b"]));
    await act(async () => { oldPost.resolve(response({ ok: true })); });
    await firstWrite;
    await queuedWrite;

    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1);
    expect(result.current.bookmarks.map((b) => b.post_id)).toEqual(["account-b"]);
  });
});
