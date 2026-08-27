/**
 * The freshness rail turns "is this reading current?" into two facts a trader
 * can act on: which session the panel is still missing, and how long until the
 * job that fills it runs. Both are derived — the schedule from the systemd
 * timer constants, the session from the ET clock — so neither can be a
 * hardcoded cadence string that drifts from the job that really runs.
 */
import { describe, expect, it } from "vitest";

import { IV_RANK_REFRESH } from "@/lib/refreshSchedule";
import { computeFreshnessRail, formatCountdown } from "@/lib/freshnessRail";

// 2026-08-26 is a Wednesday. The IV RANK timer fires 22:10 UTC = 18:10 ET.
const WED_1500_ET = new Date("2026-08-26T19:00:00Z"); // 15:00 ET, market open
const WED_1700_ET = new Date("2026-08-26T21:00:00Z"); // 17:00 ET, after the close
const WED_1830_ET = new Date("2026-08-26T22:30:00Z"); // 18:30 ET, past the slot

describe("computeFreshnessRail", () => {
  it("counts down to today's slot while it is still ahead", () => {
    const rail = computeFreshnessRail(IV_RANK_REFRESH, "2026-08-25", WED_1500_ET);
    expect(rail.nextSampleAt.toISOString()).toBe("2026-08-26T22:10:00.000Z");
    expect(rail.msRemaining).toBe(3 * 60 * 60 * 1000 + 10 * 60 * 1000);
    expect(rail.overdue).toBe(false);
  });

  it("rolls to tomorrow's slot once today's has passed", () => {
    const rail = computeFreshnessRail(IV_RANK_REFRESH, "2026-08-26", WED_1830_ET);
    expect(rail.nextSampleAt.toISOString()).toBe("2026-08-27T22:10:00.000Z");
    expect(rail.overdue).toBe(false);
  });

  it("names the session the panel is still missing", () => {
    // 17:00 ET: Wednesday's close has happened, so 2026-08-26 is a completed
    // session the panel does not have.
    const rail = computeFreshnessRail(IV_RANK_REFRESH, "2026-08-25", WED_1700_ET);
    expect(rail.behind).toBe(true);
    expect(rail.awaitingSession).toBe("2026-08-26");
  });

  it("reads as current intraday, when today's close does not exist yet", () => {
    // 15:00 ET Wednesday: the latest COMPLETED session is Tuesday, and the
    // panel has it. An EOD indicator is not stale for lacking a bar the market
    // has not printed.
    const rail = computeFreshnessRail(IV_RANK_REFRESH, "2026-08-25", WED_1500_ET);
    expect(rail.behind).toBe(false);
    expect(rail.awaitingSession).toBeNull();
  });

  // Was asserted at 18:30 ET — 20 minutes past the 18:10 slot. R-307 makes
  // that window the writer's own: RandomizedDelaySec=120 plus the job's
  // runtime plus the panel's hourly poll. Alarming inside it is a false page
  // every evening. The case keeps its intent — a run that fired and delivered
  // nothing IS overdue — measured from past the grace.
  it("flags overdue when the run has fired, delivered nothing, and the grace is spent", () => {
    const wellPastSlot = new Date("2026-08-26T23:30:00Z"); // 19:30 ET, slot + 80m
    const rail = computeFreshnessRail(IV_RANK_REFRESH, "2026-08-25", wellPastSlot);
    expect(rail.overdue).toBe(true);
    expect(rail.behind).toBe(true);
    expect(rail.msOverdue).toBe(80 * 60 * 1000);
  });

  it("is BEHIND but not yet alarming inside the writer's own run window", () => {
    const rail = computeFreshnessRail(IV_RANK_REFRESH, "2026-08-25", WED_1830_ET);
    expect(rail.behind).toBe(true);
    expect(rail.overdue).toBe(false);
  });

  it("fills the track with the elapsed share of the interval between runs", () => {
    // Six hours after the 22:10 UTC slot, a quarter of the 24h interval is gone.
    const sixHoursOn = new Date("2026-08-27T04:10:00Z");
    const rail = computeFreshnessRail(IV_RANK_REFRESH, "2026-08-26", sixHoursOn);
    expect(rail.elapsedFraction).toBeCloseTo(0.25, 6);
  });

  it("clamps the track to the interval at both ends", () => {
    const atSlot = new Date("2026-08-26T22:10:00Z");
    expect(computeFreshnessRail(IV_RANK_REFRESH, "2026-08-26", atSlot).elapsedFraction).toBe(0);
    // Past the R-307 writer grace, so this is a genuine late run.
    const overdue = computeFreshnessRail(
      IV_RANK_REFRESH, "2026-08-25", new Date("2026-08-26T23:30:00Z"),
    );
    expect(overdue.elapsedFraction).toBe(1);
  });

  it("survives a missing data date", () => {
    const rail = computeFreshnessRail(IV_RANK_REFRESH, null, WED_1700_ET);
    expect(rail.behind).toBe(false);
    expect(rail.awaitingSession).toBeNull();
    expect(rail.msRemaining).toBeGreaterThan(0);
  });
});

describe("formatCountdown", () => {
  it("drops seconds past an hour, where they are noise", () => {
    expect(formatCountdown(3 * 60 * 60 * 1000 + 10 * 60 * 1000 + 41_000)).toBe("3h 10m");
  });

  it("counts minutes and seconds inside the last hour", () => {
    expect(formatCountdown(47 * 60 * 1000 + 12_000)).toBe("47m 12s");
  });

  it("counts seconds alone inside the last minute", () => {
    expect(formatCountdown(12_000)).toBe("12s");
  });

  it("floors at zero rather than counting backwards", () => {
    expect(formatCountdown(-5_000)).toBe("0s");
  });
});
