"use client";

import {
  cpuTone,
  loopLagTone,
  memAvailTone,
  summarizeHostMetrics,
  type HostMetricsPayload,
  type HostMetricTone,
} from "@/lib/adminHostMetrics";
import { formatRelativeTime } from "@/lib/adminFormat";

/**
 * Host-metrics strip (DUR-12): CPU / memory / FastAPI event-loop lag latest
 * values with a 1h sparkline, plus a radon-* unit restarts tile. Fed by
 * /api/admin/host-metrics (the minutely host_metrics table). Sibling of the
 * ReliabilityStrip — every number is honest: a missing table or silent
 * sampler renders as "--" / "sampler silent", never a fabricated value.
 */
export default function HostMetricsStrip({
  metrics,
}: {
  metrics: HostMetricsPayload | null;
}) {
  const rows = metrics && !metrics.missing && Array.isArray(metrics.rows) ? metrics.rows : [];
  const summary = summarizeHostMetrics(rows);
  const pendingSub = metrics?.missing ? "host_metrics table pending" : "metrics loading";

  if (!summary.latest) {
    return (
      <section className="admin-reliability-strip" data-testid="host-metrics-strip">
        {["Host CPU", "Host memory", "Loop lag", "Unit restarts"].map((label) => (
          <Tile key={label} label={label} tone="neutral" value="--" sub={pendingSub} />
        ))}
      </section>
    );
  }

  const { latest, stale } = summary;
  const sampledAgo = formatRelativeTime(latest.taken_at);
  const staleSub = `sampler silent — last ${sampledAgo}`;

  const cpu = latest.cpu_pct;
  const memAvail = latest.mem_avail_mb;
  const memUsed = latest.mem_used_mb;
  const lag = latest.loop_lag_ms;
  const swap = latest.swap_used_mb ?? 0;

  const unitsTone: HostMetricTone = stale
    ? "neutral"
    : summary.failedUnits.length > 0
      ? "negative"
      : "positive";

  return (
    <section className="admin-reliability-strip" data-testid="host-metrics-strip">
      <Tile
        label="Host CPU"
        tone={stale ? "neutral" : cpuTone(cpu)}
        value={cpu !== null ? `${cpu.toFixed(1)}%` : "--"}
        sub={stale ? staleSub : `load ${latest.load1 ?? "--"} · 1h trend`}
        trend={summary.cpuTrend}
      />
      <Tile
        label="Host memory"
        tone={stale ? "neutral" : memAvailTone(memAvail)}
        value={memUsed !== null ? `${(memUsed / 1024).toFixed(1)} GB used` : "--"}
        sub={
          stale
            ? staleSub
            : `${memAvail !== null ? (memAvail / 1024).toFixed(1) : "--"} GB free${swap > 0 ? ` · swap ${swap.toFixed(0)} MB` : ""}`
        }
        trend={summary.memUsedTrend}
      />
      <Tile
        label="Loop lag"
        tone={stale ? "neutral" : loopLagTone(lag)}
        value={lag !== null ? `${lag.toFixed(lag >= 10 ? 0 : 2)}ms` : "api silent"}
        sub={stale ? staleSub : "FastAPI event loop · 1h trend"}
        trend={summary.loopLagTrend}
      />
      <Tile
        label="Unit restarts"
        tone={unitsTone}
        value={summary.totalRestarts !== null ? `${summary.totalRestarts}` : "--"}
        sub={
          stale
            ? staleSub
            : summary.failedUnits.length > 0
              ? `FAILED: ${summary.failedUnits.join(", ")}`
              : "cumulative NRestarts · no failed units"
        }
      />
    </section>
  );
}

/** Inline 1h sparkline. Pure SVG over the trend series; flat/short series
 * degrade to nothing rather than a misleading line. */
function Sparkline({ points }: { points: number[] }) {
  if (points.length < 2) return null;
  const width = 96;
  const height = 18;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const path = points
    .map((v, i) => {
      const x = (i / (points.length - 1)) * width;
      const y = height - 2 - ((v - min) / span) * (height - 4);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden
      style={{ display: "block", marginTop: 4 }}
    >
      <path
        d={path}
        fill="none"
        stroke="color-mix(in srgb, var(--signal-core) 75%, transparent)"
        strokeWidth={1.25}
      />
    </svg>
  );
}

function Tile({
  label,
  value,
  sub,
  tone,
  trend,
}: {
  label: string;
  value: string;
  sub: string;
  tone: HostMetricTone;
  trend?: number[];
}) {
  return (
    <div
      className={`admin-tile admin-tile-${tone}`}
      data-testid={`tile-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-+$/, "")}`}
    >
      <div className="admin-tile-label">{label}</div>
      <div className="admin-tile-value">
        <span className={`admin-status-dot admin-status-dot-${tone}`} aria-hidden />
        {value}
      </div>
      <div className="admin-tile-sub">{sub}</div>
      {trend ? <Sparkline points={trend} /> : null}
    </div>
  );
}
