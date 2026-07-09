/**
 * Calendar-day helpers for "Today's Executed Orders".
 * Trading day boundary is America/New_York (not the host local zone, not UTC).
 */

export const ET_TZ = "America/New_York";

/** YYYY-MM-DD for `when` in America/New_York. */
export function etCalendarDateString(when: Date = new Date()): string {
  return when.toLocaleDateString("sv", { timeZone: ET_TZ });
}

/**
 * True when `fillTime` falls on the given ET calendar day (default: today ET).
 * Accepts ISO datetimes and date-only `YYYY-MM-DD` prefixes.
 */
export function isFillOnEtCalendarDay(
  fillTime: string | null | undefined,
  dayEt: string = etCalendarDateString(),
  now: Date = new Date(),
): boolean {
  if (!fillTime) return false;
  const day = dayEt || etCalendarDateString(now);

  // Date-only or leading date: compare the ET calendar day of that instant
  // (date-only midnight UTC would shift; treat bare date as that ET day label).
  if (/^\d{4}-\d{2}-\d{2}$/.test(fillTime)) {
    return fillTime === day;
  }

  const ms = Date.parse(fillTime);
  if (Number.isNaN(ms)) {
    const prefix = fillTime.slice(0, 10);
    return /^\d{4}-\d{2}-\d{2}$/.test(prefix) && prefix === day;
  }
  return etCalendarDateString(new Date(ms)) === day;
}

export function filterExecutedToEtToday<T extends { time?: string | null }>(
  fills: readonly T[],
  now: Date = new Date(),
): T[] {
  const day = etCalendarDateString(now);
  return fills.filter((f) => isFillOnEtCalendarDay(f.time, day, now));
}

/**
 * Time column for executed fills: clock only when the fill is on today's
 * ET calendar day; otherwise `M/D H:MM:SS AM/PM` so prior-day rows are obvious.
 */
export function formatExecutedFillTime(
  fillTime: string | null | undefined,
  now: Date = new Date(),
): string {
  if (!fillTime) return "--";
  const ms = Date.parse(fillTime);
  if (Number.isNaN(ms)) return fillTime;
  const d = new Date(ms);
  const today = etCalendarDateString(now);
  const fillDay = etCalendarDateString(d);
  const timePart = d.toLocaleTimeString("en-US", {
    timeZone: ET_TZ,
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
  if (fillDay === today) return timePart;
  const datePart = d.toLocaleDateString("en-US", {
    timeZone: ET_TZ,
    month: "numeric",
    day: "numeric",
  });
  return `${datePart} ${timePart}`;
}
