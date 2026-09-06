"use client";

import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import * as d3 from "d3";
import ChartPanel from "./charts/ChartPanel";
import { buildCriHistoryXAxisTickValues } from "./CriHistoryChart";
import { chartSeriesColor } from "@/lib/chartSystem";
import {
  formatSpreadPct,
  formatVix,
  formatZ,
  type DispersionPoint,
} from "@/lib/dispersion";

/**
 * Three z-scored volatility gauges on ONE axis: the VIX, the 95-5 spread of
 * daily single-stock returns across the S&P 500 seed, and the same spread
 * across the 11 sector SPDRs. Each is a rolling 60-session mean z-scored over
 * the full sample since 2017, so the lines are directly comparable and the
 * "below the surface" read is the visible gap between the stock/sector lines
 * and the VIX line. Dashed reference levels at -1, 0, +1 and +2 carry the
 * regime edges. Descriptive only: nothing here claims forward information.
 *
 * Spec: docs/indicators/dispersion.md section H.
 */

export const DISPERSION_CHART_TITLE = "VOLATILITY DISPERSION - Z-SCORE SINCE 2017";

/* ─── Layout ─────────────────────────────────────────── */

const HEIGHT = 440;
const MARGIN = { top: 16, right: 40, bottom: 28, left: 48 };
const INNER_HEIGHT = HEIGHT - MARGIN.top - MARGIN.bottom;
/** Below the narrowest sensible frame the axes collide; clamp rather than fold. */
const MIN_WIDTH = 320;
/** Padding either side of the z extent, as a share of the extent. */
const DOMAIN_PAD = 0.06;
/** Labelled dashed rules: the regime edges plus the mean. */
const REFERENCE_LEVELS = [-1, 0, 1, 2] as const;
/** Domains wider than this get month-year ticks; shorter ones keep the day. */
const MONTH_TICK_SPAN_MS = 180 * 24 * 60 * 60_000;

/* ─── Tokens ─────────────────────────────────────────── */

const CHART_GRID = "var(--chart-grid, var(--border-dim))";
const CHART_AXIS = "var(--chart-axis, var(--border-dim))";
const CHART_AXIS_MUTED = "var(--chart-axis-muted, var(--text-secondary))";
const REFERENCE_RULE = "color-mix(in srgb, var(--text-muted) 55%, transparent)";
const MEAN_RULE = "var(--border-dim)";
const MONO = "var(--font-mono)";

const formatMonthYear = d3.timeFormat("%b %y");
const formatDayMonth = d3.timeFormat("%d %b");

type SeriesKey = "z_vix" | "z_stock" | "z_sector";

type LineSpec = { key: SeriesKey; label: string; color: string };

const LINES: readonly LineSpec[] = [
  { key: "z_vix", label: "VIX", color: chartSeriesColor("primary") },
  { key: "z_stock", label: "SINGLE STOCK", color: chartSeriesColor("dislocation") },
  { key: "z_sector", label: "CROSS SECTOR", color: chartSeriesColor("comparison") },
];

const LEGEND = LINES.map((line) => ({ label: line.label, color: line.color }));

type DispersionChartProps = {
  /** Already range-sliced series. */
  entries: DispersionPoint[];
};

type HoverState = { index: number; x: number; y: number } | null;

function toTime(date: string): number {
  return new Date(`${date}T00:00:00Z`).getTime();
}

function isFiniteNumber(v: number | null | undefined): v is number {
  return v != null && Number.isFinite(v);
}

function formatReferenceLevel(level: number): string {
  return level === 0 ? "0" : formatZ(level).replace(".00", "");
}

export default function DispersionChart({ entries }: DispersionChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = useState(720);
  const [hover, setHover] = useState<HoverState>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((observed) => {
      const entry = observed[0];
      // A zero measurement (detached / display:none) would collapse the frame;
      // keep the last good width instead.
      if (entry && entry.contentRect.width > 0) setMeasured(entry.contentRect.width);
    });
    observer.observe(el);
    const initial = el.getBoundingClientRect().width;
    if (initial > 0) setMeasured(initial);
    return () => observer.disconnect();
  }, []);

  const width = Math.max(measured, MIN_WIDTH);
  const innerWidth = width - MARGIN.left - MARGIN.right;

  const frame = useMemo(() => {
    const rows = entries.filter((row) => Number.isFinite(toTime(row.date)));
    if (rows.length < 2) return null;

    const times = rows.map((row) => toTime(row.date));
    const x = d3
      .scaleTime()
      .domain([new Date(times[0]), new Date(times[times.length - 1])])
      .range([0, innerWidth]);

    const zValues = rows.flatMap((row) => LINES.map((line) => row[line.key]).filter(isFiniteNumber));
    const zMin = Math.min(...zValues, ...REFERENCE_LEVELS);
    const zMax = Math.max(...zValues, ...REFERENCE_LEVELS);
    const pad = (zMax - zMin) * DOMAIN_PAD || 0.5;
    const y = d3
      .scaleLinear()
      .domain([zMin - pad, zMax + pad])
      .range([INNER_HEIGHT, 0]);

    const paths = LINES.map((line) => ({
      ...line,
      d: d3
        .line<DispersionPoint>()
        .defined((row) => isFiniteNumber(row[line.key]))
        .x((row) => x(new Date(toTime(row.date))))
        .y((row) => y(row[line.key]))(rows),
    }));

    const spanMs = times[times.length - 1] - times[0];
    const tickFormat = spanMs >= MONTH_TICK_SPAN_MS ? formatMonthYear : formatDayMonth;
    const xTicks = buildCriHistoryXAxisTickValues(
      times.map((t) => new Date(t)),
      innerWidth,
    );

    return { rows, times, x, y, paths, xTicks, tickFormat };
  }, [entries, innerWidth]);

  if (!frame) {
    return (
      <ChartPanel
        family="analytical-time-series"
        title={DISPERSION_CHART_TITLE}
        legend={LEGEND}
        className="chart-panel-inline"
        dataTestId="dispersion-chart"
      >
        <div ref={containerRef} className="cri-history-chart-shell">
          <div className="chart-surface cri-history-chart-surface">
            <div className="chart-empty-state cri-history-chart-empty">NO DISPERSION HISTORY</div>
          </div>
        </div>
      </ChartPanel>
    );
  }

  const { rows, times, x, y, paths, xTicks, tickFormat } = frame;
  const yTicks = y.ticks(5);
  const hovered = hover ? rows[hover.index] : null;

  function handleMove(event: ReactMouseEvent<SVGRectElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    if (bounds.width <= 0) return;
    const offset = ((event.clientX - bounds.left) / bounds.width) * innerWidth;
    const at = x.invert(offset).getTime();
    let index = d3.bisectLeft(times, at);
    index = Math.max(0, Math.min(rows.length - 1, index));
    if (index > 0 && Math.abs(times[index - 1] - at) < Math.abs(times[index] - at)) index -= 1;
    setHover({ index, x: x(new Date(times[index])), y: event.clientY - bounds.top });
  }

  return (
    <ChartPanel
      family="analytical-time-series"
      title={DISPERSION_CHART_TITLE}
      legend={LEGEND}
      className="chart-panel-inline"
      dataTestId="dispersion-chart"
    >
      <div ref={containerRef} className="cri-history-chart-shell">
        <div className="chart-surface cri-history-chart-surface">
          <svg
            className="cri-history-chart-svg"
            data-testid="dispersion-chart-svg"
            width={width}
            height={HEIGHT}
            role="img"
            aria-label="VIX, single-stock and cross-sector dispersion z-scores since 2017"
          >
            <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
              {yTicks.map((tick) => (
                <g key={`y-tick-${tick}`}>
                  <line
                    x1={0}
                    x2={innerWidth}
                    y1={y(tick)}
                    y2={y(tick)}
                    stroke={CHART_GRID}
                    strokeWidth={1}
                  />
                  <text
                    x={-8}
                    y={y(tick)}
                    textAnchor="end"
                    dominantBaseline="middle"
                    fontFamily={MONO}
                    fontSize="var(--text-meta)"
                    fill={CHART_AXIS_MUTED}
                  >
                    {formatZ(tick)}
                  </text>
                </g>
              ))}

              {REFERENCE_LEVELS.map((level) => (
                <g key={`ref-${level}`} data-testid={`dispersion-ref-${level}`}>
                  <line
                    x1={0}
                    x2={innerWidth}
                    y1={y(level)}
                    y2={y(level)}
                    stroke={level === 0 ? MEAN_RULE : REFERENCE_RULE}
                    strokeWidth={1}
                    strokeDasharray="4 4"
                  />
                  <text
                    x={innerWidth + 6}
                    y={y(level)}
                    dominantBaseline="middle"
                    fontFamily={MONO}
                    fontSize="var(--text-meta)"
                    letterSpacing="0.06em"
                    fill={CHART_AXIS_MUTED}
                  >
                    {formatReferenceLevel(level)}
                  </text>
                </g>
              ))}

              {paths.map((line) =>
                line.d ? (
                  <path
                    key={line.key}
                    data-testid={`dispersion-line-${line.key}`}
                    d={line.d}
                    fill="none"
                    stroke={line.color}
                    strokeWidth={line.key === "z_stock" ? 1.75 : 1.5}
                  />
                ) : null,
              )}

              <line
                x1={0}
                x2={innerWidth}
                y1={INNER_HEIGHT}
                y2={INNER_HEIGHT}
                stroke={CHART_AXIS}
                strokeWidth={1}
              />
              {xTicks.map((tick) => (
                <text
                  key={`x-tick-${tick.getTime()}`}
                  x={x(tick)}
                  y={INNER_HEIGHT + 14}
                  textAnchor="middle"
                  dominantBaseline="hanging"
                  fontFamily={MONO}
                  fontSize="var(--text-meta)"
                  fill={CHART_AXIS_MUTED}
                >
                  {tickFormat(tick)}
                </text>
              ))}

              {hovered ? (
                <line
                  x1={x(new Date(toTime(hovered.date)))}
                  x2={x(new Date(toTime(hovered.date)))}
                  y1={0}
                  y2={INNER_HEIGHT}
                  stroke={CHART_AXIS}
                  strokeWidth={1}
                  strokeDasharray="3 3"
                />
              ) : null}
              <rect
                data-testid="dispersion-overlay"
                x={0}
                y={0}
                width={Math.max(innerWidth, 0)}
                height={INNER_HEIGHT}
                fill="transparent"
                onMouseMove={handleMove}
                onMouseLeave={() => setHover(null)}
              />
            </g>
          </svg>
        </div>

        {hovered && hover ? (
          <div
            className="chart-tooltip"
            style={{
              ...(hover.x > innerWidth / 2
                ? { right: width - hover.x - MARGIN.left + 16 }
                : { left: hover.x + MARGIN.left + 16 }),
              top: Math.max(MARGIN.top, hover.y),
            }}
          >
            <div className="chart-tooltip-date">{hovered.date}</div>
            {LINES.map((line) => (
              <div key={line.key} className="chart-tooltip-row">
                <span className="chart-tooltip-label">{line.label} Z</span>
                <span className="chart-tooltip-value" style={{ color: line.color }}>
                  {formatZ(hovered[line.key])}
                </span>
              </div>
            ))}
            <div className="chart-tooltip-row">
              <span className="chart-tooltip-label">VIX</span>
              <span className="chart-tooltip-value">{formatVix(hovered.vix)}</span>
            </div>
            <div className="chart-tooltip-row">
              <span className="chart-tooltip-label">STOCK 95-5</span>
              <span className="chart-tooltip-value">{formatSpreadPct(hovered.stock_spread)}</span>
            </div>
            <div className="chart-tooltip-row">
              <span className="chart-tooltip-label">SECTOR 95-5</span>
              <span className="chart-tooltip-value">{formatSpreadPct(hovered.sector_spread)}</span>
            </div>
          </div>
        ) : null}
      </div>
    </ChartPanel>
  );
}
