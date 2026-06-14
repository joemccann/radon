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
 * Like useMarketHours, this does NOT special-case CME holidays (~9/year) — on
 * those days the relay simply returns no fresh ticks and the strip shows "---".
 */
const DAILY_CLOSE_MIN = 17 * 60; // 17:00 ET — daily settlement / maintenance start
const SUNDAY_REOPEN_MIN = 18 * 60; // 18:00 ET — weekly + daily reopen

/** Pure session predicate. `now` defaults to the current time; injectable for tests. */
export function isGlobexOpen(now: Date = new Date()): boolean {
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = et.getDay(); // 0=Sun … 6=Sat
  const minutes = et.getHours() * 60 + et.getMinutes();

  if (day === 6) return false; // Saturday: closed all day
  if (day === 0) return minutes >= SUNDAY_REOPEN_MIN; // Sunday: opens 18:00 ET
  if (day === 5) return minutes < DAILY_CLOSE_MIN; // Friday: closes 17:00 ET, no reopen
  // Mon-Thu: open except the 17:00-18:00 ET maintenance window.
  return minutes < DAILY_CLOSE_MIN || minutes >= SUNDAY_REOPEN_MIN;
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
