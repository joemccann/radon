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
  /** How long ago that run fired. Zero unless `overdue`. */
  msOverdue: number;
};

function intervalMs(schedule: UtcSchedule): number {
  return schedule.cadence === "weekly" ? 7 * DAY_MS : DAY_MS;
}

/**
 * @param asOf   the latest session date the panel holds, `YYYY-MM-DD`
 */
export function computeFreshnessRail(
  schedule: UtcSchedule,
  asOf: string | null | undefined,
  now: Date = new Date(),
): FreshnessRail {
  const nextSampleAt = nextRefreshUtc(schedule, now);
  const lastSampleAt = new Date(nextSampleAt.getTime() - intervalMs(schedule));

  const latestSession = lastCompletedSessionDate(now);
  const behind = Boolean(asOf) && asOf! < latestSession;

  // The last run is late only if the session it was meant to carry had already
  // closed when it fired. Asking the ET-aware helper what was complete AT that
  // instant is the same question, without a second copy of the clock math.
  const overdue = behind && lastCompletedSessionDate(lastSampleAt) >= latestSession;

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
    msOverdue: overdue ? Math.max(0, elapsed) : 0,
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
