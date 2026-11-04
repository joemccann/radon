/**
 * @vitest-environment jsdom
 *
 * Audit 2026-11-03 §3.5 `useSyncHook`:
 *  1. GET and POST shared no response sequence, so an older background GET
 *     that resolved after a newer POST overwrote the POST's fresher payload;
 *  2. the stale-data retry counter survived an endpoint change, so a new
 *     endpoint inherited an exhausted retry budget and never retried.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";

import { RouteRefreshContext } from "../lib/RouteRefreshContext";
import { useSyncHook } from "../lib/useSyncHook";

type Payload = { v: number };

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(),
    async json() {
      return body;
    },
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useSyncHook response ordering", () => {
  it("an older GET resolving after a newer POST does not overwrite it", async () => {
    let releaseGet: (() => void) | null = null;
    let gets = 0;
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => {
      if (init?.method === "POST") return Promise.resolve(jsonResponse({ v: 2 }));
      gets += 1;
      if (gets === 1) return Promise.resolve(jsonResponse({ v: 0 }));
      return new Promise((resolve) => {
        releaseGet = () => resolve(jsonResponse({ v: 1 }));
      });
    }));

    let routeKey = "/a";
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(RouteRefreshContext.Provider, { value: routeKey }, children);
    const { result, rerender } = renderHook(
      () => useSyncHook<Payload>({ endpoint: "/api/x", interval: 0 }, true),
      { wrapper },
    );
    await waitFor(() => expect(result.current.data?.v).toBe(0));

    routeKey = "/b"; // background GET, held open
    rerender();
    await waitFor(() => expect(releaseGet).not.toBeNull());

    await act(async () => {
      result.current.syncNow();
    });
    await waitFor(() => expect(result.current.data?.v).toBe(2));

    await act(async () => {
      releaseGet?.();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.data?.v).toBe(2);
  });
});

describe("useSyncHook retry budget per endpoint", () => {
  it("a new endpoint starts with a fresh stale-data retry budget", async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      calls.push(url);
      return jsonResponse({ v: 0 });
    }));
    const alwaysRetry = () => true;
    const config = (endpoint: string) => ({
      endpoint,
      interval: 0,
      hasPost: false,
      shouldRetry: alwaysRetry,
      retryIntervalMs: 100,
      maxRetryAttempts: 1,
      retryMethod: "GET" as const,
    });
    const { rerender } = renderHook(
      ({ endpoint }) => useSyncHook<Payload>(config(endpoint), true),
      { initialProps: { endpoint: "/api/a" } },
    );
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(calls.filter((u) => u === "/api/a")).toHaveLength(2); // initial + its one retry

    rerender({ endpoint: "/api/b" });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(calls.filter((u) => u === "/api/b")).toHaveLength(2);
  });
});
