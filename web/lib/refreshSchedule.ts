/**
 * Frontend source of truth for indicator refresh timers.
 *
 * Each constant mirrors the OnCalendar line of its systemd timer in
 * cloud/services/ — tests/refresh-schedule.test.ts pins them to the unit
 * files so a timer change without a matching edit here fails CI. Panels
 * derive "next refresh" copy from these instead of hardcoding cadence.
 */

export type UtcSchedule =
  | { cadence: "daily"; hourUtc: number; minuteUtc: number }
  | { cadence: "weekly"; weekdayUtc: number; hourUtc: number; minuteUtc: number };

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

const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAY_MS = 24 * 60 * 60 * 1000;

function slotOnUtcDay(schedule: UtcSchedule, dayStartUtcMs: number): number {
  return dayStartUtcMs + (schedule.hourUtc * 60 + schedule.minuteUtc) * 60 * 1000;
}

export function nextRefreshUtc(schedule: UtcSchedule, from: Date = new Date()): Date {
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

export function nextRefreshLabel(schedule: UtcSchedule, from: Date = new Date()): string {
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
