/**
 * @vitest-environment jsdom
 *
 * useBpi() — GET-only polling hook over /api/bpi (useMarginDebt mold via
 * useSyncHook): no scan POST exists (the radon-bpi timer owns refreshes),
 * every fetch is cache: no-store, and refresh() re-issues a GET.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buildBpiResponseFixture } from "./bpi-fixture";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("useBpi", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("GETs /api/bpi with cache no-store and exposes the response", async () => {
    const payload = buildBpiResponseFixture();
    const fetchSpy = vi.fn(async () => jsonResponse(payload));
    global.fetch = fetchSpy as typeof fetch;
    const { useBpi } = await import("@/lib/useBpi");

    const { result } = renderHook(() => useBpi());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/bpi",
      expect.objectContaining({ method: "GET", cache: "no-store" }),
    );
    // GET-only hook: no POST ever fires, on mount or on the initial sync.
    for (const [, init] of fetchSpy.mock.calls) {
      expect((init as RequestInit | undefined)?.method).toBe("GET");
    }
    expect(result.current.data?.generated_at).toBe(payload.generated_at);
    expect(result.current.error).toBeNull();
  });

  it("refresh() re-issues a GET", async () => {
    const payload = buildBpiResponseFixture();
    const fetchSpy = vi.fn(async () => jsonResponse(payload));
    global.fetch = fetchSpy as typeof fetch;
    const { useBpi } = await import("@/lib/useBpi");

    const { result } = renderHook(() => useBpi());
    await waitFor(() => expect(result.current.loading).toBe(false));
    const callsBefore = fetchSpy.mock.calls.length;

    await act(async () => {
      result.current.refresh();
    });

    await waitFor(() => expect(fetchSpy.mock.calls.length).toBeGreaterThan(callsBefore));
    expect(fetchSpy.mock.calls.at(-1)?.[1]).toMatchObject({ method: "GET", cache: "no-store" });
  });

  it("surfaces an error when the route fails and no data is cached", async () => {
    const fetchSpy = vi.fn(async () => jsonResponse({ error: "boom" }, 500));
    global.fetch = fetchSpy as typeof fetch;
    const { useBpi } = await import("@/lib/useBpi");

    const { result } = renderHook(() => useBpi());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.data).toBeNull();
    expect(result.current.error).toBeTruthy();
  });
});
