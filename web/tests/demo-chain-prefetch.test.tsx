/** @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";

import { useChainPrefetch } from "@/lib/useChainPrefetch";

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ strikes: [100] }))));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("demo option-chain prefetch", () => {
  it("can disable background expiries while retaining the selected-expiry cache API", async () => {
    const { result } = renderHook(() => useChainPrefetch(
      "AAPL",
      ["20260911", "20260918", "20261016"],
      "20260911",
    ));

    act(() => result.current.cacheStrikes("20260911", [270, 275, 280]));
    await act(async () => {
      vi.advanceTimersByTime(20_000);
      await Promise.resolve();
    });

    expect(result.current.getCachedStrikes("20260911")).toEqual([270, 275, 280]);
    expect(fetch).not.toHaveBeenCalled();
  });
});
