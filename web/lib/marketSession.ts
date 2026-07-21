/**
 * Most-recent expected trading-session date (ET, YYYY-MM-DD).
 *
 * Regime staleness checks compare a cached payload's session date against
 * "the session we should have data for". Using the raw CALENDAR date breaks on
 * weekends and pre-open: on Saturday the calendar date is Sat but the latest
 * session is Friday, so `sessionDate !== today` falsely flags finalized Friday
 * data as stale and fires a scan every off-hours tab open/poll — burning UW/IB
 * quota for data that cannot have changed.
 *
 * This returns the session whose data we legitimately expect:
 *  - weekday at/after the 09:30 ET open  → today (intraday or just-closed)
 *  - weekday before the open (pre-market) → the previous trading day
 *  - weekend                              → the previous trading day (Friday)
 *
 * Holidays are not modelled (no holiday calendar on the client); a holiday
 * weekday resolves to itself, which at worst triggers one stray scan that day.
 * That is acceptable versus the weekend/overnight scan storm this prevents.
 */

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function fmtLocalDate(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const OPEN_MINUTES = 9 * 60 + 30;
const CLOSE_MINUTES = 16 * 60;

function sessionDateAtBoundary(now: Date, boundaryMinutes: number): string {
  // A Date whose LOCAL fields equal the ET wall-clock.
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = et.getDay(); // 0=Sun .. 6=Sat
  const minutes = et.getHours() * 60 + et.getMinutes();
  const isWeekday = day >= 1 && day <= 5;

  if (isWeekday && minutes >= boundaryMinutes) return fmtLocalDate(et);

  // Before the boundary, or weekend → walk back to the previous weekday.
  const d = new Date(et);
  do {
    d.setDate(d.getDate() - 1);
  } while (d.getDay() === 0 || d.getDay() === 6);
  return fmtLocalDate(d);
}

export function mostRecentSessionDate(now: Date = new Date()): string {
  return sessionDateAtBoundary(now, OPEN_MINUTES);
}

/**
 * Most-recent COMPLETED trading-session date (ET, YYYY-MM-DD): today counts
 * only at/after the 16:00 ET close. Intraday, today's daily close does not
 * exist yet, so the previous trading day is the latest session an EOD
 * (daily-close) indicator may treat as final. Same holiday caveat as
 * `mostRecentSessionDate`.
 */
export function lastCompletedSessionDate(now: Date = new Date()): string {
  return sessionDateAtBoundary(now, CLOSE_MINUTES);
}
