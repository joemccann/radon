/**
 * Frontend source of truth for scheduled data refresh timers.
 *
 * Each constant mirrors the OnCalendar line of its systemd timer in
 * cloud/services/ — tests/refresh-schedule.test.ts pins them to the unit
 * files so a timer change without a matching edit here fails CI. Panels
 * derive "next refresh" copy from these instead of hardcoding cadence.
 */

export type UtcSchedule =
  | { cadence: "daily"; hourUtc: number; minuteUtc: number }
  | { cadence: "weekly"; weekdayUtc: number; hourUtc: number; minuteUtc: number };

export type ZonedWeekdaySchedule = {
  cadence: "selected-weekdays";
  weekdaysLocal: readonly number[];
  hourLocal: number;
  minuteLocal: number;
  timeZone: string;
};

export type RefreshSchedule = UtcSchedule | ZonedWeekdaySchedule;

/** cloud/services/radon-equibles-ats.timer */
export const ATS_VENUE_SHARE_REFRESH: UtcSchedule = {
  cadence: "weekly",
  weekdayUtc: 2,
  hourUtc: 9,
  minuteUtc: 15,
};

/** cloud/services/radon-equibles-short-crowding.timer */
export const SHORT_CROWDING_REFRESH: UtcSchedule = {
  cadence: "daily",
  hourUtc: 9,
  minuteUtc: 30,
};

/** cloud/services/radon-ivrank.timer */
export const IV_RANK_REFRESH: UtcSchedule = {
  cadence: "daily",
  hourUtc: 22,
  minuteUtc: 10,
};

/** cloud/services/radon-iv-spread.timer */
export const IV_SPREAD_REFRESH: UtcSchedule = { cadence: "daily", hourUtc: 22, minuteUtc: 15 };

/** cloud/services/radon-ma-ratio.timer */
export const MA_RATIO_REFRESH: UtcSchedule = {
  cadence: "daily",
  hourUtc: 22,
  minuteUtc: 45,
};

/** cloud/services/radon-equibles-cot.timer */
export const COT_POSITIONING_REFRESH: UtcSchedule = {
  cadence: "weekly",
  weekdayUtc: 6,
  hourUtc: 1,
  minuteUtc: 0,
};

/** cloud/services/radon-perf-twr.timer */
export const PERFORMANCE_REFRESH: ZonedWeekdaySchedule = {
  cadence: "selected-weekdays",
  weekdaysLocal: [2, 3, 4, 5, 6],
  hourLocal: 7,
  minuteLocal: 30,
  timeZone: "America/New_York",
};

const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAY_MS = 24 * 60 * 60 * 1000;

function slotOnUtcDay(schedule: UtcSchedule, dayStartUtcMs: number): number {
  return dayStartUtcMs + (schedule.hourUtc * 60 + schedule.minuteUtc) * 60 * 1000;
}

type CalendarParts = {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
};

const formatterCache = new Map<string, Intl.DateTimeFormat>();

function zonedParts(date: Date, timeZone: string): CalendarParts {
  let formatter = formatterCache.get(timeZone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    });
    formatterCache.set(timeZone, formatter);
  }

  const parts = Object.fromEntries(
    formatter
      .formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, Number(part.value)]),
  );
  return parts as CalendarParts;
}

/** Convert an unambiguous local wall-clock slot to UTC. Performance runs at
 *  07:30, safely away from DST's skipped/repeated overnight hour. */
function zonedSlotUtcMs(
  schedule: ZonedWeekdaySchedule,
  year: number,
  month: number,
  day: number,
): number {
  const wallClock = Date.UTC(year, month - 1, day, schedule.hourLocal, schedule.minuteLocal);
  let candidate = wallClock;
  // The first pass resolves the zone offset; the second covers an offset
  // transition between the initial UTC guess and the target local instant.
  for (let pass = 0; pass < 2; pass += 1) {
    const shown = zonedParts(new Date(candidate), schedule.timeZone);
    const shownAsUtc = Date.UTC(
      shown.year,
      shown.month - 1,
      shown.day,
      shown.hour,
      shown.minute,
      shown.second,
    );
    candidate += wallClock - shownAsUtc;
  }
  return candidate;
}

function localDateAt(date: Date, timeZone: string): Pick<CalendarParts, "year" | "month" | "day"> {
  const { year, month, day } = zonedParts(date, timeZone);
  return { year, month, day };
}

function shiftedCalendarDate(
  date: Pick<CalendarParts, "year" | "month" | "day">,
  offsetDays: number,
): Pick<CalendarParts, "year" | "month" | "day"> & { weekday: number } {
  const shifted = new Date(Date.UTC(date.year, date.month - 1, date.day + offsetDays, 12));
  return {
    year: shifted.getUTCFullYear(),
    month: shifted.getUTCMonth() + 1,
    day: shifted.getUTCDate(),
    weekday: shifted.getUTCDay(),
  };
}

function nextZonedWeekdayRefresh(schedule: ZonedWeekdaySchedule, from: Date): Date {
  const localDate = localDateAt(from, schedule.timeZone);
  for (let offset = 0; offset <= 7; offset += 1) {
    const candidateDate = shiftedCalendarDate(localDate, offset);
    if (!schedule.weekdaysLocal.includes(candidateDate.weekday)) continue;
    const candidate = zonedSlotUtcMs(
      schedule,
      candidateDate.year,
      candidateDate.month,
      candidateDate.day,
    );
    if (candidate > from.getTime()) return new Date(candidate);
  }
  throw new Error("no refresh slot within 8 days — schedule is malformed");
}

function previousZonedWeekdayRefresh(schedule: ZonedWeekdaySchedule, before: Date): Date {
  const localDate = localDateAt(new Date(before.getTime() - 1), schedule.timeZone);
  for (let offset = 0; offset >= -7; offset -= 1) {
    const candidateDate = shiftedCalendarDate(localDate, offset);
    if (!schedule.weekdaysLocal.includes(candidateDate.weekday)) continue;
    const candidate = zonedSlotUtcMs(
      schedule,
      candidateDate.year,
      candidateDate.month,
      candidateDate.day,
    );
    if (candidate < before.getTime()) return new Date(candidate);
  }
  throw new Error("no previous refresh slot within 8 days — schedule is malformed");
}

export function nextRefreshUtc(schedule: RefreshSchedule, from: Date = new Date()): Date {
  if (schedule.cadence === "selected-weekdays") {
    return nextZonedWeekdayRefresh(schedule, from);
  }
  const dayStart = Date.UTC(from.getUTCFullYear(), from.getUTCMonth(), from.getUTCDate());
  for (let offset = 0; offset <= 7; offset += 1) {
    const candidate = slotOnUtcDay(schedule, dayStart + offset * DAY_MS);
    if (candidate <= from.getTime()) continue;
    if (schedule.cadence === "weekly" && new Date(candidate).getUTCDay() !== schedule.weekdayUtc) {
      continue;
    }
    return new Date(candidate);
  }
  throw new Error("no refresh slot within 8 days — schedule is malformed");
}

export function previousRefreshUtc(schedule: RefreshSchedule, before: Date): Date {
  if (schedule.cadence === "selected-weekdays") {
    return previousZonedWeekdayRefresh(schedule, before);
  }

  const dayStart = Date.UTC(before.getUTCFullYear(), before.getUTCMonth(), before.getUTCDate());
  for (let offset = 0; offset >= -7; offset -= 1) {
    const candidate = slotOnUtcDay(schedule, dayStart + offset * DAY_MS);
    if (candidate >= before.getTime()) continue;
    if (schedule.cadence === "weekly" && new Date(candidate).getUTCDay() !== schedule.weekdayUtc) {
      continue;
    }
    return new Date(candidate);
  }
  throw new Error("no previous refresh slot within 8 days — schedule is malformed");
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
