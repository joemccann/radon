/**
 * TDD: computeRealizedPnlFromFills must only sum fills from TODAY (ET).
 *
 * Bug: orders.json persists between days — if IB Gateway session spans multiple
 * days (e.g., Gateway hasn't restarted, or last night's fills are still present),
 * executed_orders contains stale fills from previous days. Summing all of them
 * produces a large incorrect realized P&L figure (e.g. -$6,835) even when the
 * user made no trades today.
 *
 * Fix: filter fills to today's date in America/New_York before summing.
 */

import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import type { ExecutedOrder } from "../lib/types";
import { computeRealizedPnlFromFills } from "../lib/realized-pnl";

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

function makeFill(
  realizedPNL: number | null,
  time: string,
  symbol = "AAPL",
): ExecutedOrder {
  return {
    execId: `test-${Math.random()}`,
    symbol,
    contract: {
      symbol,
      secType: "STK",
      conId: null,
      strike: null,
      right: null,
      expiry: null,
    },
    side: "SLD",
    quantity: 100,
    avgPrice: 214.5,
    commission: -1.05,
    realizedPNL,
    time,
    exchange: "SMART",
  };
}

/** Fake "today" as 2026-03-09 ET by pinning Date to a known noon-ET moment. */
const FAKE_NOW_ET_DATE = "2026-03-09"; // ET date we pin to
// 2026-03-09T17:00:00Z = 2026-03-09T12:00:00 ET (noon)
const FAKE_NOW_UTC = "2026-03-09T17:00:00.000Z";

// ------------------------------------------------------------------
// Tests
// ------------------------------------------------------------------

describe("computeRealizedPnlFromFills — date filter", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(FAKE_NOW_UTC));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  test("returns 0 when all fills are from a previous day (stale orders.json)", () => {
    const fills = [
      makeFill(-6835.27, "2026-03-08T20:15:05+00:00", "ILF"), // yesterday UTC = yesterday ET
      makeFill(723.4, "2026-03-07T15:43:07+00:00", "BKD"),   // two days ago
    ];
    // Should be 0 — no today fills, not -6111.87
    expect(computeRealizedPnlFromFills(fills)).toBe(0);
  });

  test("sums only fills whose date is today in ET", () => {
    const fills = [
      // Today (2026-03-09) in ET
      makeFill(500, "2026-03-09T14:30:00-05:00", "AAPL"),   // 2:30 PM ET today
      makeFill(-200, "2026-03-09T17:00:00+00:00", "TSLA"), // noon ET today (UTC)
      // Yesterday
      makeFill(-6835.27, "2026-03-08T20:00:00+00:00", "ILF"), // yesterday ET
    ];
    expect(computeRealizedPnlFromFills(fills)).toBeCloseTo(300);
  });

  test("handles UTC timestamps that cross the ET midnight boundary", () => {
    // 2026 DST starts March 8 — ET on March 9 is EDT = UTC-4.
    // ET midnight on 2026-03-09 = 2026-03-09T04:00:00Z.
    // So 03:00Z = 23:00 EDT on March 8 (previous day) and 05:00Z = 01:00 EDT on March 9 (today).
    const fills = [
      makeFill(1000, "2026-03-09T03:00:00+00:00", "SPY"),  // still March 8 in ET (EDT)
      makeFill(200,  "2026-03-09T05:00:00+00:00", "SPY"),  // 01:00 EDT = March 9 in ET
    ];
    // Only the second fill is today (ET)
    expect(computeRealizedPnlFromFills(fills)).toBeCloseTo(200);
  });

  test("ISO strings without timezone offset are treated as UTC (ib_insync default)", () => {
    // ib_insync datetime.isoformat() without tz = UTC naive. Treat as UTC.
    // 2026-03-09T10:00:00 UTC = 2026-03-09T05:00:00 ET → today in ET
    // 2026-03-08T10:00:00 UTC = 2026-03-08T05:00:00 ET → yesterday in ET
    const fills = [
      makeFill(999, "2026-03-09T10:00:00", "MSFT"),   // today in ET (UTC naive)
      makeFill(1,   "2026-03-08T10:00:00", "MSFT"),   // yesterday in ET (UTC naive)
    ];
    expect(computeRealizedPnlFromFills(fills)).toBeCloseTo(999);
  });

  test("returns 0 for empty fills array regardless of date pinning", () => {
    expect(computeRealizedPnlFromFills([])).toBe(0);
  });

  test("includes fills from late in the ET trading day (near close)", () => {
    // 2026-03-09T20:59:00Z = 2026-03-09T15:59:00 ET
    const fills = [
      makeFill(750, "2026-03-09T20:59:00+00:00", "NVDA"),
    ];
    expect(computeRealizedPnlFromFills(fills)).toBeCloseTo(750);
  });
});
