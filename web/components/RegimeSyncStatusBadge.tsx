"use client";

import { Loader2 } from "lucide-react";

export type RegimeSyncStatusState = "syncing" | "stale" | "live" | "fresh";

const STATUS_LABELS: Record<RegimeSyncStatusState, string> = {
  syncing: "SYNCING",
  stale: "STALE",
  live: "LIVE",
  fresh: "FRESH",
};

type RegimeSyncStatusBadgeProps = {
  state: RegimeSyncStatusState;
  className?: string;
  label?: string;
  testId?: string;
};

export default function RegimeSyncStatusBadge({
  state,
  className,
  label = STATUS_LABELS[state],
  testId,
}: RegimeSyncStatusBadgeProps) {
  const syncing = state === "syncing";
  const classes = [
    "regime-sync-status",
    `regime-sync-status-${state}`,
    className,
  ].filter(Boolean).join(" ");

  return (
    <span
      className={classes}
      data-testid={testId}
      data-state={state}
      aria-live="polite"
      aria-busy={syncing}
    >
      {syncing ? (
        <Loader2
          size={11}
          className="regime-sync-status-icon spin"
          data-testid="regime-sync-working-icon"
          aria-hidden="true"
        />
      ) : null}
      <span>{label}</span>
    </span>
  );
}
