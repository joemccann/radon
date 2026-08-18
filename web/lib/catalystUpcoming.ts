/**
 * Read-time catalyst freshness — recompute `days_until` and drop past events.
 *
 * The catalysts writer (scripts/fetch_catalysts.py via run_catalysts.sh) only
 * runs on trading days, so by a Sunday after a Friday holiday the stored
 * snapshot is days old and its baked-in `days_until` integers are fossils.
 * The stored value is therefore advisory only: every consumer-facing distance
 * is recomputed here from the row's date-only string against the ET calendar
 * day at render time.
 *
 * Semantics (operator-specified):
 *  - "Upcoming" means now-or-future only. Past calendar days never survive.
 *  - Same-ET-day rows stay until 20:00 ET even after `event_time`, so a
 *    released print can still render on the dashboard card.
 *  - A same-day event ages out once the extended session has ended (20:00 ET).
 *  - Events do not occur on closed market days. A row dated a weekend/holiday
 *    "today" is provider noise and is remapped to its next trading session
 *    (per the market-calendar SoT), so it can never badge "Today".
 *
 * Date handling: date-only strings are compared as ET calendar days and
 * diffed at UTC noon — never `new Date("YYYY-MM-DD")`, which parses as UTC
 * midnight and day-shifts west of UTC (the formatTradeDate hazard).
 */

import { isUsTradingDay } from "./serviceHealthWindows";

const MS_PER_DAY = 86_400_000;
const EXTENDED_SESSION_END_MINUTES = 20 * 60; // 20:00 ET
const NEXT_SESSION_SEARCH_LIMIT_DAYS = 14;

interface DatedCatalyst {
  date: string;
  days_until: number;
  event_time?: string | null;
}

function etWallClock(now: Date): Date {
  return new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
}

function isoDateOf(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function utcNoonMs(isoDate: string): number {
  const [y, m, d] = isoDate.split("-").map(Number);
  if (!y || !m || !d) return Number.NaN;
  return Date.UTC(y, m - 1, d, 12);
}

/** Whole-day distance from the ET calendar day of `now` to a date-only string. */
export function daysUntilEt(eventDate: string, now: Date = new Date()): number {
  const eventMs = utcNoonMs(eventDate.slice(0, 10));
  const todayMs = utcNoonMs(isoDateOf(etWallClock(now)));
  return Math.round((eventMs - todayMs) / MS_PER_DAY);
}

/** Days from ET-today to the next trading session per the holiday SoT. */
function nextTradingSessionDistance(todayIso: string): number {
  const todayMs = utcNoonMs(todayIso);
  for (let offset = 1; offset <= NEXT_SESSION_SEARCH_LIMIT_DAYS; offset += 1) {
    const candidate = new Date(todayMs + offset * MS_PER_DAY).toISOString().slice(0, 10);
    if (isUsTradingDay(candidate)) return offset;
  }
  return NEXT_SESSION_SEARCH_LIMIT_DAYS;
}

/**
 * Filter a stored catalyst snapshot down to genuinely upcoming rows, with
 * `days_until` recomputed from each row's date. Returns new row objects;
 * never mutates the input. Sorted nearest-first.
 */
export function upcomingCatalysts<T extends DatedCatalyst>(rows: T[], now: Date = new Date()): T[] {
  const et = etWallClock(now);
  const todayIso = isoDateOf(et);
  const extendedSessionOver = et.getHours() * 60 + et.getMinutes() > EXTENDED_SESSION_END_MINUTES;
  const todayIsTradingDay = isUsTradingDay(todayIso);

  const upcoming: T[] = [];
  for (const row of rows) {
    const recomputed = daysUntilEt(row.date, now);
    if (!Number.isFinite(recomputed) || recomputed < 0) continue;
    if (recomputed === 0) {
      if (todayIsTradingDay && extendedSessionOver) continue;
      if (!todayIsTradingDay) {
        upcoming.push({ ...row, days_until: nextTradingSessionDistance(todayIso) });
        continue;
      }
    }
    upcoming.push({ ...row, days_until: recomputed });
  }
  return upcoming.sort((a, b) => a.days_until - b.days_until);
}
