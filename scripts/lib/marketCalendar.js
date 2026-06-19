/**
 * Market-calendar single source of truth for the JS side (relay + future
 * frontend mirror). Answers "is the US equity market open right now" with
 * holiday + early-close awareness, so day-of-week + clock checks stop treating
 * mid-week holidays (e.g. Juneteenth) as trading days.
 *
 * Resolution order (failure-safe — a missing source can only NARROW the open
 * window, never widen it):
 *   1. IBKR-sourced cache `data/market_calendar.json` (authoritative, written
 *      daily by scripts/fetch_market_calendar.py). Carries ad-hoc closures +
 *      half-days the static table can't.
 *   2. Static `scripts/config/market_holidays.json` (the existing hand list).
 *   3. Bare weekday + 09:30-16:00 ET window (legacy behavior).
 *
 * The pure decision (`resolveMarketState`) is separated from the I/O (file
 * reads, ET wall-clock) so it is unit-testable in isolation, mirroring
 * lib/staleDataMachine.js.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const _dir = path.dirname(fileURLToPath(import.meta.url));
// Resolved relative to THIS file (scripts/lib/), not cwd, so the relay finds
// them regardless of where it was launched from.
const IBKR_CACHE_PATH = path.resolve(_dir, "..", "..", "data", "market_calendar.json");
const STATIC_HOLIDAYS_PATH = path.resolve(_dir, "..", "config", "market_holidays.json");

const OPEN_MIN = 9 * 60 + 30; // 09:30 ET
const CLOSE_MIN = 16 * 60; // 16:00 ET
const RELOAD_MS = 6 * 60 * 60 * 1000; // re-read the files every 6h (picks up the daily fetch)

let _ibkrDays = null; // { "YYYY-MM-DD": {status, open_et, close_et} } | null
let _holidaysByYear = null; // { "2026": Set<"YYYY-MM-DD"> }
let _loadedAt = 0;

/** "HH:MM" → minutes since midnight, or null. */
function hhmmToMinutes(hhmm) {
  if (typeof hhmm !== "string") return null;
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
  if (!m) return null;
  return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
}

/**
 * Pure market-state decision from already-resolved inputs.
 *
 * @param {{minutes: number, weekday: number, ibkrDay: ({status:string,open_et?:string,close_et?:string}|null), isStaticHoliday: boolean}} input
 *   minutes = ET minutes-since-midnight; weekday = 0(Sun)..6(Sat).
 * @returns {{state: "open"|"early_close"|"closed", isOpen: boolean, reason: string}}
 */
export function resolveMarketState({ minutes, weekday, ibkrDay, isStaticHoliday }) {
  // 1. IBKR cache is authoritative when it has an opinion for this date.
  if (ibkrDay) {
    if (ibkrDay.status === "closed") {
      return { state: "closed", isOpen: false, reason: "ibkr:closed" };
    }
    const openM = hhmmToMinutes(ibkrDay.open_et) ?? OPEN_MIN;
    const closeM = hhmmToMinutes(ibkrDay.close_et) ?? CLOSE_MIN;
    const isOpen = minutes >= openM && minutes <= closeM;
    if (!isOpen) return { state: "closed", isOpen: false, reason: "ibkr:outside_hours" };
    return closeM < CLOSE_MIN
      ? { state: "early_close", isOpen: true, reason: "ibkr:early_close" }
      : { state: "open", isOpen: true, reason: "ibkr:open" };
  }

  // 2. Weekend.
  if (weekday === 0 || weekday === 6) {
    return { state: "closed", isOpen: false, reason: "weekend" };
  }

  // 3. Static holiday table.
  if (isStaticHoliday) {
    return { state: "closed", isOpen: false, reason: "static:holiday" };
  }

  // 4. Bare weekday + RTH window.
  const isOpen = minutes >= OPEN_MIN && minutes <= CLOSE_MIN;
  return {
    state: isOpen ? "open" : "closed",
    isOpen,
    reason: isOpen ? "weekday_hours" : "outside_hours",
  };
}

/** ET wall-clock parts for an instant. */
function etParts(now) {
  const d = now || new Date();
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    weekday: "short",
  }).formatToParts(d);
  const p = Object.fromEntries(parts.map((x) => [x.type, x.value]));
  const dateStr = `${p.year}-${p.month}-${p.day}`;
  const minutes = (parseInt(p.hour, 10) % 24) * 60 + parseInt(p.minute, 10);
  const wd = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  return { dateStr, minutes, weekday: wd[p.weekday] };
}

function readJsonSafe(file) {
  try {
    if (!fs.existsSync(file)) return null;
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

function ensureLoaded(now) {
  const t = now ? now.getTime() : Date.now();
  if (_holidaysByYear !== null && t - _loadedAt < RELOAD_MS) return;
  _loadedAt = t;

  const ibkr = readJsonSafe(IBKR_CACHE_PATH);
  _ibkrDays = ibkr && typeof ibkr.days === "object" ? ibkr.days : null;

  const holidays = readJsonSafe(STATIC_HOLIDAYS_PATH) || {};
  const byYear = {};
  for (const [year, dates] of Object.entries(holidays)) {
    if (Array.isArray(dates)) byYear[year] = new Set(dates);
  }
  _holidaysByYear = byYear;
}

/**
 * Full market state for an instant (default now), holiday + early-close aware.
 * @param {Date} [now]
 * @returns {{state: "open"|"early_close"|"closed", isOpen: boolean, reason: string}}
 */
export function marketState(now) {
  ensureLoaded(now);
  const { dateStr, minutes, weekday } = etParts(now);
  const year = dateStr.slice(0, 4);
  const ibkrDay = _ibkrDays ? _ibkrDays[dateStr] || null : null;
  const isStaticHoliday = !!(_holidaysByYear[year] && _holidaysByYear[year].has(dateStr));
  return resolveMarketState({ minutes, weekday, ibkrDay, isStaticHoliday });
}

/** Convenience boolean for callers that only need "is RTH open right now". */
export function isMarketOpen(now) {
  return marketState(now).isOpen;
}

/** Test-only: drop cached file reads so a test can re-load fixtures. */
export function _resetCacheForTest() {
  _ibkrDays = null;
  _holidaysByYear = null;
  _loadedAt = 0;
}
