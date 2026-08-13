/** @vitest-environment jsdom */
import React from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ userId: "watch-user", isLoaded: true, isSignedIn: true }),
}));
import { useWatchlist } from "@/lib/useWatchlist";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("useWatchlist mutation ordering", () => {
  it("late_failure_cannot_erase_concurrent_success_or_reuse_old_read", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ watchlist: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response("failed", { status: 500 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        watchlist: [{ id: "m", symbol: "MSFT", sector: null, added_at: "2026-08-13" }],
      }), { status: 200 }));
    const hook = renderHook(() => useWatchlist());
    await waitFor(() => expect(hook.result.current.isLoading).toBe(false));

    let first!: Promise<void>;
    let second!: Promise<void>;
    act(() => {
      first = hook.result.current.toggleWatch("AAPL");
      second = hook.result.current.toggleWatch("MSFT");
    });
    await expect(first).rejects.toThrow("Failed to update watchlist");
    await act(async () => { await second; });

    expect(fetchMock.mock.calls[1][1]?.method).toBe("POST");
    expect(fetchMock.mock.calls[2][1]?.method).toBe("POST");
    expect(hook.result.current.watchlist.map((entry) => entry.symbol)).toEqual(["MSFT"]);
  });
});
