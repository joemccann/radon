"use client";

/**
 * The freshness rail: where an indicator's data stands, and when it moves.
 *
 * An EOD indicator sits one session behind for most of the day by design, so
 * "as of 2026-08-25" on a Wednesday afternoon is correct and still reads as
 * broken. The rail answers the question that actually follows — how long until
 * this is current — as a countdown to the job's own timer, with the wait drawn
 * as a length so staleness is legible before the digits are read.
 *
 * Every figure is derived: the slot from the systemd timer constants pinned in
 * `refreshSchedule`, the session from the ET clock. Nothing here is a cadence
 * string a panel author typed.
 */

import { useEffect, useState } from "react";

import { computeFreshnessRail, formatCountdown } from "@/lib/freshnessRail";
import type { UtcSchedule } from "@/lib/refreshSchedule";

type FreshnessRailProps = {
  schedule: UtcSchedule;
  /** Latest session the panel holds, `YYYY-MM-DD`. */
  asOf: string | null;
  testId: string;
  /** Test id for the as-of value, so existing assertions keep their anchor. */
  asOfTestId?: string;
};

const DAY_MS = 24 * 60 * 60 * 1000;

function localDayKey(date: Date): number {
  return Math.floor(
    new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime() / DAY_MS,
  );
}

/** "Today 18:10" reads faster than a date, and the date is only worth spelling
 *  out once the wait crosses into a day the reader has to think about. */
function targetLabel(target: Date, now: Date): string {
  const time = target.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  const delta = localDayKey(target) - localDayKey(now);
  if (delta === 0) return `Today ${time}`;
  if (delta === 1) return `Tomorrow ${time}`;
  return `${target.toLocaleDateString([], { weekday: "short" })} ${time}`;
}

export default function FreshnessRail({ schedule, asOf, testId, asOfTestId }: FreshnessRailProps) {
  // The clock starts on the client. Reading it during render would put a
  // different countdown in the server HTML than in the first client paint —
  // the hydration-mismatch class this app has already been bitten by.
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const rail = now ? computeFreshnessRail(schedule, asOf, now) : null;
  const state = !rail ? "pending" : rail.overdue ? "overdue" : rail.behind ? "behind" : "current";

  const countdown = rail ? (rail.overdue ? "Due" : formatCountdown(rail.msRemaining)) : "--";
  const note = !rail
    ? null
    : rail.overdue
      ? `${formatCountdown(rail.msOverdue)} past the run`
      : targetLabel(rail.nextSampleAt, now!);
  const anchorNote = !rail
    ? null
    : rail.awaitingSession
      ? `Awaiting ${rail.awaitingSession}`
      : "Current";

  return (
    <div
      className="freshness-rail"
      data-testid={testId}
      data-state={state}
      aria-label={
        rail
          ? rail.overdue
            ? `Data as of ${asOf ?? "unknown"}. The ${targetLabel(rail.lastSampleAt, now!)} sample has not landed.`
            : `Data as of ${asOf ?? "unknown"}. Next sample ${countdown} from now.`
          : undefined
      }
    >
      <div className="freshness-rail-side">
        <div className="freshness-rail-label">As of</div>
        <div className="freshness-rail-anchor" data-testid={asOfTestId}>
          {asOf ?? "---"}
        </div>
        <div className="freshness-rail-note">{anchorNote ?? " "}</div>
      </div>

      {/* The wait as a length. No transition: the fill moves once a second and
          an eased width would smear rather than tick. */}
      <div className="freshness-rail-track" aria-hidden="true">
        <div
          className="freshness-rail-track-fill"
          style={{ width: rail ? `${(rail.elapsedFraction * 100).toFixed(2)}%` : "0%" }}
        />
      </div>

      <div className="freshness-rail-side freshness-rail-side--end">
        <div className="freshness-rail-label">Next sample</div>
        <div className="freshness-rail-countdown" data-testid={`${testId}-countdown`}>
          {countdown}
        </div>
        <div className="freshness-rail-note">{note ?? " "}</div>
      </div>
    </div>
  );
}
