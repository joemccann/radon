import type { ExecutedOrder } from "./types";

/**
 * Today's date in America/New_York (ET) as YYYY-MM-DD.
 * Identical pattern to `todayET()` used in app/api/regime/route.ts.
 */
function todayET(): string {
  return new Date().toLocaleDateString("sv", { timeZone: "America/New_York" });
}

/**
 * Convert an IB fill timestamp string to a YYYY-MM-DD date in ET.
 *
 * IB (via ib_insync) always emits ISO-8601 strings.  Two forms occur in
 * practice:
 *   • With explicit UTC offset  e.g. "2026-03-09T15:43:07+00:00"
 *   • Without timezone (UTC-naive from Python datetime.isoformat())
 *     e.g. "2026-03-09T15:43:07"  — treat as UTC.
 *
 * We parse to a proper Date and then project to ET to get the correct trading
 * calendar date.  A fill at 2026-03-09T04:00:00Z is still 2026-03-08 in ET
 * and must NOT be counted as today if today is 2026-03-09.
 */
function fillDateET(time: string): string {
  // If the string has no timezone designator (no Z, no +HH:MM, no -HH:MM at
  // the end), append "Z" so JavaScript treats it as UTC rather than local time.
  const hasTimezone = /[Z+\-]\d{2}:?\d{2}$/.test(time) || time.endsWith("Z");
  const normalised = hasTimezone ? time : time + "Z";
  return new Date(normalised).toLocaleDateString("sv", {
    timeZone: "America/New_York",
  });
}

/**
 * Compute today's realized P&L as the sum of realizedPNL across fills that
 * occurred **today in ET**.
 *
 * Why filter by date?
 * IB Gateway's session and ib_insync's fills() cache can span multiple
 * calendar days between the nightly auto-restart. Turso's executed-order rows
 * intentionally persist across sessions. When the user opens the dashboard on
 * a new day, stale fills
 * from previous sessions are still present and produce a large, incorrect
 * realized P&L (e.g. -$6,835) even when no trades have been made today.
 *
 * IB's account_summary.realized_pnl (from reqPnL) has the same problem and
 * also includes non-order events (option expiry, adjustments, etc.).  Using
 * fill-level data filtered to today is the canonical approach.
 */
export function computeRealizedPnlFromFills(fills: ExecutedOrder[]): number {
  if (fills.length === 0) return 0;
  const today = todayET();
  return fills
    .filter((fill) => fillDateET(fill.time) === today)
    .reduce((sum, fill) => sum + (fill.realizedPNL ?? 0), 0);
}
