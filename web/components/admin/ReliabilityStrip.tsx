"use client";

import {
  externalProbeSummary,
  freshnessSummary,
  ibAuthSummary,
  livenessSummary,
  reliabilityRollup,
  RELIABILITY_WINDOW_MS,
  type HistoryRollup,
  type ReliabilityHistoryPayload,
} from "@/lib/adminReliability";
import { formatRelativeTime, formatUptime } from "@/lib/adminFormat";
import type { AdminHealthPayload, EdgeHealthStatus, UnitStatus } from "@/lib/adminTypes";

type EdgePayload = (EdgeHealthStatus & { reachable?: boolean }) | null;
type Tone = "positive" | "warning" | "negative" | "neutral";

// Worst-service uptime thresholds for the 7d tile tone.
const UPTIME_POSITIVE_AT_LEAST = 99;
const UPTIME_WARNING_AT_LEAST = 95;

/**
 * The Reliability Strip: four instantaneous tiles (liveness, freshness, IB
 * auth, off-box probe) plus four time-series tiles (uptime %, MTTR,
 * transitions, last deploy) computed from the append-only
 * service_health_events history (migration 0011). Every number is honest:
 * missing history renders as "--", never a fabricated value.
 */
export default function ReliabilityStrip({
  units,
  edge,
  health,
  edgeReachable,
  history = null,
  loading = false,
}: {
  units: UnitStatus[];
  edge: EdgePayload;
  health: AdminHealthPayload | null;
  edgeReachable: boolean;
  history?: ReliabilityHistoryPayload | null;
  loading?: boolean;
}) {
  if (loading) {
    return (
      <section className="admin-reliability-strip" data-testid="reliability-strip">
        {["Liveness", "Freshness", "IB Auth", "Off-box probe"].map((label) => (
          <div className="admin-tile admin-tile-neutral" key={label}>
            <div className="admin-tile-label">{label}</div>
            <div className="admin-tile-value">
              <span className="admin-skeleton admin-skeleton-line" style={{ width: 72, height: 16 }} />
            </div>
            <div className="admin-tile-sub">
              <span className="admin-skeleton admin-skeleton-line" style={{ width: 100 }} />
            </div>
          </div>
        ))}
      </section>
    );
  }

  const liveness = livenessSummary(units);
  const rows = edge?.service_health?.rows ?? [];
  const freshness = freshnessSummary(rows);
  const auth = ibAuthSummary(health);
  const probe = externalProbeSummary(edge?.external_probe ?? null);

  const livenessTone: Tone =
    liveness.total === 0 ? "neutral" : liveness.ok === liveness.total ? "positive" : "negative";
  const freshnessTone: Tone = !edgeReachable
    ? "neutral"
    : freshness.total === 0
      ? "neutral"
      : freshness.stale === 0
        ? "positive"
        : "warning";
  const probeTone: Tone =
    probe.state === "healthy" ? "positive" : probe.state === "down" ? "negative" : "neutral";

  return (
    <>
      <section className="admin-reliability-strip" data-testid="reliability-strip">
        <Tile
          label="Liveness"
          tone={livenessTone}
          value={liveness.total ? `${liveness.ok}/${liveness.total}` : "--"}
          sub={liveness.total ? "units running now" : "no units"}
        />
        <Tile
          label="Freshness"
          tone={freshnessTone}
          value={
            !edgeReachable ? "Unknown" : freshness.total === 0 ? "--" : freshness.stale === 0 ? "All fresh" : `${freshness.stale} stale`
          }
          sub={
            !edgeReachable
              ? "edge unreachable"
              : freshness.total === 0
                ? "no writers reported"
                : freshness.stale > 0
                  ? freshness.staleServices.slice(0, 3).join(", ")
                  : `${freshness.total} writers current`
          }
        />
        <Tile label="IB Auth" tone={auth.tone} value={auth.label} sub="gateway session" />
        <Tile
          label="Off-box probe"
          tone={probeTone}
          value={
            probe.state === "healthy"
              ? probe.latencyMs != null
                ? `${probe.latencyMs}ms`
                : "OK"
              : probe.state === "down"
                ? "Down"
                : probe.state === "stale"
                  ? "Prober silent"
                  : "Unknown"
          }
          sub={probe.state === "healthy" ? "last probe latency" : "Tier-3 external witness"}
        />
      </section>
      <HistoryStrip history={history} />
    </>
  );
}

/** The 7-day time-series row, fed by /api/admin/reliability. */
function HistoryStrip({ history }: { history: ReliabilityHistoryPayload | null }) {
  const windowDays = Math.round((history?.window_ms ?? RELIABILITY_WINDOW_MS) / 86_400_000) || 7;
  const pendingSub = history?.missing ? "history table pending" : "history loading";
  // Defensive: only score a payload that actually carries the events array
  // (a proxy error body or pre-migration payload must degrade, not crash).
  const usable = history && !history.missing && Array.isArray(history.events);
  const rollup: HistoryRollup | null = usable
    ? reliabilityRollup(history.events, {
        windowStartMs: Date.parse(history.since) || Date.now() - RELIABILITY_WINDOW_MS,
        windowEndMs: Date.now(),
        baseline: typeof history.baseline === "object" && history.baseline !== null ? history.baseline : {},
      })
    : null;

  if (!rollup) {
    return (
      <section className="admin-reliability-strip" data-testid="reliability-history-strip">
        {[`Uptime (${windowDays}d)`, `MTTR (${windowDays}d)`, `Transitions (${windowDays}d)`, "Last deploy"].map((label) => (
          <Tile key={label} label={label} tone="neutral" value="--" sub={pendingSub} />
        ))}
      </section>
    );
  }

  const worst = rollup.worstUptime;
  const uptimeTone: Tone =
    worst === null
      ? "neutral"
      : worst.uptimePct >= UPTIME_POSITIVE_AT_LEAST
        ? "positive"
        : worst.uptimePct >= UPTIME_WARNING_AT_LEAST
          ? "warning"
          : "negative";
  const unresolved = rollup.incidents - rollup.resolvedIncidents;
  const mttrTone: Tone = rollup.incidents === 0 ? "positive" : unresolved > 0 ? "warning" : "neutral";
  const lastDeploy = rollup.deploys[0] ?? null;

  return (
    <section className="admin-reliability-strip" data-testid="reliability-history-strip">
      <Tile
        label={`Uptime (${windowDays}d)`}
        tone={uptimeTone}
        value={worst ? `${worst.uptimePct.toFixed(worst.uptimePct >= 99.995 ? 0 : 2)}%` : "--"}
        sub={worst ? `worst: ${worst.service}` : "no transitions recorded yet"}
      />
      <Tile
        label={`MTTR (${windowDays}d)`}
        tone={mttrTone}
        value={rollup.mttrMs !== null ? formatUptime(Math.round(rollup.mttrMs / 1000)) : "--"}
        sub={
          rollup.incidents === 0
            ? "no incidents"
            : `${rollup.incidents} incident${rollup.incidents === 1 ? "" : "s"} · ${rollup.resolvedIncidents} resolved`
        }
      />
      <Tile
        label={`Transitions (${windowDays}d)`}
        tone="neutral"
        value={`${rollup.totalTransitions}`}
        sub={rollup.topFlapper ? `most: ${rollup.topFlapper.service} (${rollup.topFlapper.transitions})` : "steady states all week"}
      />
      <Tile
        label="Last deploy"
        tone={lastDeploy ? "positive" : "neutral"}
        value={lastDeploy ? formatRelativeTime(lastDeploy.at) : "--"}
        sub={lastDeploy?.sha ? lastDeploy.sha.slice(0, 7) : "none in window"}
      />
    </section>
  );
}

function Tile({ label, value, sub, tone }: { label: string; value: string; sub: string; tone: Tone }) {
  return (
    <div className={`admin-tile admin-tile-${tone}`} data-testid={`tile-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-+$/, "")}`}>
      <div className="admin-tile-label">{label}</div>
      <div className="admin-tile-value">
        <span className={`admin-status-dot admin-status-dot-${tone}`} aria-hidden />
        {value}
      </div>
      <div className="admin-tile-sub">{sub}</div>
    </div>
  );
}
