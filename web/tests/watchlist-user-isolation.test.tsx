/** @vitest-environment jsdom */
import React from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ userId: "user_a" as string | null, isLoaded: true, isSignedIn: true }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ ...auth }) }));
import { useWatchlist } from "@/lib/useWatchlist";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("useWatchlist identity isolation", () => {
  it("does not expose user A cache or a stale completion after switching to user B", async () => {
    let resolveA: ((response: Response) => void) | undefined;
    const pendingA = new Promise<Response>((resolve) => { resolveA = resolve; });
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockImplementationOnce(() => pendingA)
      .mockResolvedValueOnce(new Response(JSON.stringify({
        watchlist: [{ id: "b1", symbol: "MSFT", sector: null, added_at: "2026-01-01" }],
      }), { status: 200 }));

    const hook = renderHook(() => useWatchlist());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    auth.userId = "user_b";
    hook.rerender();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(hook.result.current.watchlist.map((entry) => entry.symbol)).toEqual(["MSFT"]));

    await act(async () => {
      resolveA?.(new Response(JSON.stringify({
        watchlist: [{ id: "a1", symbol: "AAPL", sector: null, added_at: "2026-01-01" }],
      }), { status: 200 }));
      await pendingA;
    });
    expect(hook.result.current.watchlist.map((entry) => entry.symbol)).toEqual(["MSFT"]);
  });

  it("clears visible state on sign-out", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      watchlist: [{ id: "a1", symbol: "AAPL", sector: null, added_at: "2026-01-01" }],
    }), { status: 200 }));
    auth.userId = "user_a";
    auth.isSignedIn = true;
    const hook = renderHook(() => useWatchlist());
    await waitFor(() => expect(hook.result.current.watchlist).toHaveLength(1));
    auth.userId = null;
    auth.isSignedIn = false;
    hook.rerender();
    expect(hook.result.current.watchlist).toEqual([]);
  });
});
