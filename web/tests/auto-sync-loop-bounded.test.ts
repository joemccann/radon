/**
 * @vitest-environment jsdom
 *
 * REL-048 / R-105, R-106, R-107 (P1), R-139 (P2) — the auto-sync loop closes.
 *
 * R-105: a FAILED producer sync returns HTTP 200. `POST /api/portfolio`
 * catches the FastAPI failure, re-serves the Turso snapshot with
 * `X-Sync-Warning`, and `usePortfolio` sets `lastSync` to the UNCHANGED
 * stale timestamp. `isStale` never clears, the 30s self-tick bumps `tick`,
 * the 60s cooldown expires, the effect re-fires: one `/portfolio/sync`
 * (reqPositions / reqAccountSummary / PnL subscriptions) per minute per tab,
 * 24/7, with no backoff and no consecutive-failure cap. ~1440 gateway
 * connect attempts per day per tab across a weekend or a 2FA window, and the
 * cooldown map is a per-instance `useRef` so every route remount starts empty.
 *
 * R-107: the daily-P&L gate reads the WALL CLOCK, never `portfolio.last_sync`.
 * With the producer's last success on Saturday — exactly the state R-105
 * leaves behind — Monday 08:00 ET renders the Saturday-captured phantom
 * daily P&L labelled TODAY.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

import {
  resetAutoSyncCooldowns,
  useAutoSyncOnStale,
} from "../lib/useAutoSyncOnStale";
import { useSnapshotStaleness } from "../lib/useSnapshotStaleness";
import { deriveKpis } from "../lib/dashboardKpis";
import type { PortfolioData } from "../lib/types";

beforeEach(() => {
  vi.useFakeTimers();
  // The cooldown is module scope on purpose (R-105) — every remount and
  // every tab shares one window — so each case has to clear it explicitly.
  resetAutoSyncCooldowns();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useAutoSyncOnStale backs off a failing producer", () => {
  it("does not fire once per cooldown forever when the sync never clears staleness", () => {
    const syncNow = vi.fn();
    const { rerender } = renderHook(
      ({ tick }) => useAutoSyncOnStale(true, syncNow, "portfolio", true, tick),
      { initialProps: { tick: 0 } },
    );

    // 10 minutes of 30s staleness ticks with the snapshot never refreshing.
    for (let i = 1; i <= 20; i += 1) {
      act(() => {
        vi.advanceTimersByTime(30_000);
      });
      rerender({ tick: i });
    }

    expect(syncNow.mock.calls.length).toBeLessThanOrEqual(5);
    expect(syncNow.mock.calls.length).toBeGreaterThan(0);
  });

  it("resets the backoff once a sync actually refreshes the snapshot", () => {
    const syncNow = vi.fn();
    const { rerender } = renderHook(
      ({ tick, stale }) => useAutoSyncOnStale(stale, syncNow, "portfolio", true, tick),
      { initialProps: { tick: 0, stale: true } },
    );
    for (let i = 1; i <= 12; i += 1) {
      act(() => {
        vi.advanceTimersByTime(30_000);
      });
      rerender({ tick: i, stale: true });
    }
    const backedOff = syncNow.mock.calls.length;

    rerender({ tick: 13, stale: false }); // producer succeeded
    act(() => {
      vi.advanceTimersByTime(120_000);
    });
    rerender({ tick: 14, stale: true }); // stale again later

    expect(syncNow.mock.calls.length).toBe(backedOff + 1);
  });

  it("shares the cooldown across remounts of the same target", () => {
    // R-105: the map used to be a per-WorkspaceShell useRef, so every route
    // remount started empty and re-fired immediately.
    const syncNow = vi.fn();
    const first = renderHook(() =>
      useAutoSyncOnStale(true, syncNow, "portfolio", true, 0),
    );
    expect(syncNow).toHaveBeenCalledTimes(1);
    first.unmount();

    renderHook(() => useAutoSyncOnStale(true, syncNow, "portfolio", true, 0));
    expect(syncNow).toHaveBeenCalledTimes(1);
  });
});

describe("useSnapshotStaleness does not tick when there is nothing to age", () => {
  it("holds the tick at 0 while lastSync is null", () => {
    const { result } = renderHook(() => useSnapshotStaleness(null));
    act(() => {
      vi.advanceTimersByTime(5 * 60_000);
    });
    expect(result.current.tick).toBe(0);
  });

  it("still ticks once a snapshot exists", () => {
    const { result } = renderHook(() =>
      useSnapshotStaleness(new Date(Date.now() - 5 * 60_000).toISOString()),
    );
    act(() => {
      vi.advanceTimersByTime(90_000);
    });
    expect(result.current.tick).toBeGreaterThan(0);
  });
});

describe("the daily P&L gate reads the snapshot date, not the wall clock", () => {
  const portfolio = (lastSync: string): PortfolioData =>
    ({
      positions: [],
      account_summary: { net_liquidation: 500_000, daily_pnl: 13_951.76 },
      last_sync: lastSync,
    }) as unknown as PortfolioData;

  it("blanks a Saturday-captured daily P&L opened on Monday morning", () => {
    const cells = deriveKpis(
      portfolio("2026-08-22T20:00:00Z"),
      0,
      new Date("2026-08-24T12:00:00Z"),
    );
    const today = cells.find((c) => c.key === "todayPnl");
    expect(today?.value).toBeNull();
  });

  it("keeps a same-session daily P&L", () => {
    const cells = deriveKpis(
      portfolio("2026-08-24T14:00:00Z"),
      0,
      new Date("2026-08-24T15:00:00Z"),
    );
    const today = cells.find((c) => c.key === "todayPnl");
    expect(today?.value).toBe(13_951.76);
  });
});
