/**
 * "Is this reading current, and when does it stop being stale?"
 *
 * An EOD indicator is one session behind for most of the day by design — its
 * bar cannot exist until the market prints a close. What the operator actually
 * needs is the pair of facts underneath that: which session the panel is still
 * missing, and when the job that fills it runs. Both are derived — the slot
 * from the systemd timer constants in `refreshSchedule`, the session from the
 * ET clock — so no panel has to carry a cadence string that can drift away
 * from the job that really runs (root CLAUDE.md, UI Copy Rules).
 */

import { lastCompletedSessionDate } from "./marketSession";
import { isUsTradingDay } from "./serviceHealthWindows";
import { nextRefreshUtc, type UtcSchedule } from "./refreshSchedule";

const DAY_MS = 24 * 60 * 60 * 1000;

export type FreshnessRail = {
  /** When the next scheduled run fires. */
  nextSampleAt: Date;
  /** The run before it — the one whose output the panel should already hold. */
  lastSampleAt: Date;
  /** Milliseconds until `nextSampleAt`, floored at zero. */
  msRemaining: number;
  /** Share of the interval between runs already elapsed, 0-1. */
  elapsedFraction: number;
  /** The panel is missing a session the market has already closed. */
  behind: boolean;
  /** That session, or null when the panel is current. */
  awaitingSession: string | null;
  /** Behind, and the run that should have delivered it has already fired. */
  overdue: boolean;
  /** No `asOf` at all — the panel holds no date, which is not "current". */
  unknown: boolean;
  /** How long ago that run fired. Zero unless `overdue`. */
  msOverdue: number;
};

function intervalMs(schedule: UtcSchedule): number {
  return schedule.cadence === "weekly" ? 7 * DAY_MS : DAY_MS;
}

/**
 * The timer carries `RandomizedDelaySec=120`, then the job has to actually
 * run, and the panel polls hourly — so for a while after the slot the writer
 * is legitimately still working. Alarming inside that window is a false page,
 * and a rail that cries wolf every evening stops being read. R-307.
 *
 * Sixty minutes is the sum the finding names: `RandomizedDelaySec=120`, plus
 * the job's own runtime, plus the panel's hourly poll — until a poll has had a
 * chance to land, the rail is looking at data fetched BEFORE the run.
 *
 * Note this is "due", not "late": the countdown already reads `Due` at the
 * slot; this only holds back the ALARM colour.
 */
export const WRITER_GRACE_MS = 60 * 60 * 1000;

const MAX_SESSION_LOOKBACK_DAYS = 10;

/**
 * The last session the exchange actually printed, at `at`.
 *
 * `lastCompletedSessionDate` models a session as any weekday, so on a holiday
 * evening it names a date that will never carry a print and every EOD panel in
 * the app goes amber waiting for it. Walk back to a real trading day using the
 * holiday source of truth the app already ships. R-305.
 *
 * The EARLY-CLOSE half of R-305 is deliberately not implemented here: the
 * client has only the full-closure table (`scripts/config/market_holidays.json`),
 * and early closes are derived server-side from IBKR liquid hours
 * (`scripts/utils/market_calendar.py`). Closing that needs an early-close table
 * on the client; it is not something this function can infer.
 */
function lastPrintedSessionDate(at: Date): string {
  let candidate = lastCompletedSessionDate(at);
  for (let i = 0; i < MAX_SESSION_LOOKBACK_DAYS; i += 1) {
    if (isUsTradingDay(candidate)) return candidate;
    const ms = Date.parse(`${candidate}T12:00:00Z`) - DAY_MS;
    candidate = new Date(ms).toISOString().slice(0, 10);
  }
  return candidate;
}

/**
 * @param asOf   the latest session date the panel holds, `YYYY-MM-DD`
 */
/**
 * The instant of the first scheduled run that should have carried a reading
 * newer than `asOf`. Everything since then is the outage.
 */
function expectedRunFor(schedule: UtcSchedule, asOf: string, lastSampleAt: Date): number {
  // End of the held session's DAY: a session dated D is carried by D's own
  // evening run, so the first run that owed something newer is the next one.
  // Anchoring at noon counted D's own run as missing and over-reported by a
  // full interval.
  const asOfMs = Date.parse(`${asOf.slice(0, 10)}T23:59:59Z`);
  if (Number.isNaN(asOfMs)) return lastSampleAt.getTime();
  const step = intervalMs(schedule);
  let run = lastSampleAt.getTime();
  // Walk back while the previous run still post-dates the held session.
  while (run - step > asOfMs) run -= step;
  return Math.min(run, lastSampleAt.getTime());
}

export function computeFreshnessRail(
  schedule: UtcSchedule,
  asOf: string | null | undefined,
  now: Date = new Date(),
): FreshnessRail {
  const nextSampleAt = nextRefreshUtc(schedule, now);
  const lastSampleAt = new Date(nextSampleAt.getTime() - intervalMs(schedule));

  // A null/empty date is UNKNOWN, not current: the rail used to render the
  // calm state with a ticking countdown over a panel holding no date at all.
  // R-306.
  const unknown = !asOf;

  const latestSession = lastPrintedSessionDate(now);
  const behind = !unknown && asOf! < latestSession;

  // The last run is late only if the session it was meant to carry had already
  // closed when it fired. Asking the ET-aware helper what was complete AT that
  // instant is the same question, without a second copy of the clock math.
  const ranAfterSessionClosed =
    behind && lastPrintedSessionDate(lastSampleAt) >= latestSession;
  const pastGrace = now.getTime() - lastSampleAt.getTime() > WRITER_GRACE_MS;
  const overdue = ranAfterSessionClosed && pastGrace;

  const msRemaining = Math.max(0, nextSampleAt.getTime() - now.getTime());
  const elapsed = now.getTime() - lastSampleAt.getTime();

  return {
    nextSampleAt,
    lastSampleAt,
    msRemaining,
    // A late run reads as a full track: the wait is over and the data is not here.
    elapsedFraction: overdue ? 1 : Math.min(1, Math.max(0, elapsed / intervalMs(schedule))),
    behind,
    awaitingSession: behind ? latestSession : null,
    overdue,
    unknown,
    // Measured from the run that should have carried `asOf`, not from the most
    // recent slot, so a seven-day outage reports seven days rather than being
    // capped at one interval. A twenty-minute miss and a week-long one were
    // indistinguishable. R-308.
    msOverdue: overdue ? Math.max(0, now.getTime() - expectedRunFor(schedule, asOf!, lastSampleAt)) : 0,
  };
}

/**
 * Seconds are noise beyond an hour and the only thing worth reading inside a
 * minute. The timer carries `RandomizedDelaySec=120`, so the real fire time is
 * up to two minutes past the slot — this counts to the earliest one, and the
 * DUE state absorbs the rest.
 */
export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  if (minutes > 0) return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  return `${seconds}s`;
}
