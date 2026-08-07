/**
 * Holiday-aware market state for the staleness gate (2026-07-03).
 *
 * getMarketStateFromDate ignored holidays, so an observed-holiday weekday
 * (e.g. Fri 2026-07-03, July-4th observed) computed as "open" during
 * 09:30-16:00 ET and applied the tight open windows to market-hours scan
 * writers that correctly did not run — the footer showed 6-7 stale rows of
 * pure noise. The scripts side already treats the static holiday table
 * (scripts/config/market_holidays.json) as SoT; the web mirror must agree.
 */
import { describe, it, expect } from "vitest";
import {
  getMarketStateFromDate,
  getMarketPhaseFromDate,
  getFreshnessWindowMs,
  getServiceCategory,
} from "../lib/serviceHealthWindows";
import holidays from "../../scripts/config/market_holidays.json";

function etDate(iso: string): Date {
  return new Date(iso);
}

describe("holiday awareness", () => {
  it("mid-session on an observed holiday is closed, not open", () => {
    // Fri 2026-07-03 15:00 UTC = 11:00 ET, a normal trading hour on a
    // non-holiday Friday.
    expect(getMarketStateFromDate(etDate("2026-07-03T15:00:00Z"))).toBe("closed");
  });

  it("the same wall-clock time on a regular trading day is open", () => {
    // Thu 2026-07-02 15:00 UTC = 11:00 ET.
    expect(getMarketStateFromDate(etDate("2026-07-02T15:00:00Z"))).toBe("open");
  });

  it("extended hours on a holiday are closed too (writers gate on RTH calendar)", () => {
    // 12:00 UTC = 08:00 ET — would be "extended" on a trading day.
    expect(getMarketStateFromDate(etDate("2026-07-03T12:00:00Z"))).toBe("closed");
    expect(getMarketStateFromDate(etDate("2026-07-02T12:00:00Z"))).toBe("extended");
  });

  it("market phase mirrors the holiday gate", () => {
    expect(getMarketPhaseFromDate(etDate("2026-07-03T15:00:00Z"))).toBe("closed");
    expect(getMarketPhaseFromDate(etDate("2026-07-02T15:00:00Z"))).toBe("open");
  });

  it("catalysts is registered with intraday and weekend-bridging windows", () => {
    // radon-catalysts.timer fires at 06:30, 10:00, and 16:00 ET and
    // heartbeats on holiday skips; closed still bridges Fri→Mon.
    expect(getServiceCategory("catalysts")).toBe("scheduled");
    expect(getFreshnessWindowMs("catalysts", "open")).toBe(7 * 60 * 60_000);
    expect(getFreshnessWindowMs("catalysts", "closed")).toBeGreaterThanOrEqual(3 * 24 * 60 * 60_000);
  });

  it("every static-table year covers Christmas (sanity: table wired, not a stub)", () => {
    for (const [year, dates] of Object.entries(holidays)) {
      expect(dates.some((d: string) => d.endsWith("-12-25") || d.endsWith("-12-24")),
        `year ${year} missing Christmas`).toBe(true);
      expect(getMarketStateFromDate(new Date(`${year}-12-25T15:00:00Z`))).toBe("closed");
    }
  });
});
