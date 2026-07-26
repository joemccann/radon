/**
 * Weekend scan-storm regression (2026-07-26): isCriDataStale compared
 * data.date against calendar-today, so on Saturday Friday's payload was
 * permanently "stale" — every /api/regime poll fired a background FastAPI
 * /regime/scan (observed at a ~5s cadence for hours), each producing data
 * still dated Friday. Staleness-by-date must be session-aware: a payload
 * dated the last COMPLETED session is current on non-session days.
 */
import { describe, expect, it } from "vitest";
import { isCriDataStale } from "../lib/criStaleness";
import { lastCompletedSessionDate } from "../lib/marketSession";

function nextSaturday(from = new Date()): Date {
  const d = new Date(from);
  d.setDate(d.getDate() + ((6 - d.getDay() + 7) % 7 || 7));
  d.setHours(12, 0, 0, 0);
  return d;
}

describe("isCriDataStale — session-aware date check", () => {
  it("keeps the last completed session's payload fresh on a weekend", () => {
    const saturday = nextSaturday();
    const lastSession = lastCompletedSessionDate(saturday);
    const todayET = saturday.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
    const stale = isCriDataStale(
      { date: lastSession, market_open: false },
      Date.now(),
      todayET,
      false,
      lastSession,
    );
    expect(stale).toBe(false);
  });

  it("treats a market_open:true payload from the last session as final once closed", () => {
    const saturday = nextSaturday();
    const lastSession = lastCompletedSessionDate(saturday);
    const todayET = saturday.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
    const stale = isCriDataStale(
      { date: lastSession, market_open: true },
      Date.now() - 3_600_000,
      todayET,
      false,
      lastSession,
    );
    expect(stale).toBe(false);
  });

  it("flags a payload older than the last completed session", () => {
    const saturday = nextSaturday();
    const lastSession = lastCompletedSessionDate(saturday);
    const todayET = saturday.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
    const stale = isCriDataStale(
      { date: "2020-01-02", market_open: false },
      Date.now(),
      todayET,
      false,
      lastSession,
    );
    expect(stale).toBe(true);
  });
});
