"use client";

import { useEffect, useState } from "react";

/**
 * CME Globex session gate for the equity-index E-mini futures (ES / NQ / RTY).
 *
 * Globex trades nearly 24h: Sunday 18:00 ET through Friday 17:00 ET, with a
 * daily 17:00-18:00 ET maintenance halt (Mon-Thu). The cash-index sessions
 * (SPX/NDX/RUT) only cover ~09:30-16:00 ET, so the header strip must key off
 * the futures session, not the equities one, to show overnight prices.
 *
 * Holiday overrides follow CME's published Equity Index Globex calendar.
 * Source: https://www.cmegroup.com/trading-hours.html (2026 schedule).
 */
const DAILY_CLOSE_MIN = 17 * 60; // 17:00 ET — daily settlement / maintenance start
const SUNDAY_REOPEN_MIN = 18 * 60; // 18:00 ET — weekly + daily reopen

const CME_EQUITY_FULL_CLOSURES = new Set([
  "2026-01-01",
  "2026-04-03",
  "2026-12-25",
  "2027-01-01",
]);

const CME_EQUITY_EARLY_CLOSE_MIN: Record<string, number> = {
  "2026-01-19": 13 * 60,
  "2026-02-16": 13 * 60,
  "2026-05-25": 13 * 60,
  "2026-06-19": 13 * 60,
  "2026-07-03": 13 * 60,
  "2026-09-07": 13 * 60,
  "2026-11-26": 13 * 60,
  "2026-11-27": 13 * 60 + 15,
  "2026-12-24": 13 * 60 + 15,
};

function etParts(now: Date): { date: string; day: number; minutes: number } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  const weekdays: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  return {
    date: `${value("year")}-${value("month")}-${value("day")}`,
    day: weekdays[value("weekday")] ?? 0,
    minutes: Number(value("hour")) * 60 + Number(value("minute")),
  };
}

/** Pure session predicate. `now` defaults to the current time; injectable for tests. */
export function isGlobexOpen(now: Date = new Date()): boolean {
  const { date, day, minutes } = etParts(now);

  if (CME_EQUITY_FULL_CLOSURES.has(date)) return false;
  const earlyClose = CME_EQUITY_EARLY_CLOSE_MIN[date];
  if (earlyClose != null && minutes >= earlyClose && minutes < SUNDAY_REOPEN_MIN) return false;

  if (day === 6) return false; // Saturday: closed all day
  if (day === 0) return minutes >= SUNDAY_REOPEN_MIN; // Sunday: opens 18:00 ET
  if (day === 5) return minutes < DAILY_CLOSE_MIN; // Friday: closes 17:00 ET, no reopen
  // Mon-Thu: open except the 17:00-18:00 ET maintenance window.
  return minutes < DAILY_CLOSE_MIN || minutes >= SUNDAY_REOPEN_MIN;
}

export function isGlobexQuoteFresh(
  timestamp: string | null | undefined,
  now: Date = new Date(),
  maxAgeMs = 5 * 60_000,
): boolean {
  if (!isGlobexOpen(now) || !timestamp) return false;
  const quoteMs = Date.parse(timestamp);
  const nowMs = now.getTime();
  return Number.isFinite(quoteMs) && quoteMs <= nowMs + 60_000 && nowMs - quoteMs <= maxAgeMs;
}

/** Hook form — re-evaluates every minute (sufficient for session boundaries). */
export function useGlobexOpen(): boolean {
  const [open, setOpen] = useState<boolean>(false);

  useEffect(() => {
    const check = () => setOpen(isGlobexOpen());
    check();
    const interval = setInterval(check, 60_000);
    return () => clearInterval(interval);
  }, []);

  return open;
}

/** Header strip config — display label ↔ relay subscription root (CME E-minis). */
export const HEADER_FUTURES: ReadonlyArray<{ label: string; symbol: string }> = [
  { label: "ES", symbol: "ES" },
  { label: "NQ", symbol: "NQ" },
  { label: "RTY", symbol: "RTY" },
];
