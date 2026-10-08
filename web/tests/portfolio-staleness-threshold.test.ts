// @vitest-environment jsdom
/**
 * The portfolio stale threshold (60s) equalled the producer cadence
 * (radon-portfolio-sync.timer, every minute) while a sync run takes p50 26s
 * and p99 ~61s, so a healthy snapshot read stale for part of every cycle and
 * every tab auto-fired POST /api/portfolio about once a minute (2906 browser
 * POSTs in 48h from one desktop), doubling the IB sync load. The threshold is
 * now the cadence plus the sync tail, so only a producer that has actually
 * fallen behind arms the auto-sync.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import { resetAutoSyncCooldowns, useAutoSyncOnStale } from "../lib/useAutoSyncOnStale";
import {
  PORTFOLIO_SNAPSHOT_STALE_THRESHOLD_MS,
  useSnapshotStaleness,
} from "../lib/useSnapshotStaleness";

const NOW = new Date("2026-10-08T15:00:00Z"); // Thu 11:00 ET, timer running
const PORTFOLIO_TIMER_CADENCE_MS = 60_000;
const SYNC_P99_MS = 61_000;

function renderAutoSync(ageMs: number, syncNow: () => void) {
  const lastSync = new Date(NOW.getTime() - ageMs).toISOString();
  return renderHook(() => {
    const { isStale, tick } = useSnapshotStaleness(lastSync, PORTFOLIO_SNAPSHOT_STALE_THRESHOLD_MS);
    useAutoSyncOnStale(isStale, syncNow, "portfolio", true, tick);
    return isStale;
  });
}

describe("portfolio snapshot staleness threshold", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    resetAutoSyncCooldowns();
  });

  afterEach(() => {
    vi.useRealTimers();
    resetAutoSyncCooldowns();
  });

  it("covers the producer cadence plus the sync tail", () => {
    expect(PORTFOLIO_SNAPSHOT_STALE_THRESHOLD_MS).toBeGreaterThan(PORTFOLIO_TIMER_CADENCE_MS + SYNC_P99_MS);
  });

  it("does not auto-sync a snapshot one timer cycle old", () => {
    const syncNow = vi.fn();
    const { result } = renderAutoSync(70_000, syncNow);
    expect(result.current).toBe(false);
    expect(syncNow).not.toHaveBeenCalled();
  });

  it("still auto-syncs once the producer has fallen behind", () => {
    const syncNow = vi.fn();
    const { result } = renderAutoSync(PORTFOLIO_SNAPSHOT_STALE_THRESHOLD_MS + 10_000, syncNow);
    expect(result.current).toBe(true);
    expect(syncNow).toHaveBeenCalledTimes(1);
  });
});
