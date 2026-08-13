"use client";

import { ibAuthSummary, livenessSummary } from "@/lib/adminReliability";
import type { AdminHealthPayload, EdgeHealthStatus, UnitStatus } from "@/lib/adminTypes";

type EdgePayload = (EdgeHealthStatus & { reachable?: boolean }) | null;

/**
 * One-line sticky rollup at the top of the page. Always renders a freshness
 * indicator; when polling has stalled but stale data remains, it says so loudly
 * rather than letting frozen data read as healthy.
 */
export default function SystemStatusBar({
  units,
  health,
  updatedSecsAgo,
  stalled,
  loading = false,
}: {
  units: UnitStatus[];
  edge?: EdgePayload;
  health: AdminHealthPayload | null;
  edgeReachable?: boolean;
  updatedSecsAgo: number | null;
  stalled: boolean;
  loading?: boolean;
}) {
  const liveness = livenessSummary(units);
  const auth = ibAuthSummary(health);
  const allUp = liveness.total > 0 && liveness.ok === liveness.total;
  const rollupTone = loading
    ? "neutral"
    : stalled
      ? "warning"
      : allUp
        ? "positive"
        : liveness.total === 0
          ? "neutral"
          : "negative";

  if (loading) {
    return (
      <div className="admin-status-bar admin-status-bar-neutral" data-testid="system-status-bar">
        <span className="admin-skeleton admin-skeleton-line" style={{ width: 230 }} />
        <span className="admin-status-bar-spacer" />
        <span className="admin-skeleton admin-skeleton-line" style={{ width: 90 }} />
      </div>
    );
  }

  // Edge state intentionally omitted here (the Off-box tile owns it) to keep the
  // sticky summary a single, non-duplicative line.
  return (
    <div className={`admin-status-bar admin-status-bar-${rollupTone}`} data-testid="system-status-bar">
      <span className="admin-status-bar-main">
        <span className={`admin-status-dot admin-status-dot-${rollupTone}`} aria-hidden />
        {liveness.total ? `${liveness.ok}/${liveness.total} OK` : "No units"}
      </span>
      <span className="admin-status-bar-sep">·</span>
      <span>IB {auth.label}</span>
      <span className="admin-status-bar-spacer" />
      {stalled ? (
        <span className="admin-status-bar-stale" data-testid="status-bar-stalled">
          Polling stalled · last good data {fmtAgo(updatedSecsAgo)}
        </span>
      ) : (
        <span className="admin-status-bar-fresh">updated {fmtAgo(updatedSecsAgo)}</span>
      )}
    </div>
  );
}

function fmtAgo(secs: number | null): string {
  if (secs == null) return "freshness unknown";
  if (secs < 2) return "just now";
  if (secs < 60) return `${secs}s ago`;
  return `${Math.floor(secs / 60)}m ago`;
}
