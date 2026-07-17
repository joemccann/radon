/**
 * @vitest-environment jsdom
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EXPOSURE_FIXTURE } from "./options-exposure-fixture";

describe("useOptionsExposure", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("requests symbol and frequency with no-store caching", async () => {
    const fetchSpy = vi.fn(async () =>
      new Response(JSON.stringify(EXPOSURE_FIXTURE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    global.fetch = fetchSpy as typeof fetch;
    const { useOptionsExposure } = await import("@/lib/useOptionsExposure");

    const { result } = renderHook(() => useOptionsExposure("mu", "eod"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/options/exposure?symbol=MU&frequency=eod",
      expect.objectContaining({ cache: "no-store", signal: expect.any(AbortSignal) }),
    );
    expect(result.current.data?.symbol).toBe("MU");
    expect(result.current.error).toBeNull();
  });

  it("aborts the previous request when frequency changes", async () => {
    const signals: AbortSignal[] = [];
    global.fetch = vi.fn((_url, init) => {
      signals.push(init?.signal as AbortSignal);
      return new Promise<Response>(() => undefined);
    }) as typeof fetch;
    const { useOptionsExposure } = await import("@/lib/useOptionsExposure");

    const { rerender, unmount } = renderHook(
      ({ frequency }) => useOptionsExposure("MU", frequency),
      { initialProps: { frequency: "eod" as const } },
    );
    rerender({ frequency: "intraday" });
    expect(signals[0].aborted).toBe(true);
    unmount();
    expect(signals[1].aborted).toBe(true);
  });

  it("sanitizes upstream error bodies and can retry", async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(new Response("secret provider traceback", { status: 502 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(EXPOSURE_FIXTURE), { status: 200 }));
    global.fetch = fetchSpy as typeof fetch;
    const { useOptionsExposure } = await import("@/lib/useOptionsExposure");

    const { result } = renderHook(() => useOptionsExposure("MU", "eod"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("Options exposure is temporarily unavailable (HTTP 502)");
    expect(result.current.error).not.toContain("traceback");

    await act(async () => result.current.refresh());
    await waitFor(() => expect(result.current.data?.symbol).toBe("MU"));
  });
});
