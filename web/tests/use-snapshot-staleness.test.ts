// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useSnapshotStaleness } from "../lib/useSnapshotStaleness";

// Saturday 03:00 ET — deep outside any equities session. Staleness is
// session-independent by design: after-hours and overnight fills exist,
// so a snapshot is never allowed to rot just because RTH ended.
const OVERNIGHT_NOW = new Date("2026-08-22T07:00:00Z");

describe("useSnapshotStaleness", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(OVERNIGHT_NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("fresh snapshot is not stale", () => {
    const lastSync = new Date(OVERNIGHT_NOW.getTime() - 10_000).toISOString();
    const { result } = renderHook(() => useSnapshotStaleness(lastSync));
    expect(result.current.isStale).toBe(false);
  });

  it("a >60s-old snapshot is stale even outside market hours", () => {
    const lastSync = new Date(OVERNIGHT_NOW.getTime() - 3 * 60_000).toISOString();
    const { result } = renderHook(() => useSnapshotStaleness(lastSync));
    expect(result.current.isStale).toBe(true);
    expect(result.current.staleAgeMinutes).toBe(3);
  });

  it("re-evaluates on its own clock — a snapshot goes stale while the page sits idle", () => {
    const lastSync = new Date(OVERNIGHT_NOW.getTime() - 10_000).toISOString();
    const { result } = renderHook(() => useSnapshotStaleness(lastSync));
    expect(result.current.isStale).toBe(false);

    act(() => {
      vi.advanceTimersByTime(91_000);
    });
    expect(result.current.isStale).toBe(true);
  });

  it("null or unparseable lastSync is UNKNOWN, which is not fresh", () => {
    // REL-061 / R-149: this asserted `isStale: false`, i.e. a total blackout
    // reported healthy. lastSync is null exactly when the producer has never
    // written a snapshot this session, when the GET returned an error body,
    // or when extractTimestamp yielded null — so the stale pill stayed
    // hidden and useAutoSyncOnStale never fired. There is no age to report,
    // so staleAgeMinutes is still null.
    const { result: nullResult } = renderHook(() => useSnapshotStaleness(null));
    expect(nullResult.current.state).toBe("unknown");
    expect(nullResult.current.isStale).toBe(true);
    expect(nullResult.current.staleAgeMinutes).toBeNull();

    const { result: badResult } = renderHook(() => useSnapshotStaleness("not-a-date"));
    expect(badResult.current.state).toBe("unknown");
    expect(badResult.current.isStale).toBe(true);
  });

  it("exposes a tick that advances with each re-evaluation", () => {
    const { result } = renderHook(() =>
      useSnapshotStaleness(new Date(Date.now() - 5 * 60_000).toISOString()),
    );
    const initialTick = result.current.tick;
    act(() => {
      vi.advanceTimersByTime(61_000);
    });
    expect(result.current.tick).toBeGreaterThan(initialTick);
  });

  it("does not tick while the snapshot is fresh", () => {
    // REL-048 / R-139: `tick` is in the returned memo, so an unconditional
    // interval re-rendered WorkspaceShell and every non-memoised child every
    // 30s in every open tab, with nothing to recompute. REL-061 / R-149 then
    // narrowed the exemption to FRESH: a blackout has to keep the retry
    // cadence alive, so it ticks.
    const { result } = renderHook(() =>
      useSnapshotStaleness(new Date(Date.now() - 1_000).toISOString()),
    );
    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    expect(result.current.tick).toBe(0);

    const { result: blackout } = renderHook(() => useSnapshotStaleness(null));
    act(() => {
      vi.advanceTimersByTime(90_000);
    });
    expect(blackout.current.tick).toBeGreaterThan(0);
  });
});
