/**
 * Read-time catalyst recompute + past-event filter (web/lib/catalystUpcoming.ts).
 *
 * The catalysts writer legitimately does not run on weekends/holidays, so the
 * stored `days_until` in the snapshot is days old by Sunday. These tests pin
 * the read-time semantics with a frozen-clock matrix:
 *  - past events never survive the filter (Sunday/Saturday viewing last week)
 *  - stored days_until is advisory only — always recomputed from `date`
 *  - a row dated a closed "today" (weekend/holiday) is provider noise and is
 *    remapped to its next trading session, never 0 ("Today")
 *  - same-day events stay after event_time until 20:00 ET, then age out
 *  - date-only strings never day-shift west of UTC (formatTradeDate hazard)
 *
 * All `now` instants are explicit UTC — no wall-clock dependence on the host.
 */
import { describe, it, expect } from "vitest";

import { daysUntilEt, upcomingCatalysts } from "@/lib/catalystUpcoming";

// Frozen-clock matrix instants (all deliberate ET wall-clock times).
const SUNDAY_AFTER_JULY4 = new Date("2026-07-05T19:00:00Z"); // Sun 15:00 ET
const SATURDAY_JULY4 = new Date("2026-07-04T16:00:00Z"); // Sat 12:00 ET
const MONDAY_REGULAR = new Date("2026-07-06T16:00:00Z"); // Mon 12:00 ET (trading day)
const MONDAY_LABOR_DAY = new Date("2026-09-07T16:00:00Z"); // Mon 12:00 ET (holiday)
const FRIDAY_LATE_EVENING = new Date("2026-07-11T01:00:00Z"); // Fri 2026-07-10 21:00 ET (Sat in UTC)

function row(date: string, storedDaysUntil: number, title = date, eventTime?: string) {
  return { ticker: null, type: "economic", title, date, source: "economic", days_until: storedDaysUntil, event_time: eventTime };
}

describe("daysUntilEt", () => {
  it("computes whole-day distance from the ET calendar day", () => {
    expect(daysUntilEt("2026-07-07", SUNDAY_AFTER_JULY4)).toBe(2);
    expect(daysUntilEt("2026-07-05", SUNDAY_AFTER_JULY4)).toBe(0);
    expect(daysUntilEt("2026-07-02", SUNDAY_AFTER_JULY4)).toBe(-3);
  });

  it("never day-shifts a date-only string west of UTC", () => {
    // 2026-07-06T02:00:00Z is still Sunday 2026-07-05 22:00 in ET.
    const lateSundayEt = new Date("2026-07-06T02:00:00Z");
    expect(daysUntilEt("2026-07-06", lateSundayEt)).toBe(1);
    expect(daysUntilEt("2026-07-05", lateSundayEt)).toBe(0);
  });
});

describe("upcomingCatalysts", () => {
  it("drops last week's events on Sunday (the frozen-fossil scenario)", () => {
    const fossil = [row("2026-07-02", 0), row("2026-07-02", 0), row("2026-07-03", 0)];
    expect(upcomingCatalysts(fossil, SUNDAY_AFTER_JULY4)).toEqual([]);
  });

  it("drops Friday events on Saturday", () => {
    expect(upcomingCatalysts([row("2026-07-03", 0)], SATURDAY_JULY4)).toEqual([]);
  });

  it("drops Friday events on a Monday holiday (Labor Day)", () => {
    expect(upcomingCatalysts([row("2026-09-04", 0)], MONDAY_LABOR_DAY)).toEqual([]);
  });

  it("keeps a Monday event on a regular Monday at days_until 0 (Today allowed)", () => {
    const kept = upcomingCatalysts([row("2026-07-06", 0)], MONDAY_REGULAR);
    expect(kept).toHaveLength(1);
    expect(kept[0].days_until).toBe(0);
  });

  it("recomputes days_until from the date, ignoring the stored value", () => {
    // Tuesday event carrying a stale stored 0 must read 2d on Sunday, not Today.
    const kept = upcomingCatalysts([row("2026-07-07", 0)], SUNDAY_AFTER_JULY4);
    expect(kept).toHaveLength(1);
    expect(kept[0].days_until).toBe(2);
  });

  it("remaps a row dated a closed today (Sunday) to its next trading session, never 0", () => {
    const kept = upcomingCatalysts([row("2026-07-05", 0)], SUNDAY_AFTER_JULY4);
    expect(kept).toHaveLength(1);
    expect(kept[0].days_until).toBe(1); // Monday 2026-07-06
  });

  it("remaps a Saturday-dated row across the weekend to Monday", () => {
    const kept = upcomingCatalysts([row("2026-07-04", 0)], SATURDAY_JULY4);
    expect(kept).toHaveLength(1);
    expect(kept[0].days_until).toBe(2); // skips Sunday
  });

  it("remaps a holiday-dated row to the day after the holiday", () => {
    const kept = upcomingCatalysts([row("2026-09-07", 0)], MONDAY_LABOR_DAY);
    expect(kept).toHaveLength(1);
    expect(kept[0].days_until).toBe(1); // Tuesday 2026-09-08
  });

  it("ages out a same-day event once the extended session has ended (Friday 21:00 ET)", () => {
    expect(upcomingCatalysts([row("2026-07-10", 0)], FRIDAY_LATE_EVENING)).toEqual([]);
  });

  it("keeps an 08:30 ET print at 17:59 ET the same day so the actual can render", () => {
    const friday1759Et = new Date("2026-08-07T21:59:00Z");
    const kept = upcomingCatalysts(
      [row("2026-08-07", 0, "Employment report", "2026-08-07T12:30:00Z")],
      friday1759Et,
    );
    expect(kept).toHaveLength(1);
    expect(kept[0].title).toBe("Employment report");
  });

  it("drops same-day rows after 20:00 ET even if they already printed", () => {
    const friday2100Et = new Date("2026-08-08T01:00:00Z");
    expect(
      upcomingCatalysts(
        [row("2026-08-07", 0, "Employment report", "2026-08-07T12:30:00Z")],
        friday2100Et,
      ),
    ).toEqual([]);
  });

  it("keeps future events late Friday evening with ET-correct distance (west-of-UTC)", () => {
    // 21:00 ET Friday is already Saturday in UTC; ET today must stay 07-10.
    const kept = upcomingCatalysts([row("2026-07-14", 0)], FRIDAY_LATE_EVENING);
    expect(kept).toHaveLength(1);
    expect(kept[0].days_until).toBe(4);
  });

  it("sorts by recomputed days_until ascending", () => {
    const kept = upcomingCatalysts(
      [row("2026-07-09", 0), row("2026-07-05", 0), row("2026-07-07", 9)],
      SUNDAY_AFTER_JULY4,
    );
    expect(kept.map((r) => r.days_until)).toEqual([1, 2, 4]);
  });

  it("drops rows with unparseable dates", () => {
    expect(upcomingCatalysts([row("not-a-date", 0)], MONDAY_REGULAR)).toEqual([]);
  });
});
