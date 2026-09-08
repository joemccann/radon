/**
 * Frontend source of truth for indicator refresh timers.
 *
 * Each constant mirrors the OnCalendar lines of its systemd timer in
 * cloud/services/ — tests/refresh-schedule.test.ts parses those unit files
 * and pins every constant to them, so a timer change without a matching edit
 * here fails CI. Panels derive "next refresh" copy from these instead of
 * hardcoding cadence (root CLAUDE.md, UI Copy Rules).
 *
 * A schedule is a list of rules, one per OnCalendar line. A rule names the
 * wall-clock slots it fires on in its own zone: the VPS clock is UTC, so a
 * line without a zone suffix is UTC; a few timers are written in
 * America/New_York so they track the exchange across DST.
 */

export type ScheduleZone = "UTC" | "America/New_York";

/** One OnCalendar line. */
export type RefreshRule = {
  /** Zone the line is written in. Omitted means UTC, the VPS clock. */
  tz?: ScheduleZone;
  /** 0=Sun .. 6=Sat, in `tz`. Omitted means every day. */
  weekdays?: readonly number[];
  /** Wall-clock hours the line fires on, in `tz`. */
  hours: readonly number[];
  /** Minutes within each of those hours. */
  minutes: readonly number[];
};

export type RefreshSchedule = readonly RefreshRule[];

/** Older name for the same shape; every schedule is resolved to UTC instants. */
export type UtcSchedule = RefreshSchedule;

const MON_FRI: readonly number[] = [1, 2, 3, 4, 5];
const TUE_SAT: readonly number[] = [2, 3, 4, 5, 6];

/** `from..to`, inclusive, the way systemd writes an hour range. */
function range(from: number, to: number): number[] {
  return Array.from({ length: to - from + 1 }, (_, i) => from + i);
}

/** Every `step` minutes within the hour, starting at `offset`. */
function every(step: number, offset = 0): number[] {
  return range(0, Math.floor((59 - offset) / step)).map((i) => offset + i * step);
}

function daily(hour: number, minute: number): RefreshRule {
  return { hours: [hour], minutes: [minute] };
}

function weekly(weekday: number, hour: number, minute: number): RefreshRule {
  return { weekdays: [weekday], hours: [hour], minutes: [minute] };
}

/** cloud/services/radon-equibles-ats.timer */
export const ATS_VENUE_SHARE_REFRESH: RefreshSchedule = [weekly(2, 9, 15)];

/** cloud/services/radon-equibles-short-crowding.timer */
export const SHORT_CROWDING_REFRESH: RefreshSchedule = [daily(9, 30)];

/** cloud/services/radon-ivrank.timer */
export const IV_RANK_REFRESH: RefreshSchedule = [daily(22, 10)];

/** cloud/services/radon-iv-spread.timer */
export const IV_SPREAD_REFRESH: RefreshSchedule = [daily(22, 15)];

/** cloud/services/radon-ma-ratio.timer */
export const MA_RATIO_REFRESH: RefreshSchedule = [daily(22, 45)];

/** cloud/services/radon-equibles-cot.timer */
export const COT_POSITIONING_REFRESH: RefreshSchedule = [weekly(6, 1, 0)];

/**
 * cloud/services/radon-refresh.timer — `scripts.data_refresh`, which rewrites
 * the CRI, VCG and GEX caches on every fire.
 */
export const DATA_REFRESH: RefreshSchedule = [
  { weekdays: MON_FRI, hours: range(13, 21), minutes: [0, 15, 30, 45] },
];
export const CRI_REFRESH: RefreshSchedule = DATA_REFRESH;
export const GEX_REFRESH: RefreshSchedule = DATA_REFRESH;

/** cloud/services/radon-vcg-refresh.timer, plus the data_refresh sweep. */
export const VCG_REFRESH: RefreshSchedule = [
  { tz: "America/New_York", weekdays: MON_FRI, hours: range(9, 16), minutes: every(5) },
  ...DATA_REFRESH,
];

/** cloud/services/radon-breadth.timer */
export const BREADTH_REFRESH: RefreshSchedule = [
  { weekdays: MON_FRI, hours: range(13, 21), minutes: every(5) },
];

/** cloud/services/radon-bpi.timer */
export const BPI_REFRESH: RefreshSchedule = [
  { weekdays: MON_FRI, hours: [21], minutes: [30] },
  { weekdays: MON_FRI, hours: [23], minutes: [30] },
  { weekdays: TUE_SAT, hours: [11], minutes: [0] },
];

/** cloud/services/radon-margin-debt.timer */
export const MARGIN_DEBT_REFRESH: RefreshSchedule = [daily(13, 10)];

/** cloud/services/radon-straddle.timer */
export const STRADDLE_REFRESH: RefreshSchedule = [daily(2, 15)];

/** cloud/services/radon-cor.timer */
export const COR_REFRESH: RefreshSchedule = [daily(2, 20)];

/** cloud/services/radon-vixcor.timer */
export const VIXCOR_REFRESH: RefreshSchedule = [daily(2, 35)];

/** cloud/services/radon-vixts.timer */
export const VIXTS_REFRESH: RefreshSchedule = [daily(2, 45)];

/** cloud/services/radon-dispersion.timer */
export const DISPERSION_REFRESH: RefreshSchedule = [daily(22, 20)];

/** cloud/services/radon-skew.timer — intraday sweep plus the EOD finalize. */
export const SKEW_REFRESH: RefreshSchedule = [
  { weekdays: MON_FRI, hours: range(13, 21), minutes: every(5) },
  daily(21, 45),
];

/** cloud/services/radon-skew2d.timer */
export const SKEW2D_REFRESH: RefreshSchedule = [daily(21, 50)];

/** cloud/services/radon-yield-curve.timer */
export const YIELD_CURVE_REFRESH: RefreshSchedule = [
  { weekdays: MON_FRI, hours: [20], minutes: [45] },
  daily(22, 30),
];

/** cloud/services/radon-credit-spread.timer */
export const CREDIT_SPREAD_REFRESH: RefreshSchedule = [daily(21, 45)];

/** cloud/services/radon-iei-hyg.timer */
export const IEI_HYG_REFRESH: RefreshSchedule = [daily(21, 55)];

/** cloud/services/radon-trin.timer */
export const TRIN_REFRESH: RefreshSchedule = [
  { weekdays: MON_FRI, hours: range(13, 21), minutes: every(5, 2) },
];

/** cloud/services/radon-divyield.timer */
export const DIV_YIELD_REFRESH: RefreshSchedule = [daily(22, 40)];

/** cloud/services/radon-hyad.timer */
export const HY_AD_REFRESH: RefreshSchedule = [{ weekdays: TUE_SAT, hours: [11], minutes: [0] }];

/** cloud/services/radon-hhlev.timer */
export const HH_LEV_REFRESH: RefreshSchedule = [daily(13, 20)];

const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAY_MS = 24 * 60 * 60 * 1000;
/** A weekly rule needs up to seven days to come round; one more is slack. */
const MAX_DAYS_SCANNED = 8;

type WallDate = { year: number; month: number; day: number };

function wallDateIn(tz: ScheduleZone, instant: Date): WallDate {
  if (tz === "UTC") {
    return { year: instant.getUTCFullYear(), month: instant.getUTCMonth(), day: instant.getUTCDate() };
  }
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    year: "numeric",
    month: "numeric",
    day: "numeric",
  }).formatToParts(instant);
  const read = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  return { year: read("year"), month: read("month") - 1, day: read("day") };
}

/** Milliseconds the zone's wall clock runs ahead of UTC at `instant`. */
function zoneOffsetMs(tz: ScheduleZone, instant: Date): number {
  if (tz === "UTC") return 0;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hourCycle: "h23",
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    second: "numeric",
  }).formatToParts(instant);
  const read = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  const asUtc = Date.UTC(
    read("year"), read("month") - 1, read("day"), read("hour"), read("minute"), read("second"),
  );
  return asUtc - instant.getTime();
}

/**
 * Every instant a rule fires on one wall-clock day, ascending. The zone
 * offset is read once at that day's noon: DST moves at 02:00 local, and no
 * radon timer fires inside that hour.
 */
function slotsOnDay(rule: RefreshRule, dayStartUtcMs: number): number[] {
  const tz = rule.tz ?? "UTC";
  if (rule.weekdays && !rule.weekdays.includes(new Date(dayStartUtcMs).getUTCDay())) return [];
  const offset = zoneOffsetMs(tz, new Date(dayStartUtcMs + DAY_MS / 2));
  const out: number[] = [];
  for (const hour of rule.hours) {
    for (const minute of rule.minutes) {
      out.push(dayStartUtcMs + (hour * 60 + minute) * 60 * 1000 - offset);
    }
  }
  return out.sort((a, b) => a - b);
}

function wallDayStartMs(rule: RefreshRule, from: Date): number {
  const wall = wallDateIn(rule.tz ?? "UTC", from);
  return Date.UTC(wall.year, wall.month, wall.day);
}

function nextForRule(rule: RefreshRule, from: Date): number | null {
  const start = wallDayStartMs(rule, from);
  for (let offset = 0; offset <= MAX_DAYS_SCANNED; offset += 1) {
    const hit = slotsOnDay(rule, start + offset * DAY_MS).find((slot) => slot > from.getTime());
    if (hit !== undefined) return hit;
  }
  return null;
}

function previousForRule(rule: RefreshRule, from: Date): number | null {
  const start = wallDayStartMs(rule, from);
  for (let offset = 0; offset <= MAX_DAYS_SCANNED; offset += 1) {
    const past = slotsOnDay(rule, start - offset * DAY_MS).filter((slot) => slot <= from.getTime());
    if (past.length > 0) return past[past.length - 1];
  }
  return null;
}

/** Every rule fires on a single weekday: the writer runs once a week. */
export function firesWeekly(schedule: RefreshSchedule): boolean {
  return schedule.length > 0 && schedule.every((rule) => rule.weekdays?.length === 1);
}

/** The first fire strictly after `from`. */
export function nextRefreshUtc(schedule: RefreshSchedule, from: Date = new Date()): Date {
  const hits = schedule.map((rule) => nextForRule(rule, from)).filter((ms): ms is number => ms !== null);
  if (hits.length === 0) throw new Error("no refresh slot within 8 days — schedule is malformed");
  return new Date(Math.min(...hits));
}

/** The most recent fire at or before `from`. */
export function previousRefreshUtc(schedule: RefreshSchedule, from: Date = new Date()): Date {
  const hits = schedule
    .map((rule) => previousForRule(rule, from))
    .filter((ms): ms is number => ms !== null);
  if (hits.length === 0) throw new Error("no refresh slot within 8 days — schedule is malformed");
  return new Date(Math.max(...hits));
}

export function nextRefreshLabel(schedule: RefreshSchedule, from: Date = new Date()): string {
  const next = nextRefreshUtc(schedule, from);
  const weekday = WEEKDAY_LABELS[next.getUTCDay()];
  const date = next.toISOString().slice(0, 10);
  const hh = String(next.getUTCHours()).padStart(2, "0");
  const mm = String(next.getUTCMinutes()).padStart(2, "0");
  return `${weekday} ${date} ${hh}:${mm} UTC`;
}

export function dataAgeDays(
  isoDate: string | null | undefined,
  from: Date = new Date(),
): number | null {
  if (!isoDate) return null;
  const parsed = Date.parse(`${isoDate.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(parsed)) return null;
  return Math.max(0, Math.floor((from.getTime() - parsed) / DAY_MS));
}
