/**
 * @vitest-environment jsdom
 */

import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MarketState, useMarketHours } from "../lib/useMarketHours";

describe("useMarketHours first paint", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("is OPEN on a weekday RTH clock, not a CLOSED placeholder", () => {
    vi.useFakeTimers();
    // Monday 2026-08-17 11:00 ET
    vi.setSystemTime(new Date("2026-08-17T15:00:00Z"));
    const { result } = renderHook(() => useMarketHours());
    expect(result.current).toBe(MarketState.OPEN);
  });
});
