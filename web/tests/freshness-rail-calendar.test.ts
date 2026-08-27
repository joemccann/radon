/**
 * R-305 / R-306 / R-307 / R-308 / R-309 (REL-107): the freshness rail earns
 * its colour.
 *
 * The rail decides "behind" by comparing the panel's `asOf` against
 * `lastCompletedSessionDate`, whose model of a session is weekday-only. It
 * therefore goes amber for a session the exchange will never print. It also
 * alarms during the window in which the writer is legitimately still running,
 * caps the overdue magnitude at one interval, and treats a null `asOf` as
 * "current" rather than unknown.
 */
import { describe, expect, it } from "vitest";

import { IV_RANK_REFRESH } from "@/lib/refreshSchedule";
import { computeFreshnessRail, WRITER_GRACE_MS } from "@/lib/freshnessRail";

describe("(a) a session the exchange never printed is not a missing sample", () => {
  it("does not go behind over Thanksgiving", () => {
    // 2026-11-26 is Thanksgiving (full closure). At 17:00 ET the day after
    // the previous session, a weekday-only model calls 2026-11-26 the last
    // completed session and marks a panel holding 2026-11-25 as behind.
    const thanksgivingEvening = new Date("2026-11-26T22:00:00Z");
    const rail = computeFreshnessRail(IV_RANK_REFRESH, "2026-11-25", thanksgivingEvening);
    expect(rail.behind).toBe(false);
    expect(rail.overdue).toBe(false);
  });

  it("does not go behind on Christmas Day", () => {
    const rail = computeFreshnessRail(
      IV_RANK_REFRESH,
      "2026-12-24",
      new Date("2026-12-25T22:00:00Z"),
    );
    expect(rail.behind).toBe(false);
  });

  it("still goes behind on a genuine missed weekday", () => {
    // 2026-08-26 is an ordinary Wednesday; a panel holding Monday IS behind.
    const rail = computeFreshnessRail(IV_RANK_REFRESH, "2026-08-24", new Date("2026-08-26T22:30:00Z"));
    expect(rail.behind).toBe(true);
  });
});

describe("(b) a missing data date is UNKNOWN, not current", () => {
  for (const [label, asOf] of [["null", null], ["undefined", undefined], ["empty", ""]] as const) {
    it(`reports ${label} as unknown`, () => {
      const rail = computeFreshnessRail(IV_RANK_REFRESH, asOf, new Date("2026-08-26T21:00:00Z"));
      expect(rail.unknown).toBe(true);
      // The original case asserted these three, and they still hold: an
      // unknown date must not crash the rail or invent a session.
      expect(rail.behind).toBe(false);
      expect(rail.awaitingSession).toBeNull();
      expect(rail.msRemaining).toBeGreaterThan(0);
    });
  }

  it("a real date is not unknown", () => {
    const rail = computeFreshnessRail(IV_RANK_REFRESH, "2026-08-26", new Date("2026-08-26T21:00:00Z"));
    expect(rail.unknown).toBe(false);
  });
});

describe("(c) the writer's own run window is not lateness", () => {
  // The timer carries RandomizedDelaySec=120, then the job has to run, and the
  // panel polls hourly. Five minutes past the slot is a job in flight.
  const JUST_PAST_SLOT = new Date("2026-08-27T22:15:00Z"); // slot + 5m
  const LONG_PAST_SLOT = new Date("2026-08-28T00:10:00Z"); // slot + 2h

  it("does not alarm five minutes past the slot", () => {
    const rail = computeFreshnessRail(IV_RANK_REFRESH, "2026-08-26", JUST_PAST_SLOT);
    expect(rail.overdue).toBe(false);
  });

  it("does alarm two hours past the slot", () => {
    const rail = computeFreshnessRail(IV_RANK_REFRESH, "2026-08-26", LONG_PAST_SLOT);
    expect(rail.overdue).toBe(true);
  });

  it("states the grace it applies rather than burying it", () => {
    // At least the RandomizedDelaySec=120 it is meant to absorb, and not so
    // wide that a genuinely dead writer stays calm all evening.
    expect(WRITER_GRACE_MS).toBeGreaterThan(2 * 60 * 1000);
    expect(WRITER_GRACE_MS).toBeLessThanOrEqual(2 * 60 * 60 * 1000);
  });
});

describe("(d) a long outage is distinguishable from a short one", () => {
  it("reports more than 24h overdue for a week-old reading", () => {
    // Past the writer grace, so the alarm is live and the magnitude is real.
    const rail = computeFreshnessRail(IV_RANK_REFRESH, "2026-08-20", new Date("2026-08-28T00:00:00Z"));
    expect(rail.overdue).toBe(true);
    expect(rail.msOverdue).toBeGreaterThan(24 * 60 * 60 * 1000);
  });

  it("still reports a small figure for a twenty-minute miss", () => {
    const short = computeFreshnessRail(IV_RANK_REFRESH, "2026-08-26", new Date("2026-08-28T00:10:00Z"));
    expect(short.msOverdue).toBeLessThan(24 * 60 * 60 * 1000);
  });
});
