// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useAutoSyncOnStale, resetAutoSyncCooldowns } from "../lib/useAutoSyncOnStale";

describe("useAutoSyncOnStale", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-18T15:52:00Z"));
    // REL-048 / R-105: the cooldown moved to module scope so remounts and
    // sibling tabs share one window against the per-operator rate limiter,
    // which means each case must clear it explicitly.
    resetAutoSyncCooldowns();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("fires syncNow once when the rendered snapshot is stale", () => {
    const syncNow = vi.fn();
    const { rerender } = renderHook(
      ({ stale }) => useAutoSyncOnStale(stale, syncNow, "orders", true),
      { initialProps: { stale: false } },
    );
    expect(syncNow).not.toHaveBeenCalled();

    rerender({ stale: true });
    expect(syncNow).toHaveBeenCalledTimes(1);
  });

  it("does not refire while the snapshot stays stale across re-renders", () => {
    const syncNow = vi.fn();
    const { rerender } = renderHook(
      ({ stale }) => useAutoSyncOnStale(stale, syncNow, "orders", true),
      { initialProps: { stale: true } },
    );
    expect(syncNow).toHaveBeenCalledTimes(1);

    rerender({ stale: true });
    rerender({ stale: true });
    expect(syncNow).toHaveBeenCalledTimes(1);
  });

  it("does not fire when disabled (demo mode)", () => {
    const syncNow = vi.fn();
    renderHook(() => useAutoSyncOnStale(true, syncNow, "orders", false));
    expect(syncNow).not.toHaveBeenCalled();
  });

  it("cooldown bounds repeat fires within 60s of the same target", () => {
    const syncNow = vi.fn();
    const { rerender } = renderHook(
      ({ stale }) => useAutoSyncOnStale(stale, syncNow, "orders", true),
      { initialProps: { stale: true } },
    );
    expect(syncNow).toHaveBeenCalledTimes(1);

    rerender({ stale: false });
    rerender({ stale: true });
    expect(syncNow).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(61_000);
    rerender({ stale: false });
    rerender({ stale: true });
    expect(syncNow).toHaveBeenCalledTimes(2);
  });

  it("retries on the staleness tick while the snapshot stays stale, backoff-bounded", () => {
    // REL-048 / R-105: the window used to be a flat 60s, so a producer that
    // returns HTTP 200 without refreshing last_sync was re-fired once a
    // minute forever. Each fire that fails to clear staleness doubles it.
    const syncNow = vi.fn();
    const { rerender } = renderHook(
      ({ tick }) => useAutoSyncOnStale(true, syncNow, "orders", true, tick),
      { initialProps: { tick: 0 } },
    );
    expect(syncNow).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(30_000);
    rerender({ tick: 1 });
    expect(syncNow).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(31_000);
    rerender({ tick: 2 });
    expect(syncNow).toHaveBeenCalledTimes(1); // 2x window after one failure

    vi.advanceTimersByTime(60_000);
    rerender({ tick: 3 });
    expect(syncNow).toHaveBeenCalledTimes(2);
  });

  it("tracks cooldown per target so a route change still syncs the new page", () => {
    const portfolioSync = vi.fn();
    const ordersSync = vi.fn();
    const { rerender } = renderHook(
      ({ stale, syncNow, target }: { stale: boolean; syncNow: () => void; target: string }) =>
        useAutoSyncOnStale(stale, syncNow, target, true),
      { initialProps: { stale: true, syncNow: portfolioSync, target: "portfolio" } },
    );
    expect(portfolioSync).toHaveBeenCalledTimes(1);

    rerender({ stale: true, syncNow: ordersSync, target: "orders" });
    expect(ordersSync).toHaveBeenCalledTimes(1);
    expect(portfolioSync).toHaveBeenCalledTimes(1);
  });
});
