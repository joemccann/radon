/** @vitest-environment jsdom */

import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MarketState } from "@/lib/useMarketHours";

const mockUseSyncHook = vi.fn(() => ({
  data: null,
  loading: false,
  syncing: false,
  error: null,
  lastSync: null,
  syncNow: vi.fn(),
}));

vi.mock("@/lib/useSyncHook", () => ({
  useSyncHook: (...args: unknown[]) => mockUseSyncHook(...args),
}));

import { useSkew } from "@/lib/useSkew";

describe("useSkew realtime cadence", () => {
  beforeEach(() => mockUseSyncHook.mockClear());

  it("polls the shared snapshot every minute during regular trading hours", () => {
    renderHook(() => useSkew(MarketState.OPEN));
    expect(mockUseSyncHook).toHaveBeenCalledWith(
      expect.objectContaining({ endpoint: "/api/skew", interval: 60_000, hasPost: false }),
      true,
    );
  });

  it("pauses background polling when the market is closed", () => {
    renderHook(() => useSkew(MarketState.CLOSED));
    expect(mockUseSyncHook).toHaveBeenCalledWith(
      expect.objectContaining({ interval: 0 }),
      true,
    );
  });
});
