// @vitest-environment jsdom
/**
 * /orders used the 60s snapshot threshold against a 5-minute orders-sync
 * loop. A healthy flat book read stale for most of every cycle and the
 * shell auto-posted /api/orders trying to clear a clock that had not moved.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import { resetAutoSyncCooldowns, useAutoSyncOnStale } from "../lib/useAutoSyncOnStale";
import {
  ORDERS_SNAPSHOT_STALE_THRESHOLD_MS,
  useSnapshotStaleness,
} from "../lib/useSnapshotStaleness";

const NOW = new Date("2026-11-12T15:26:10Z");
const ORDERS_SYNC_INTERVAL_MS = 5 * 60_000;
const ORDERS_SYNC_TIMEOUT_MS = 30_000;
const ORDERS_WATCHDOG_WINDOW_MS = 10 * 60_000;

function renderAutoSync(ageMs: number, syncNow: () => void) {
  const lastSync = new Date(NOW.getTime() - ageMs).toISOString();
  return renderHook(() => {
    const { isStale, tick } = useSnapshotStaleness(lastSync, ORDERS_SNAPSHOT_STALE_THRESHOLD_MS);
    useAutoSyncOnStale(isStale, syncNow, "orders", true, tick);
    return isStale;
  });
}

describe("orders snapshot staleness threshold", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    resetAutoSyncCooldowns();
  });

  afterEach(() => {
    vi.useRealTimers();
    resetAutoSyncCooldowns();
  });

  it("covers one orders-sync cycle plus the run, inside the watchdog window", () => {
    expect(ORDERS_SNAPSHOT_STALE_THRESHOLD_MS).toBeGreaterThan(
      ORDERS_SYNC_INTERVAL_MS + ORDERS_SYNC_TIMEOUT_MS,
    );
    expect(ORDERS_SNAPSHOT_STALE_THRESHOLD_MS).toBeLessThan(ORDERS_WATCHDOG_WINDOW_MS);
  });

  it("does not auto-sync a snapshot one orders-sync cycle old", () => {
    const syncNow = vi.fn();
    const { result } = renderAutoSync(ORDERS_SYNC_INTERVAL_MS + ORDERS_SYNC_TIMEOUT_MS, syncNow);
    expect(result.current).toBe(false);
    expect(syncNow).not.toHaveBeenCalled();
  });

  it("still auto-syncs a snapshot past the threshold", () => {
    const syncNow = vi.fn();
    const { result } = renderAutoSync(ORDERS_SNAPSHOT_STALE_THRESHOLD_MS + 10_000, syncNow);
    expect(result.current).toBe(true);
    expect(syncNow).toHaveBeenCalledTimes(1);
  });
});
