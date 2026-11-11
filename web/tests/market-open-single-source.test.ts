/**
 * D3: every web "is the market open" answer comes from one holiday- and
 * half-day-aware source (lib/marketCalendar.ts).
 *
 * Same dates as the Python suite: full holidays 2026-11-26 (Thanksgiving) and
 * 2026-12-25, the 2026-11-27 half day (13:00 ET close), and the DST Mondays
 * 2026-03-09 (first EDT) and 2026-11-02 (first EST). Instants are UTC.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

import { getMarketStateFromDate, getMarketPhaseFromDate } from "../lib/serviceHealthWindows";
import { isMarketOpenAt } from "../lib/marketCalendar";
import { marketStateAt, MarketState } from "../lib/useMarketHours";
import { mostRecentSessionDate, lastCompletedSessionDate } from "../lib/marketSession";
import { chipMarketState } from "../lib/orders/sessionWindow";
import { isVcgDataStale } from "../lib/vcgStaleness";
import { isGexDataStale } from "../lib/gexStaleness";
import { isGammaRotationStale } from "../lib/gammaRotationStaleness";
import { isCriDataStale } from "../lib/criStaleness";
import { isFlowReportStale } from "../lib/flowReportStaleness";
import { expectedVcgSessionDate } from "../app/api/vcg/route";

const THANKSGIVING_11 = new Date("2026-11-26T16:00:00Z"); // 11:00 EST
const CHRISTMAS_11 = new Date("2026-12-25T16:00:00Z"); // 11:00 EST
const HALF_DAY_11 = new Date("2026-11-27T16:00:00Z"); // 11:00 EST
const HALF_DAY_14 = new Date("2026-11-27T19:00:00Z"); // 14:00 EST, after the 13:00 close
const SPRING_DST_0945 = new Date("2026-03-09T13:45:00Z"); // 09:45 EDT
const FALL_DST_0945 = new Date("2026-11-02T14:45:00Z"); // 09:45 EST
const FALL_DST_0845 = new Date("2026-11-02T13:45:00Z"); // 08:45 EST

const RTH_CASES: Array<[string, Date, boolean]> = [
  ["thanksgiving", THANKSGIVING_11, false],
  ["christmas", CHRISTMAS_11, false],
  ["half-day morning", HALF_DAY_11, true],
  ["half-day after the 13:00 close", HALF_DAY_14, false],
  ["spring DST 09:45 EDT", SPRING_DST_0945, true],
  ["fall DST 09:45 EST", FALL_DST_0945, true],
  ["fall DST 08:45 EST", FALL_DST_0845, false],
];

afterEach(() => {
  vi.useRealTimers();
});

describe("canonical isMarketOpenAt", () => {
  it.each(RTH_CASES)("%s", (_label, now, open) => {
    expect(isMarketOpenAt(now)).toBe(open);
  });
});

describe("three-state market clock", () => {
  it.each([
    ["thanksgiving", THANKSGIVING_11, "closed"],
    ["christmas", CHRISTMAS_11, "closed"],
    ["half-day morning", HALF_DAY_11, "open"],
    ["half-day after close", HALF_DAY_14, "extended"],
    ["spring DST open", SPRING_DST_0945, "open"],
    ["fall DST pre-market", FALL_DST_0845, "extended"],
  ] as const)("getMarketStateFromDate and marketStateAt agree: %s", (_label, now, state) => {
    expect(getMarketStateFromDate(now)).toBe(state);
    expect(marketStateAt(now)).toBe(state as MarketState);
    expect(chipMarketState(now)).toBe(state as MarketState);
  });

  it("half-day afternoon is the after-hours phase", () => {
    expect(getMarketPhaseFromDate(HALF_DAY_14)).toBe("after");
    expect(getMarketPhaseFromDate(HALF_DAY_11)).toBe("open");
  });
});

describe("session dates skip holidays", () => {
  it.each([
    ["thanksgiving resolves to Wednesday", THANKSGIVING_11, "2026-11-25"],
    ["half-day pre-market skips Thanksgiving", new Date("2026-11-27T13:00:00Z"), "2026-11-25"],
    ["Monday pre-market skips Christmas", new Date("2026-12-28T13:00:00Z"), "2026-12-24"],
    ["fall DST after open", FALL_DST_0945, "2026-11-02"],
    ["fall DST pre-market", FALL_DST_0845, "2026-10-30"],
  ] as const)("mostRecentSessionDate / expectedVcgSessionDate: %s", (_label, now, date) => {
    expect(mostRecentSessionDate(now)).toBe(date);
    expect(expectedVcgSessionDate(now)).toBe(date);
  });

  it("lastCompletedSessionDate on Thanksgiving evening is Wednesday", () => {
    expect(lastCompletedSessionDate(new Date("2026-11-26T22:00:00Z"))).toBe("2026-11-25");
  });
});

describe("staleness gates default to the canonical clock", () => {
  // Each gate takes an injectable market-open flag; its default must equal
  // the canonical answer for the same instant.
  it.each(RTH_CASES)("%s", (_label, now, open) => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const session = mostRecentSessionDate(now);
    const scanned = { scan_time: new Date(now.getTime() - 3_600_000).toISOString() };
    expect(isVcgDataStale(scanned, session)).toBe(isVcgDataStale(scanned, session, open));
    expect(isGexDataStale(scanned, session)).toBe(isGexDataStale(scanned, session, open));
    expect(isGammaRotationStale(scanned, session)).toBe(isGammaRotationStale(scanned, session, open));
    const cri = { date: session, market_open: true };
    const mtime = now.getTime() - 3_600_000;
    expect(isCriDataStale(cri, mtime, session, undefined, session)).toBe(open);
    const report = { fetched_at: scanned.scan_time };
    expect(isFlowReportStale(report, now)).toBe(isFlowReportStale(report, now, open));
  });
});

describe("no private market-open copies", () => {
  const webRoot = path.resolve(__dirname, "..");
  const files = execFileSync("git", ["ls-files", "lib", "app", "components"], { cwd: webRoot, encoding: "utf8" })
    .split("\n")
    .filter((f) => /\.(ts|tsx)$/.test(f) && f !== "lib/marketCalendar.ts");

  it("no file outside lib/marketCalendar.ts re-derives the 09:30-16:00 ET window", () => {
    const offenders = files.filter((f) => {
      const src = fs.readFileSync(path.join(webRoot, f), "utf8");
      return /function isMarketOpen(Now|At)\b/.test(src) || /minutes\s*>=\s*9 \* 60 \+ 30 && minutes\s*<=\s*16 \* 60/.test(src);
    });
    expect(offenders).toEqual([]);
  });
});
