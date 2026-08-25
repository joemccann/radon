// @vitest-environment jsdom
/**
 * REL-061 / R-149 (P1): a total blackout reports FRESH.
 *
 * `useSnapshotStaleness` returns `{ isStale: false }` when `lastSync` is null
 * and again when the date is unparseable. `lastSync` is null exactly when the
 * producer has never written a snapshot this session, when the GET returned an
 * error body, or when `extractTimestamp` yields null — so the stale pill stays
 * hidden, `useAutoSyncOnStale` (gated on `stale`) never fires, and `syncLabel`
 * degrades to the reassuring "Awaiting first sample".
 *
 * Same honesty class as R-061/REL-035, which covered only the relay. Distinct
 * from R-105, which is a failed sync returning HTTP 200 with a stale
 * timestamp; this is the case where there is no timestamp at all.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

import { useSnapshotStaleness } from "../lib/useSnapshotStaleness";
import {
  resetAutoSyncCooldowns,
  useAutoSyncOnStale,
} from "../lib/useAutoSyncOnStale";

beforeEach(() => {
  vi.useFakeTimers();
  resetAutoSyncCooldowns();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("a missing snapshot is unknown, not fresh", () => {
  it("reports unknown for a null lastSync", () => {
    const { result } = renderHook(() => useSnapshotStaleness(null));
    expect(result.current.state).toBe("unknown");
    expect(result.current.isStale).toBe(true);
  });

  it("reports unknown for an unparseable timestamp", () => {
    const { result } = renderHook(() => useSnapshotStaleness("not-a-date"));
    expect(result.current.state).toBe("unknown");
    expect(result.current.isStale).toBe(true);
  });

  it("reports fresh for a recent snapshot", () => {
    const { result } = renderHook(() =>
      useSnapshotStaleness(new Date(Date.now() - 5_000).toISOString()),
    );
    expect(result.current.state).toBe("fresh");
    expect(result.current.isStale).toBe(false);
  });

  it("reports stale for an aged snapshot", () => {
    const { result } = renderHook(() =>
      useSnapshotStaleness(new Date(Date.now() - 10 * 60_000).toISOString()),
    );
    expect(result.current.state).toBe("stale");
    expect(result.current.isStale).toBe(true);
  });

  it("arms the producer sync during a blackout", () => {
    const syncNow = vi.fn();
    const { result } = renderHook(() => useSnapshotStaleness(null));
    renderHook(() =>
      useAutoSyncOnStale(result.current.isStale, syncNow, "portfolio", true, 0),
    );
    expect(syncNow).toHaveBeenCalledTimes(1);
  });

  it("keeps ticking during a blackout so the retry cadence survives", () => {
    // R-139 removed the unconditional tick; a blackout still needs one,
    // because that IS the retry cadence useAutoSyncOnStale rides on.
    const { result } = renderHook(() => useSnapshotStaleness(null));
    act(() => {
      vi.advanceTimersByTime(90_000);
    });
    expect(result.current.tick).toBeGreaterThan(0);
  });

  it("does not tick while a snapshot is simply fresh", () => {
    // R-139: `tick` is in the returned memo, so an unconditional bump
    // re-rendered WorkspaceShell and every non-memoised child every 30s in
    // every tab — with nothing to recompute in the steady state.
    const { result } = renderHook(() =>
      useSnapshotStaleness(new Date(Date.now() - 1_000).toISOString()),
    );
    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    expect(result.current.tick).toBe(0);
  });

  it("ticks once a fresh snapshot crosses into stale", () => {
    // vitest's fake timers freeze Date.now(), so the wall clock has to be
    // advanced alongside them or the snapshot never ages.
    const start = Date.now();
    const { result } = renderHook(() =>
      useSnapshotStaleness(new Date(start - 1_000).toISOString()),
    );
    expect(result.current.state).toBe("fresh");

    act(() => {
      for (let elapsed = 30_000; elapsed <= 120_000; elapsed += 30_000) {
        vi.setSystemTime(start + elapsed);
        vi.advanceTimersByTime(30_000);
      }
    });

    expect(result.current.state).toBe("stale");
    expect(result.current.tick).toBeGreaterThan(0);
  });
});
