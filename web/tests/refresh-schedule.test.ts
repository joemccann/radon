/**
 * Refresh-schedule helper: the single frontend source of truth for the
 * Equibles indicator timers. Cadence copy is never hardcoded in panels —
 * it is derived from these constants, and these constants are pinned to
 * the actual systemd OnCalendar lines in cloud/services/ so they cannot
 * silently drift from the schedule that really runs.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";

import {
  ATS_VENUE_SHARE_REFRESH,
  COT_POSITIONING_REFRESH,
  SHORT_CROWDING_REFRESH,
  dataAgeDays,
  nextRefreshLabel,
  nextRefreshUtc,
  type UtcSchedule,
} from "@/lib/refreshSchedule";

const TEST_DIR = fileURLToPath(new URL(".", import.meta.url));

function timerOnCalendar(unit: string): string {
  const source = readFileSync(join(TEST_DIR, "../../cloud/services", unit), "utf-8");
  return source.match(/^OnCalendar=(.+)$/m)?.[1].trim() ?? "";
}

const WEEKDAY_TOKENS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function expectedOnCalendar(schedule: UtcSchedule): string {
  const hh = String(schedule.hourUtc).padStart(2, "0");
  const mm = String(schedule.minuteUtc).padStart(2, "0");
  const time = `*-*-* ${hh}:${mm}:00 UTC`;
  return schedule.cadence === "weekly"
    ? `${WEEKDAY_TOKENS[schedule.weekdayUtc]} ${time}`
    : time;
}

describe("schedule constants are pinned to the systemd timers", () => {
  it("ATS venue share mirrors radon-equibles-ats.timer", () => {
    expect(expectedOnCalendar(ATS_VENUE_SHARE_REFRESH)).toBe(
      timerOnCalendar("radon-equibles-ats.timer"),
    );
  });

  it("short crowding mirrors radon-equibles-short-crowding.timer", () => {
    expect(expectedOnCalendar(SHORT_CROWDING_REFRESH)).toBe(
      timerOnCalendar("radon-equibles-short-crowding.timer"),
    );
  });

  it("COT positioning mirrors radon-equibles-cot.timer", () => {
    expect(expectedOnCalendar(COT_POSITIONING_REFRESH)).toBe(
      timerOnCalendar("radon-equibles-cot.timer"),
    );
  });
});

describe("nextRefreshUtc", () => {
  it("daily: later today when the slot has not passed yet", () => {
    const from = new Date(Date.UTC(2026, 7, 23, 8, 0));
    expect(nextRefreshUtc(SHORT_CROWDING_REFRESH, from).toISOString()).toBe(
      "2026-08-23T09:30:00.000Z",
    );
  });

  it("daily: tomorrow once the slot has passed", () => {
    const from = new Date(Date.UTC(2026, 7, 23, 9, 30));
    expect(nextRefreshUtc(SHORT_CROWDING_REFRESH, from).toISOString()).toBe(
      "2026-08-24T09:30:00.000Z",
    );
  });

  it("weekly: the coming weekday", () => {
    // 2026-08-23 is a Sunday; next Tuesday slot is 08-25 09:15 UTC.
    const from = new Date(Date.UTC(2026, 7, 23, 12, 0));
    expect(nextRefreshUtc(ATS_VENUE_SHARE_REFRESH, from).toISOString()).toBe(
      "2026-08-25T09:15:00.000Z",
    );
  });

  it("weekly: rolls a full week once the weekday slot has passed", () => {
    // Saturday 01:00 UTC exactly at the slot rolls to the next Saturday.
    const from = new Date(Date.UTC(2026, 7, 22, 1, 0));
    expect(nextRefreshUtc(COT_POSITIONING_REFRESH, from).toISOString()).toBe(
      "2026-08-29T01:00:00.000Z",
    );
  });
});

describe("nextRefreshLabel", () => {
  it("names the weekday, date and UTC time", () => {
    const from = new Date(Date.UTC(2026, 7, 23, 12, 0));
    expect(nextRefreshLabel(ATS_VENUE_SHARE_REFRESH, from)).toBe("Tue 2026-08-25 09:15 UTC");
    expect(nextRefreshLabel(SHORT_CROWDING_REFRESH, from)).toBe("Mon 2026-08-24 09:30 UTC");
  });
});

describe("dataAgeDays", () => {
  it("counts whole UTC days since the data date", () => {
    const from = new Date(Date.UTC(2026, 7, 23, 12, 0));
    expect(dataAgeDays("2026-08-18", from)).toBe(5);
    expect(dataAgeDays("2026-07-13", from)).toBe(41);
  });

  it("returns null for missing or unparseable dates", () => {
    expect(dataAgeDays(null)).toBeNull();
    expect(dataAgeDays(undefined)).toBeNull();
    expect(dataAgeDays("not-a-date")).toBeNull();
  });
});
