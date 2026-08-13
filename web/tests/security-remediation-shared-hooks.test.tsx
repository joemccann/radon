// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

function jsonResponse(body: unknown, ok = true): Response {
  return {
    ok,
    json: async () => body,
  } as Response;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.resetModules();
});

describe("useAlerts request identity", () => {
  it("does not let an older refresh restore pre-mutation rules", async () => {
    const oldRead = deferred<Response>();
    let getCount = 0;
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return jsonResponse({ ok: true });
      getCount += 1;
      if (getCount === 1) return oldRead.promise;
      return jsonResponse({ rules: [{ id: "new", ticker: "SPY", metric: "price", op: ">", threshold: 600, channel: "app", created_at: "2026-08-13", last_fired_at: null }] });
    }));
    const { useAlerts } = await import("@/lib/useAlerts");
    const { result } = renderHook(() => useAlerts());

    await act(async () => {
      await result.current.createRule({ ticker: "SPY", metric: "price", op: ">", threshold: 600 });
    });
    expect(result.current.rules.map((rule) => rule.id)).toEqual(["new"]);

    await act(async () => {
      oldRead.resolve(jsonResponse({ rules: [{ id: "old" }] }));
      await Promise.resolve();
    });
    expect(result.current.rules.map((rule) => rule.id)).toEqual(["new"]);
  });
});

describe("useBookmarks shared-store concurrency", () => {
  it("keeps initial failures retryable and not loaded", async () => {
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      calls += 1;
      return calls === 1
        ? jsonResponse({}, false)
        : jsonResponse({ bookmarks: [] });
    }));
    const { useBookmarks } = await import("@/lib/useBookmarks");
    const { result } = renderHook(() => useBookmarks());

    await waitFor(() => expect(result.current.error).toBe("Failed to fetch bookmarks"));
    expect(result.current.isLoading).toBe(false);
    await act(async () => { await result.current.retry(); });
    expect(result.current.error).toBeNull();
    expect(calls).toBe(2);
  });

  it("rolls back only the failed mutation and retains a concurrent success", async () => {
    const a = { id: "a-row", post_id: "a", snapshot: null, saved_at: "2026-08-13" };
    let call = 0;
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      call += 1;
      if (call === 1) return jsonResponse({ bookmarks: [] });
      if (call === 2 && init?.method === "POST") return jsonResponse({ ok: true });
      if (call === 3) return jsonResponse({ bookmarks: [a] });
      if (call === 4 && init?.method === "POST") return jsonResponse({}, false);
      throw new Error(`unexpected fetch ${call}`);
    }));
    const { useBookmarks } = await import("@/lib/useBookmarks");
    const { result } = renderHook(() => useBookmarks());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let outcomes: PromiseSettledResult<void>[] = [];
    await act(async () => {
      outcomes = await Promise.allSettled([
        result.current.toggleBookmark({ id: "a" }),
        result.current.toggleBookmark({ id: "b" }),
      ]);
    });

    expect(outcomes.map((outcome) => outcome.status)).toEqual(["fulfilled", "rejected"]);
    expect(result.current.isBookmarked("a")).toBe(true);
    expect(result.current.isBookmarked("b")).toBe(false);
  });
});
