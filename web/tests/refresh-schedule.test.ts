/**
 * Refresh-schedule helper: the single frontend source of truth for indicator
 * timers. Cadence copy is never hardcoded in panels — it is derived from these
 * constants, and these constants are pinned to the actual systemd OnCalendar
 * lines in cloud/services/ by parsing the unit files, so they cannot silently
 * drift from the schedule that really runs.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";

import {
  ATS_VENUE_SHARE_REFRESH,
  BPI_REFRESH,
  BREADTH_REFRESH,
  COR_REFRESH,
  COT_POSITIONING_REFRESH,
  CREDIT_SPREAD_REFRESH,
  CRI_REFRESH,
  DATA_REFRESH,
  DISPERSION_REFRESH,
  DIV_YIELD_REFRESH,
  GEX_REFRESH,
  HH_LEV_REFRESH,
  HY_AD_REFRESH,
  IEI_HYG_REFRESH,
  IV_RANK_REFRESH,
  IV_SPREAD_REFRESH,
  MARGIN_DEBT_REFRESH,
  MA_RATIO_REFRESH,
  SHORT_CROWDING_REFRESH,
  SKEW2D_REFRESH,
  SKEW_REFRESH,
  STRADDLE_REFRESH,
  TRIN_REFRESH,
  VCG_REFRESH,
  VIXCOR_REFRESH,
  VIXTS_REFRESH,
  YIELD_CURVE_REFRESH,
  dataAgeDays,
  firesWeekly,
  nextRefreshLabel,
  nextRefreshUtc,
  previousRefreshUtc,
  type RefreshRule,
  type RefreshSchedule,
} from "@/lib/refreshSchedule";

const TEST_DIR = fileURLToPath(new URL(".", import.meta.url));

const WEEKDAY_TOKENS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function expandRange(token: string): number[] {
  const [from, to = from] = token.split("..").map(Number);
  return Array.from({ length: to - from + 1 }, (_, i) => from + i);
}

function parseWeekdays(token: string): number[] {
  const [from, to = from] = token.split("..").map((t) => WEEKDAY_TOKENS.indexOf(t));
  return Array.from({ length: to - from + 1 }, (_, i) => from + i);
}

/**
 * The OnCalendar subset radon's timers use:
 *   [Mon..Fri] [*-*-*] HH[..HH]:MM[,MM...][:SS] [UTC|America/New_York]
 */
function parseOnCalendar(line: string): RefreshRule {
  const tokens = line.trim().split(/\s+/);
  const rule: { tz?: RefreshRule["tz"]; weekdays?: number[]; hours: number[]; minutes: number[] } = {
    hours: [],
    minutes: [],
  };
  for (const token of tokens) {
    if (token === "UTC" || token === "America/New_York") rule.tz = token;
    else if (/^[A-Z][a-z]{2}(\.\.[A-Z][a-z]{2})?$/.test(token)) rule.weekdays = parseWeekdays(token);
    else if (token === "*-*-*") continue;
    else {
      const [hours, minutes] = token.split(":");
      rule.hours = expandRange(hours);
      rule.minutes = minutes.split(",").flatMap(expandRange);
    }
  }
  if (rule.tz === "UTC") delete rule.tz;
  return rule;
}

function timerRules(unit: string): RefreshSchedule {
  const source = readFileSync(join(TEST_DIR, "../../cloud/services", unit), "utf-8");
  const lines = source.match(/^OnCalendar=(.+)$/gm) ?? [];
  return lines.map((line) => parseOnCalendar(line.replace(/^OnCalendar=/, "")));
}

/** Canonical form: `tz` omitted for UTC, `weekdays` omitted for every day. */
function canonical(schedule: RefreshSchedule): RefreshRule[] {
  return schedule.map((rule) => ({
    ...(rule.tz && rule.tz !== "UTC" ? { tz: rule.tz } : {}),
    ...(rule.weekdays ? { weekdays: [...rule.weekdays] } : {}),
    hours: [...rule.hours],
    minutes: [...rule.minutes],
  }));
}

const PINNED: Array<[string, RefreshSchedule, string[]]> = [
  ["ATS venue share", ATS_VENUE_SHARE_REFRESH, ["radon-equibles-ats.timer"]],
  ["short crowding", SHORT_CROWDING_REFRESH, ["radon-equibles-short-crowding.timer"]],
  ["IV rank", IV_RANK_REFRESH, ["radon-ivrank.timer"]],
  ["IV spread", IV_SPREAD_REFRESH, ["radon-iv-spread.timer"]],
  ["MA ratio", MA_RATIO_REFRESH, ["radon-ma-ratio.timer"]],
  ["COT positioning", COT_POSITIONING_REFRESH, ["radon-equibles-cot.timer"]],
  ["data_refresh sweep", DATA_REFRESH, ["radon-refresh.timer"]],
  ["CRI", CRI_REFRESH, ["radon-refresh.timer"]],
  ["GEX", GEX_REFRESH, ["radon-refresh.timer"]],
  ["VCG", VCG_REFRESH, ["radon-vcg-refresh.timer", "radon-refresh.timer"]],
  ["breadth", BREADTH_REFRESH, ["radon-breadth.timer"]],
  ["BPI", BPI_REFRESH, ["radon-bpi.timer"]],
  ["margin debt", MARGIN_DEBT_REFRESH, ["radon-margin-debt.timer"]],
  ["straddle", STRADDLE_REFRESH, ["radon-straddle.timer"]],
  ["COR", COR_REFRESH, ["radon-cor.timer"]],
  ["VIX-COR", VIXCOR_REFRESH, ["radon-vixcor.timer"]],
  ["VIX TS", VIXTS_REFRESH, ["radon-vixts.timer"]],
  ["dispersion", DISPERSION_REFRESH, ["radon-dispersion.timer"]],
  ["skew", SKEW_REFRESH, ["radon-skew.timer"]],
  ["skew 2D", SKEW2D_REFRESH, ["radon-skew2d.timer"]],
  ["yield curve", YIELD_CURVE_REFRESH, ["radon-yield-curve.timer"]],
  ["credit spread", CREDIT_SPREAD_REFRESH, ["radon-credit-spread.timer"]],
  ["TSY/HY", IEI_HYG_REFRESH, ["radon-iei-hyg.timer"]],
  ["TRIN", TRIN_REFRESH, ["radon-trin.timer"]],
  ["dividend yield", DIV_YIELD_REFRESH, ["radon-divyield.timer"]],
  ["HY A/D", HY_AD_REFRESH, ["radon-hyad.timer"]],
  ["HH leverage", HH_LEV_REFRESH, ["radon-hhlev.timer"]],
];

describe("schedule constants are pinned to the systemd timers", () => {
  it.each(PINNED)("%s mirrors %s", (_name, schedule, units) => {
    const expected = units.flatMap((unit) => canonical(timerRules(unit)));
    expect(expected.length).toBeGreaterThan(0);
    expect(canonical(schedule)).toEqual(expected);
  });

  it("reads every OnCalendar line of a multi-line timer", () => {
    expect(timerRules("radon-bpi.timer")).toHaveLength(3);
    expect(timerRules("radon-yield-curve.timer")).toHaveLength(2);
  });

  it("parses the short and long OnCalendar forms alike", () => {
    expect(parseOnCalendar("Tue..Sat 11:00 UTC")).toEqual({ weekdays: [2, 3, 4, 5, 6], hours: [11], minutes: [0] });
    expect(parseOnCalendar("*-*-* 02:20:00 UTC")).toEqual({ hours: [2], minutes: [20] });
    expect(parseOnCalendar("Mon..Fri *-*-* 13..14:00,15,30,45")).toEqual({
      weekdays: [1, 2, 3, 4, 5],
      hours: [13, 14],
      minutes: [0, 15, 30, 45],
    });
    expect(parseOnCalendar("Mon..Fri *-*-* 09:00 America/New_York")).toEqual({
      tz: "America/New_York",
      weekdays: [1, 2, 3, 4, 5],
      hours: [9],
      minutes: [0],
    });
  });
});

describe("firesWeekly", () => {
  it("is true only for the once-a-week writers", () => {
    expect(firesWeekly(ATS_VENUE_SHARE_REFRESH)).toBe(true);
    expect(firesWeekly(COT_POSITIONING_REFRESH)).toBe(true);
    expect(firesWeekly(IV_RANK_REFRESH)).toBe(false);
    expect(firesWeekly(HY_AD_REFRESH)).toBe(false);
    expect(firesWeekly(BPI_REFRESH)).toBe(false);
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

  it("weekday-only: skips the weekend", () => {
    // Friday 2026-08-21 on the last breadth slot → Monday 13:00 UTC.
    const from = new Date(Date.UTC(2026, 7, 21, 21, 55));
    expect(nextRefreshUtc(BREADTH_REFRESH, from).toISOString()).toBe("2026-08-24T13:00:00.000Z");
  });

  it("intraday: the next five-minute slot, honouring the minute offset", () => {
    const from = new Date(Date.UTC(2026, 7, 26, 15, 3));
    expect(nextRefreshUtc(BREADTH_REFRESH, from).toISOString()).toBe("2026-08-26T15:05:00.000Z");
    expect(nextRefreshUtc(TRIN_REFRESH, from).toISOString()).toBe("2026-08-26T15:07:00.000Z");
  });

  it("multi-line: the earliest slot across the lines", () => {
    // Wednesday 22:00 UTC: BPI's 23:30 weekday slot beats Thursday 11:00.
    const wed = new Date(Date.UTC(2026, 7, 26, 22, 0));
    expect(nextRefreshUtc(BPI_REFRESH, wed).toISOString()).toBe("2026-08-26T23:30:00.000Z");
    // Friday after 23:30: Saturday 11:00 is the only weekend line.
    const fri = new Date(Date.UTC(2026, 7, 28, 23, 45));
    expect(nextRefreshUtc(BPI_REFRESH, fri).toISOString()).toBe("2026-08-29T11:00:00.000Z");
  });

  it("America/New_York: 09:00 ET is 13:00 UTC in summer and 14:00 UTC in winter", () => {
    const etLine: RefreshSchedule = [VCG_REFRESH[0]];
    const summer = new Date(Date.UTC(2026, 7, 26, 12, 0));
    expect(nextRefreshUtc(etLine, summer).toISOString()).toBe("2026-08-26T13:00:00.000Z");
    const winter = new Date(Date.UTC(2026, 0, 14, 12, 0));
    expect(nextRefreshUtc(etLine, winter).toISOString()).toBe("2026-01-14T14:00:00.000Z");
  });

  it("America/New_York: the last ET slot lands on the UTC evening", () => {
    // 16:55 ET Wednesday in summer is 20:55 UTC; the ET line is done, and the
    // UTC data_refresh sweep still owns 21:00 and 21:15.
    const from = new Date(Date.UTC(2026, 7, 26, 20, 56));
    expect(nextRefreshUtc(VCG_REFRESH, from).toISOString()).toBe("2026-08-26T21:00:00.000Z");
  });
});

describe("previousRefreshUtc", () => {
  it("daily: the slot itself when standing on it, otherwise the one before", () => {
    const atSlot = new Date(Date.UTC(2026, 7, 26, 22, 10));
    expect(previousRefreshUtc(IV_RANK_REFRESH, atSlot).toISOString()).toBe("2026-08-26T22:10:00.000Z");
    const before = new Date(Date.UTC(2026, 7, 26, 22, 9));
    expect(previousRefreshUtc(IV_RANK_REFRESH, before).toISOString()).toBe("2026-08-25T22:10:00.000Z");
  });

  it("weekday-only: Friday's last slot all weekend", () => {
    const sunday = new Date(Date.UTC(2026, 7, 23, 12, 0));
    expect(previousRefreshUtc(BREADTH_REFRESH, sunday).toISOString()).toBe("2026-08-21T21:55:00.000Z");
  });

  it("weekly: one interval behind the next fire", () => {
    const from = new Date(Date.UTC(2026, 7, 23, 12, 0));
    expect(previousRefreshUtc(ATS_VENUE_SHARE_REFRESH, from).toISOString()).toBe(
      "2026-08-18T09:15:00.000Z",
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
