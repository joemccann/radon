"use client";

import { useId, useMemo, useState } from "react";

export type DailyDarkPoolRow = {
  date?: string;
  flow_direction?: string;
  flow_strength?: number;
  dp_buy_ratio?: number | null;
  num_prints?: number;
};

type Props = {
  daily: DailyDarkPoolRow[];
  /** Rows shown before progressive disclosure. */
  previewCount?: number;
  compact?: boolean;
};

const PREVIEW_DEFAULT = 5;

function buyPct(row: DailyDarkPoolRow): number | null {
  return typeof row.dp_buy_ratio === "number" ? Math.round(row.dp_buy_ratio * 100) : null;
}

function directionClass(direction?: string): string {
  if (direction === "ACCUMULATION") return "accum";
  if (direction === "DISTRIBUTION") return "distrib";
  return "neutral";
}

function DailyTable({ rows }: { rows: DailyDarkPoolRow[] }) {
  return (
    <table className="ticker-flow-daily">
      <thead>
        <tr>
          <th>Date</th>
          <th>Direction</th>
          <th>Strength</th>
          <th>Buy %</th>
          <th>Prints</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((d) => {
          const pct = buyPct(d);
          return (
            <tr key={d.date ?? JSON.stringify(d)}>
              <td className="mono">{d.date ?? "--"}</td>
              <td>
                <span className={`pill ${directionClass(d.flow_direction)}`}>
                  {(d.flow_direction ?? "NEUTRAL").replace("_", " ")}
                </span>
              </td>
              <td className="mono">{d.flow_strength ?? "--"}</td>
              <td className="mono">{pct == null ? "--" : `${pct}%`}</td>
              <td className="mono">{d.num_prints ?? "--"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Chronological buy-% chart for the disclosed history window. */
function DailyBuyRatioChart({ rows }: { rows: DailyDarkPoolRow[] }) {
  const uid = useId().replace(/:/g, "");
  // Chart left→right = oldest→newest (opposite of the newest-first table).
  const points = useMemo(() => {
    return rows
      .slice()
      .sort((a, b) => (a.date ?? "").localeCompare(b.date ?? ""))
      .map((row) => ({
        date: row.date ?? "",
        buy: buyPct(row),
        strength: typeof row.flow_strength === "number" ? row.flow_strength : null,
        direction: row.flow_direction ?? "NEUTRAL",
      }))
      .filter((p) => p.date && p.buy != null) as Array<{
      date: string;
      buy: number;
      strength: number | null;
      direction: string;
    }>;
  }, [rows]);

  if (points.length === 0) {
    return (
      <div className="ticker-flow-history-chart-empty" data-testid="daily-dp-history-chart-empty">
        No buy-ratio series for chart (missing session ratios).
      </div>
    );
  }

  const W = 640;
  const H = 160;
  const pad = { top: 12, right: 12, bottom: 28, left: 36 };
  const innerW = W - pad.left - pad.right;
  const innerH = H - pad.top - pad.bottom;
  const n = points.length;
  const xAt = (i: number) => pad.left + (n === 1 ? innerW / 2 : (i / (n - 1)) * innerW);
  const yAt = (buy: number) => pad.top + ((100 - buy) / 100) * innerH;

  const linePath = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(p.buy).toFixed(1)}`)
    .join(" ");

  const tickIdx =
    n <= 5
      ? points.map((_, i) => i)
      : [0, Math.floor((n - 1) / 2), n - 1];

  const yTicks = [0, 25, 50, 75, 100];

  return (
    <div className="ticker-flow-history-chart" data-testid="daily-dp-history-chart">
      <div className="ticker-flow-history-chart-title">Buy % by session</div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label="Daily dark pool buy percentage over sessions"
        className="ticker-flow-history-chart-svg"
      >
        <defs>
          <linearGradient id={`dp-buy-fill-${uid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--signal-core, #05AD98)" stopOpacity="0.28" />
            <stop offset="100%" stopColor="var(--signal-core, #05AD98)" stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {/* Grid + accumulation band (55–100) */}
        <rect
          x={pad.left}
          y={yAt(100)}
          width={innerW}
          height={Math.max(0, yAt(55) - yAt(100))}
          fill="var(--signal-core, #05AD98)"
          opacity={0.06}
        />
        <rect
          x={pad.left}
          y={yAt(45)}
          width={innerW}
          height={Math.max(0, yAt(0) - yAt(45))}
          fill="var(--negative, #e35d6a)"
          opacity={0.06}
        />

        {yTicks.map((t) => (
          <g key={t}>
            <line
              x1={pad.left}
              x2={pad.left + innerW}
              y1={yAt(t)}
              y2={yAt(t)}
              className="ticker-flow-history-chart-grid"
            />
            <text x={pad.left - 6} y={yAt(t) + 3} textAnchor="end" className="ticker-flow-history-chart-axis">
              {t}
            </text>
          </g>
        ))}

        {/* 50% mid reference */}
        <line
          x1={pad.left}
          x2={pad.left + innerW}
          y1={yAt(50)}
          y2={yAt(50)}
          className="ticker-flow-history-chart-mid"
        />

        {/* Area under buy% line */}
        <path
          d={`${linePath} L ${xAt(n - 1).toFixed(1)} ${yAt(0).toFixed(1)} L ${xAt(0).toFixed(1)} ${yAt(0).toFixed(1)} Z`}
          fill={`url(#dp-buy-fill-${uid})`}
        />
        <path d={linePath} className="ticker-flow-history-chart-line" fill="none" />

        {points.map((p, i) => (
          <circle
            key={p.date}
            cx={xAt(i)}
            cy={yAt(p.buy)}
            r={3}
            className={
              p.direction === "ACCUMULATION"
                ? "ticker-flow-history-chart-dot accum"
                : p.direction === "DISTRIBUTION"
                  ? "ticker-flow-history-chart-dot distrib"
                  : "ticker-flow-history-chart-dot neutral"
            }
          >
            <title>
              {p.date}: buy {p.buy}%
              {p.strength != null ? `, strength ${p.strength}` : ""} ({p.direction})
            </title>
          </circle>
        ))}

        {tickIdx.map((i) => (
          <text
            key={points[i].date}
            x={xAt(i)}
            y={H - 8}
            textAnchor="middle"
            className="ticker-flow-history-chart-axis"
          >
            {points[i].date.slice(5)}
          </text>
        ))}
      </svg>
    </div>
  );
}

/**
 * Daily dark-pool session table with progressive disclosure.
 * Preview shows the most recent N sessions; expand reveals the full
 * lookback window plus a buy-% chart of the same rows.
 */
export default function DailyDarkPoolHistory({
  daily,
  previewCount = PREVIEW_DEFAULT,
  compact = false,
}: Props) {
  const [expanded, setExpanded] = useState(false);

  const sortedNewestFirst = useMemo(
    () => daily.slice().sort((a, b) => (b.date ?? "").localeCompare(a.date ?? "")),
    [daily],
  );

  if (sortedNewestFirst.length === 0) return null;

  const canExpand = sortedNewestFirst.length > previewCount;
  const visible = expanded || !canExpand
    ? sortedNewestFirst
    : sortedNewestFirst.slice(0, previewCount);
  const hiddenCount = Math.max(0, sortedNewestFirst.length - previewCount);

  return (
    <section className={`section${compact ? " ticker-flow-history-compact" : ""}`} data-testid="daily-dp-history">
      <div className="section-header">
        <div className="section-title">Daily Dark Pool History</div>
        <span className="pill neutral">{sortedNewestFirst.length} SESSIONS</span>
      </div>
      <div className="section-body">
        <DailyTable rows={visible} />

        {canExpand && (
          <div className="ticker-flow-history-disclosure">
            <button
              type="button"
              className="ticker-flow-history-toggle"
              data-testid="daily-dp-history-toggle"
              aria-expanded={expanded}
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded
                ? "Show recent sessions only"
                : `Show full ${sortedNewestFirst.length}-session history (+${hiddenCount})`}
            </button>
          </div>
        )}

        {expanded && canExpand && (
          <DailyBuyRatioChart rows={sortedNewestFirst} />
        )}

        {/* When the full set already fits the preview, still chart if >1 session */}
        {!canExpand && sortedNewestFirst.length > 1 && (
          <DailyBuyRatioChart rows={sortedNewestFirst} />
        )}
      </div>
    </section>
  );
}
