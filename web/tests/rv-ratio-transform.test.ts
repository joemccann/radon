/**
 * @vitest-environment node
 *
 * web/lib/rvRatio.ts — payload guard accept/reject matrix, entry zipping,
 * regime badge mapping, and the session-relative currency check.
 *
 * Dates are window-relative (derived from the real clock via the fixture
 * helpers); `isSnapshotCurrent` cases construct explicit `now` instants at
 * 17:00Z (intraday: after the open, before the 16:00 ET close), 22:00Z
 * (after the close) and 10:00Z (pre-open).
 */
import { describe, expect, it } from "vitest";

import {
  classifyRatioRegime,
  isRvRatioMissingPayload,
  isRvRatioPayload,
  isSnapshotCurrent,
  zipRvRatioEntries,
  RV_RATIO_SCAN_TIMEOUT_MS,
  type RvRatioDivergence,
} from "@/lib/rvRatio";
import {
  addCalendarDays,
  buildCompleteRvRatioFixture,
  buildMissingRvRatioPayload,
  buildPartialRvRatioFixture,
  previousWeekday,
  weekdayOf,
  RV_RATIO_COMPLETE_SESSIONS,
  RV_RATIO_PARTIAL_SESSIONS,
} from "./rv-ratio-fixture";

const todayEt = new Date().toLocaleDateString("sv", { timeZone: "America/New_York" });

function mostRecent(weekday: number): string {
  let d = todayEt;
  while (weekdayOf(d) !== weekday) d = addCalendarDays(d, -1);
  return d;
}

/** A `now` at 17:00Z: intraday — after the 09:30 ET open, before the 16:00
 * ET close, same ET calendar date. */
function afterOpen(dateStr: string): Date {
  return new Date(`${dateStr}T17:00:00Z`);
}

/** A `now` at 22:00Z: after the 16:00 ET close, same ET calendar date. */
function afterClose(dateStr: string): Date {
  return new Date(`${dateStr}T22:00:00Z`);
}

/** A `now` at 10:00Z: 05:00/06:00 ET, before the open, same ET calendar date. */
function preOpen(dateStr: string): Date {
  return new Date(`${dateStr}T10:00:00Z`);
}

describe("isRvRatioPayload", () => {
  it("accepts the complete ~10y fixture", () => {
    const payload = buildCompleteRvRatioFixture();
    expect(payload.coverage.sessions).toBe(RV_RATIO_COMPLETE_SESSIONS);
    expect(payload.coverage.complete).toBe(true);
    expect(isRvRatioPayload(payload)).toBe(true);
  });

  it("accepts the ~300-session partial fixture, with or without the route's stale flag", () => {
    const payload = buildPartialRvRatioFixture();
    expect(payload.coverage.sessions).toBe(RV_RATIO_PARTIAL_SESSIONS);
    expect(payload.coverage.complete).toBe(false);
    expect(isRvRatioPayload(payload)).toBe(true);
    expect(isRvRatioPayload({ ...payload, stale: true })).toBe(true);
  });

  it.each([
    ["null", null],
    ["a number", 42],
    ["an empty object", {}],
    ["the missing variant", buildMissingRvRatioPayload()],
    ["a cooldown response", { status: "cooldown", retry_in: 412 }],
  ])("rejects %s", (_label, value) => {
    expect(isRvRatioPayload(value)).toBe(false);
  });

  it("rejects mismatched parallel array lengths", () => {
    const payload = buildPartialRvRatioFixture();
    expect(isRvRatioPayload({ ...payload, ratio: payload.ratio.slice(0, -1) })).toBe(false);
    expect(isRvRatioPayload({ ...payload, asset_rv: payload.asset_rv.slice(0, -1) })).toBe(false);
  });

  it("rejects non-finite series values and unknown divergence regimes", () => {
    const payload = buildPartialRvRatioFixture();
    const poisonedRatio = [...payload.ratio];
    poisonedRatio[3] = "x" as unknown as number;
    expect(isRvRatioPayload({ ...payload, ratio: poisonedRatio })).toBe(false);
    expect(
      isRvRatioPayload({ ...payload, stats: { ...payload.stats, divergence: "sideways" } }),
    ).toBe(false);
  });

  it("rejects payloads missing stats or coverage", () => {
    const { stats: _stats, ...withoutStats } = buildPartialRvRatioFixture();
    const { coverage: _coverage, ...withoutCoverage } = buildPartialRvRatioFixture();
    expect(isRvRatioPayload(withoutStats)).toBe(false);
    expect(isRvRatioPayload(withoutCoverage)).toBe(false);
  });
});

describe("isRvRatioMissingPayload", () => {
  it("identifies the missing variant and nothing else", () => {
    expect(isRvRatioMissingPayload(buildMissingRvRatioPayload())).toBe(true);
    expect(isRvRatioMissingPayload({ schema_version: 1, symbol: "SMH", missing: true })).toBe(true);
    expect(isRvRatioMissingPayload(buildPartialRvRatioFixture())).toBe(false);
    expect(isRvRatioMissingPayload(null)).toBe(false);
    expect(isRvRatioMissingPayload({ missing: true })).toBe(false);
  });
});

describe("zipRvRatioEntries", () => {
  it("zips the parallel arrays into per-session entries in order", () => {
    const payload = buildPartialRvRatioFixture();
    const entries = zipRvRatioEntries(payload);

    expect(entries).toHaveLength(payload.dates.length);
    expect(entries[0]).toEqual({
      date: payload.dates[0],
      ratio: payload.ratio[0],
      assetRv: payload.asset_rv[0],
      benchmarkRv: payload.benchmark_rv[0],
    });
    const last = entries[entries.length - 1];
    expect(last.date).toBe(payload.coverage.last_date);
    expect(last.ratio).toBe(payload.stats.last);
  });
});

describe("classifyRatioRegime", () => {
  it.each<[RvRatioDivergence, string, string]>([
    ["in_band", "IN BAND", "neutral"],
    ["elevated", "ELEVATED", "caution"],
    ["decoupled", "DECOUPLED", "dislocation"],
    ["compressed", "COMPRESSED", "neutral-low"],
  ])("maps %s to %s / %s", (divergence, label, tone) => {
    expect(classifyRatioRegime({ divergence })).toEqual({ label, tone });
  });
});

describe("isSnapshotCurrent", () => {
  const wednesday = mostRecent(3);
  const tuesday = previousWeekday(wednesday);
  const saturday = mostRecent(6);
  const fridayBeforeSaturday = previousWeekday(saturday);

  it("accepts the previous session intraday (today's close does not exist yet)", () => {
    expect(isSnapshotCurrent(tuesday, afterOpen(wednesday))).toBe(true);
    expect(isSnapshotCurrent(previousWeekday(tuesday), afterOpen(wednesday))).toBe(false);
  });

  it("requires the same session only after the 16:00 ET close", () => {
    expect(isSnapshotCurrent(wednesday, afterClose(wednesday))).toBe(true);
    expect(isSnapshotCurrent(tuesday, afterClose(wednesday))).toBe(false);
  });

  it("accepts the previous session before the open on a weekday", () => {
    expect(isSnapshotCurrent(tuesday, preOpen(wednesday))).toBe(true);
    expect(isSnapshotCurrent(previousWeekday(tuesday), preOpen(wednesday))).toBe(false);
  });

  it("treats Friday's snapshot as current all weekend (no wall-clock mislabel)", () => {
    expect(isSnapshotCurrent(fridayBeforeSaturday, afterOpen(saturday))).toBe(true);
    expect(
      isSnapshotCurrent(addCalendarDays(fridayBeforeSaturday, -7), afterOpen(saturday)),
    ).toBe(false);
  });

  it("is never current without a last date", () => {
    expect(isSnapshotCurrent(null)).toBe(false);
    expect(isSnapshotCurrent(undefined)).toBe(false);
    expect(isSnapshotCurrent("")).toBe(false);
  });
});

describe("scan timeout constant", () => {
  it("pins the shared 250s scan budget", () => {
    expect(RV_RATIO_SCAN_TIMEOUT_MS).toBe(250_000);
  });
});
