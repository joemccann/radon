/** @vitest-environment jsdom */
import React from "react";
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  auth: { isLoaded: true, isSignedIn: false, userId: null as string | null },
  useAuth: vi.fn(),
}));
vi.mock("@clerk/nextjs", () => ({ useAuth: mocks.useAuth }));
import SignOutCachePurge from "../components/SignOutCachePurge";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("SignOutCachePurge", () => {
  it("purges through controller and active registration on initial signed-out state", async () => {
    mocks.useAuth.mockImplementation(() => mocks.auth);
    const controllerPost = vi.fn();
    const activePost = vi.fn();
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: {
        controller: { postMessage: controllerPost },
        getRegistration: vi.fn(async () => ({ active: { postMessage: activePost } })),
      },
    });
    render(<SignOutCachePurge />);
    await waitFor(() => expect(activePost).toHaveBeenCalled());
    expect(controllerPost).toHaveBeenCalledWith({ type: "radon-clear-caches", identity: null });
  });

  it("purges again when the authenticated identity changes", async () => {
    mocks.auth.isSignedIn = true;
    mocks.auth.userId = "user_a";
    mocks.useAuth.mockImplementation(() => ({ ...mocks.auth }));
    const postMessage = vi.fn();
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: { controller: { postMessage }, getRegistration: vi.fn(async () => undefined) },
    });
    const view = render(<SignOutCachePurge />);
    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));
    mocks.auth.userId = "user_b";
    view.rerender(<SignOutCachePurge />);
    await waitFor(() => expect(postMessage).toHaveBeenCalledTimes(2));
    expect(postMessage).toHaveBeenLastCalledWith({ type: "radon-clear-caches", identity: "user_b" });
  });
});

describe("SignOutCachePurge — in-memory per-user stores", () => {
  it("drops return-cache snapshots and bookmarks when the identity changes", async () => {
    const { createReturnCache, getReturnCacheGeneration } = await import("../lib/returnCache");
    const { useBookmarks } = await import("../lib/useBookmarks");
    Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: undefined });

    const owner = { id: "b1", post_id: "post-owner", snapshot: null, saved_at: "2026-09-01T00:00:00Z" };
    let served: unknown[] = [owner];
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({ bookmarks: served }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    const seen: string[][] = [];
    function Probe() {
      const { bookmarks } = useBookmarks();
      seen.push(bookmarks.map((b) => b.post_id));
      return null;
    }

    mocks.auth.isLoaded = true;
    mocks.auth.isSignedIn = true;
    mocks.auth.userId = "user_a";
    mocks.useAuth.mockImplementation(() => ({ ...mocks.auth }));

    const returnCache = createReturnCache();
    const view = render(<><SignOutCachePurge /><Probe /></>);
    await waitFor(() => expect(seen.at(-1)).toEqual(["post-owner"]));
    returnCache.write("/api/portfolio", { data: { owner: true }, fetchedAt: Date.now(), lastSync: null }, getReturnCacheGeneration());

    served = [];
    mocks.auth.isSignedIn = false;
    mocks.auth.userId = null;
    view.rerender(<><SignOutCachePurge /><Probe /></>);

    await waitFor(() => expect(seen.at(-1)).toEqual([]));
    expect(returnCache.read("/api/portfolio")).toBeNull();
    vi.unstubAllGlobals();
  });
});
