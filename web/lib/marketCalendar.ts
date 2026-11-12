/**
 * The one web answer to "is the US equity market open", holiday- and
 * half-day-aware. Every route, staleness gate, hook and chip delegates here.
 *
 * Data: the static tables Python's scripts/utils/market_calendar.py also
 * reads. `market_holidays.json` lists full closures; `market_early_closes.json`
 * lists half days ({date: "HH:MM" ET close}). The browser has no IBKR
 * calendar cache, so an ad-hoc closure the tables do not carry reads as a
 * normal day here. Years missing from the holiday table fall back to
 * weekday-only rather than guessing (see HOLIDAY_TABLE_YEARS).
 */

import staticHolidays from "../../scripts/config/market_holidays.json" with { type: "json" };
import earlyCloses from "../../scripts/config/market_early_closes.json" with { type: "json" };

export type MarketState = "open" | "extended" | "closed";
export type MarketPhase = "pre" | "open" | "after" | "closed";

const PRE_OPEN_MIN = 4 * 60;
const OPEN_MIN = 9 * 60 + 30;
const CLOSE_MIN = 16 * 60;
const POST_CLOSE_END_MIN = 20 * 60;

type EtClock = { iso: string; minutes: number };

function etClock(now: Date): EtClock {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(now);
  const value = (type: string) => parts.find((p) => p.type === type)?.value ?? "00";
  return {
    iso: `${value("year")}-${value("month")}-${value("day")}`,
    minutes: (Number(value("hour")) % 24) * 60 + Number(value("minute")),
  };
}

function hhmmToMinutes(hhmm: string | undefined): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm ?? "");
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

/**
 * R-164: the static table is finite, and once it runs out every full-closure
 * holiday would read as a trading day. The coverage is explicit here and
 * pinned by a contract test that fails CI a year before the data runs out.
 */
export const HOLIDAY_TABLE_YEARS: readonly string[] = Object.freeze(
  Object.keys(staticHolidays as Record<string, string[]>).sort(),
);

export function isHolidayTableCovering(isoDate: string): boolean {
  return HOLIDAY_TABLE_YEARS.includes(isoDate.slice(0, 4));
}

function isHolidayIso(isoDate: string): boolean {
  const dates = (staticHolidays as Record<string, string[]>)[isoDate.slice(0, 4)];
  return Array.isArray(dates) && dates.includes(isoDate);
}

/** Weekday that is not a full-closure holiday. Pure on "YYYY-MM-DD". */
export function isUsTradingDay(isoDate: string): boolean {
  const [y, m, d] = isoDate.split("-").map(Number);
  const weekday = new Date(Date.UTC(y, m - 1, d, 12)).getUTCDay();
  if (weekday === 0 || weekday === 6) return false;
  return !isHolidayIso(isoDate);
}

/** Regular-session close for a trading day, in ET minutes (13:00 on a half day). */
export function sessionCloseMinutes(isoDate: string): number {
  return hhmmToMinutes((earlyCloses as Record<string, string>)[isoDate]) ?? CLOSE_MIN;
}

export function getMarketPhaseFromDate(now: Date = new Date()): MarketPhase {
  const { iso, minutes } = etClock(now);
  if (!isUsTradingDay(iso)) return "closed";
  const close = sessionCloseMinutes(iso);
  if (minutes >= OPEN_MIN && minutes <= close) return "open";
  if (minutes >= PRE_OPEN_MIN && minutes < OPEN_MIN) return "pre";
  if (minutes > close && minutes <= POST_CLOSE_END_MIN) return "after";
  return "closed";
}

export function getMarketStateFromDate(now: Date = new Date()): MarketState {
  const phase = getMarketPhaseFromDate(now);
  if (phase === "pre" || phase === "after") return "extended";
  return phase;
}

export function isMarketOpenAt(now: Date = new Date()): boolean {
  return getMarketPhaseFromDate(now) === "open";
}

function previousTradingDay(isoDate: string): string {
  const cursor = new Date(`${isoDate}T12:00:00Z`);
  do cursor.setUTCDate(cursor.getUTCDate() - 1);
  while (!isUsTradingDay(cursor.toISOString().slice(0, 10)));
  return cursor.toISOString().slice(0, 10);
}

function sessionDateAtBoundary(now: Date, boundaryMinutes: number): string {
  const { iso, minutes } = etClock(now);
  if (isUsTradingDay(iso) && minutes >= boundaryMinutes) return iso;
  return previousTradingDay(iso);
}

/** Session whose data we should already have: today once the 09:30 ET open
 * has passed, otherwise (and on weekends/holidays) the prior trading day. */
export function mostRecentSessionDate(now: Date = new Date()): string {
  return sessionDateAtBoundary(now, OPEN_MIN);
}

/** Most recent COMPLETED session: today only at/after the 16:00 ET close. */
export function lastCompletedSessionDate(now: Date = new Date()): string {
  return sessionDateAtBoundary(now, CLOSE_MIN);
}
