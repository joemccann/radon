"use client";

import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import * as d3 from "d3";
import ChartPanel from "./charts/ChartPanel";
import { chartSeriesColor } from "@/lib/chartSystem";
import { formatCor } from "@/lib/cor";
import {
  BREAKDOWN_TRIGGER,
  CORR_WINDOW,
  formatCorr,
  formatVix,
  type VixcorChartRow,
  type VixcorEpisode,
} from "@/lib/vixcor";

/**
 * VIX over the 20-session VIX / COR3M correlation, two panes sharing one
 * x-scale inside a single SVG. The lower pane is scaled to a FIXED domain on
 * purpose: a fixed frame is what makes +0.01 read as a collapse and +0.90 read
 * as the norm, which is the whole editorial job of the panel.
 *
 * Deliberately absent: any arrow, marker or annotation running from a
 * correlation trough to a later VIX high. The forward statistics say the
 * post-breakdown drawup is below the all-session base rate, so an arrow would
 * be the chart claiming something the study rejected. The base-rate block in
 * VixCorPanel carries the honest comparison instead.
 *
 * Spec: docs/indicators/vixcor.md section G.4.
 */

/* ─── Layout ─────────────────────────────────────────── */

const VIX_PANE_HEIGHT = 260;
const CORR_PANE_HEIGHT = 168;
const PANE_GAP = 12;
const TOTAL_HEIGHT = VIX_PANE_HEIGHT + PANE_GAP + CORR_PANE_HEIGHT;

const VIX_MARGIN = { top: 16, right: 48, bottom: 8, left: 52 };
const CORR_MARGIN = { top: 4, right: 48, bottom: 28, left: 52 };

const VIX_INNER_HEIGHT = VIX_PANE_HEIGHT - VIX_MARGIN.top - VIX_MARGIN.bottom;
const CORR_INNER_HEIGHT = CORR_PANE_HEIGHT - CORR_MARGIN.top - CORR_MARGIN.bottom;
const CORR_PANE_TOP = VIX_PANE_HEIGHT + PANE_GAP + CORR_MARGIN.top;

/** Below the narrowest sensible frame the axes collide; clamp rather than fold. */
const MIN_WIDTH = 320;
/** Fixed lower-pane domain. Full-history min is -0.5324, so -0.6 never clips. */
const CORR_DOMAIN: [number, number] = [-0.6, 1.0];
const CORR_TICKS = [-0.5, -0.25, 0, 0.25, 0.5, 0.75, 1.0];
/** Padding either side of the VIX extent, as a share of the extent. */
const VIX_DOMAIN_PAD = 0.04;

/* ─── Tokens ─────────────────────────────────────────── */

const CHART_GRID = "var(--chart-grid, var(--border-dim))";
const CHART_AXIS = "var(--chart-axis, var(--border-dim))";
const CHART_AXIS_MUTED = "var(--chart-axis-muted, var(--text-secondary))";
const EPISODE_FILL = "color-mix(in srgb, var(--dislocation) 12%, transparent)";
const EPISODE_EDGE = "color-mix(in srgb, var(--dislocation) 55%, transparent)";
const THRESHOLD_RULE = "color-mix(in srgb, var(--dislocation) 45%, transparent)";
const MONO = "var(--font-mono)";

type VixCorChartProps = {
  history: VixcorChartRow[];
  episodes: VixcorEpisode[];
  title: string;
  xTickFormat: (d: Date) => string;
};

type HoverState = { index: number; x: number; y: number } | null;

function toTime(date: string): number {
  return new Date(`${date}T00:00:00Z`).getTime();
}

/** Evenly spaced label positions, endpoints always included. */
function pickTickIndices(count: number, innerWidth: number): number[] {
  if (count <= 1) return count === 1 ? [0] : [];
  const maxLabels = Math.max(2, Math.min(7, Math.floor(innerWidth / 110)));
  if (count <= maxLabels) return d3.range(count);
  const step = (count - 1) / (maxLabels - 1);
  const picked = new Set<number>([0, count - 1]);
  for (let i = 0; i < maxLabels; i += 1) picked.add(Math.round(i * step));
  return [...picked].sort((a, b) => a - b);
}

export default function VixCorChart({ history, episodes, title, xTickFormat }: VixCorChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = useState(720);
  const [hover, setHover] = useState<HoverState>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      // A zero measurement (detached / display:none) would collapse the frame
      // and stretch every glyph; keep the last good width instead.
      if (entry && entry.contentRect.width > 0) setMeasured(entry.contentRect.width);
    });
    observer.observe(el);
    const initial = el.getBoundingClientRect().width;
    if (initial > 0) setMeasured(initial);
    return () => observer.disconnect();
  }, []);

  const width = Math.max(measured, MIN_WIDTH);
  const innerWidth = width - VIX_MARGIN.left - VIX_MARGIN.right;

  const frame = useMemo(() => {
    const rows = history.filter(
      (row) => Number.isFinite(toTime(row.date)) && Number.isFinite(row.vix_close),
    );
    if (rows.length < 2) return null;

    const times = rows.map((row) => toTime(row.date));
    const x = d3
      .scaleTime()
      .domain([new Date(times[0]), new Date(times[times.length - 1])])
      .range([0, innerWidth]);

    const vixValues = rows.map((row) => row.vix_close);
    const vixMin = Math.min(...vixValues);
    const vixMax = Math.max(...vixValues);
    const vixPad = (vixMax - vixMin) * VIX_DOMAIN_PAD || 0.5;
    const yVix = d3
      .scaleLinear()
      .domain([vixMin - vixPad, vixMax + vixPad])
      .range([VIX_INNER_HEIGHT, 0]);

    const yCorr = d3.scaleLinear().domain(CORR_DOMAIN).range([CORR_INNER_HEIGHT, 0]);

    const vixPath = d3
      .line<VixcorChartRow>()
      .defined((row) => Number.isFinite(row.vix_close))
      .x((row) => x(new Date(toTime(row.date))))
      .y((row) => yVix(row.vix_close))
      .curve(d3.curveMonotoneX)(rows);

    const corrPath = d3
      .line<VixcorChartRow>()
      .defined((row) => row.corr20 != null && Number.isFinite(row.corr20))
      .x((row) => x(new Date(toTime(row.date))))
      .y((row) => yCorr(row.corr20 as number))
      .curve(d3.curveMonotoneX)(rows);

    return { rows, times, x, yVix, yCorr, vixPath, corrPath };
  }, [history, innerWidth]);

  if (!frame) {
    return (
      <ChartPanel
        family="analytical-time-series"
        title={title}
        legend={[
          { label: "VIX", color: chartSeriesColor("primary") },
          { label: `CORR ${CORR_WINDOW}D`, color: chartSeriesColor("comparison") },
        ]}
        className="chart-panel-inline"
        bodyClassName="vixcor-chart-panel"
        contentClassName="vixcor-chart-content"
        dataTestId="vixcor-chart"
      >
        <div ref={containerRef} className="vixcor-chart-shell">
          <div className="chart-surface vixcor-chart-surface">
            <div className="chart-empty-state vixcor-chart-empty">NO HISTORY AVAILABLE</div>
          </div>
        </div>
      </ChartPanel>
    );
  }

  const { rows, times, x, yVix, yCorr, vixPath, corrPath } = frame;
  const firstDate = rows[0].date;
  const lastDate = rows[rows.length - 1].date;
  const bandTop = VIX_MARGIN.top;
  const bandHeight = CORR_PANE_TOP + CORR_INNER_HEIGHT - bandTop;
  const vixColor = chartSeriesColor("primary");
  const corrColor = chartSeriesColor("comparison");

  const bands = episodes
    .map((episode) => {
      const startDate = episode.start > firstDate ? episode.start : firstDate;
      const endDate = episode.end < lastDate ? episode.end : lastDate;
      const rawStart = x(new Date(toTime(startDate)));
      const rawEnd = x(new Date(toTime(endDate)));
      if (!Number.isFinite(rawStart) || !Number.isFinite(rawEnd)) return null;
      const left = Math.max(0, Math.min(rawStart, innerWidth));
      const right = Math.max(0, Math.min(rawEnd, innerWidth));
      return { episode, left, width: Math.max(right - left, 2) };
    })
    .filter((band): band is { episode: VixcorEpisode; left: number; width: number } => band !== null);

  const tickIndices = pickTickIndices(rows.length, innerWidth);
  const vixTicks = yVix.ticks(4);
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
      title={title}
      legend={[
        { label: "VIX", color: vixColor },
        { label: `CORR ${CORR_WINDOW}D`, color: corrColor },
      ]}
      className="chart-panel-inline"
      bodyClassName="vixcor-chart-panel"
      contentClassName="vixcor-chart-content"
      dataTestId="vixcor-chart"
    >
      <div ref={containerRef} className="vixcor-chart-shell">
        <div className="chart-surface vixcor-chart-surface">
          <svg
            className="vixcor-chart-svg"
            width={width}
            height={TOTAL_HEIGHT}
            role="img"
            aria-label="VIX close above the 20-session VIX and COR3M correlation"
          >
            {/* ── Breakdown episodes, shaded across both panes ── */}
            <g transform={`translate(${VIX_MARGIN.left},0)`}>
              {bands.map((band) => (
                <g key={`band-${band.episode.start}`}>
                  <rect
                    data-testid="vixcor-episode-shade"
                    x={band.left}
                    y={bandTop}
                    width={band.width}
                    height={bandHeight}
                    fill={EPISODE_FILL}
                  />
                  {band.episode.open ? (
                    <line
                      data-testid="vixcor-episode-open"
                      x1={band.left + band.width}
                      x2={band.left + band.width}
                      y1={bandTop}
                      y2={bandTop + bandHeight}
                      stroke={EPISODE_EDGE}
                      strokeWidth={1}
                    />
                  ) : null}
                </g>
              ))}
            </g>

            {/* ── Upper pane: VIX ─────────────────────────────── */}
            <g transform={`translate(${VIX_MARGIN.left},${VIX_MARGIN.top})`}>
              {vixTicks.map((tick) => (
                <g key={`vix-tick-${tick}`}>
                  <line
                    x1={0}
                    x2={innerWidth}
                    y1={yVix(tick)}
                    y2={yVix(tick)}
                    stroke={CHART_GRID}
                    strokeWidth={1}
                  />
                  <text
                    x={-8}
                    y={yVix(tick)}
                    textAnchor="end"
                    dominantBaseline="middle"
                    fontFamily={MONO}
                    fontSize="var(--text-meta)"
                    fill={CHART_AXIS_MUTED}
                  >
                    {formatVix(tick)}
                  </text>
                </g>
              ))}
              {vixPath ? (
                <path d={vixPath} fill="none" stroke={vixColor} strokeWidth={1.5} />
              ) : null}
              <text
                x={0}
                y={-4}
                fontFamily={MONO}
                fontSize="var(--text-meta)"
                letterSpacing="0.08em"
                fill={CHART_AXIS_MUTED}
              >
                VIX
              </text>
            </g>

            {/* ── Lower pane: 20-session correlation ──────────── */}
            <g transform={`translate(${CORR_MARGIN.left},${CORR_PANE_TOP})`}>
              {CORR_TICKS.map((tick) => (
                <text
                  key={`corr-tick-${tick}`}
                  x={-8}
                  y={yCorr(tick)}
                  textAnchor="end"
                  dominantBaseline="middle"
                  fontFamily={MONO}
                  fontSize="var(--text-meta)"
                  fill={CHART_AXIS_MUTED}
                >
                  {tick.toFixed(2)}
                </text>
              ))}
              <line
                data-testid="vixcor-zero-line"
                x1={0}
                x2={innerWidth}
                y1={yCorr(0)}
                y2={yCorr(0)}
                stroke="var(--border-dim)"
                strokeWidth={1}
              />
              <line
                data-testid="vixcor-threshold-line"
                x1={0}
                x2={innerWidth}
                y1={yCorr(BREAKDOWN_TRIGGER)}
                y2={yCorr(BREAKDOWN_TRIGGER)}
                stroke={THRESHOLD_RULE}
                strokeWidth={1}
                strokeDasharray="4 4"
              />
              {corrPath ? (
                <path d={corrPath} fill="none" stroke={corrColor} strokeWidth={1.5} />
              ) : null}
              <text
                x={0}
                y={-6}
                fontFamily={MONO}
                fontSize="var(--text-meta)"
                letterSpacing="0.08em"
                fill={CHART_AXIS_MUTED}
              >
                CORR {CORR_WINDOW}D
              </text>

              {/* Shared x-axis, owned by the lower pane. */}
              <line
                x1={0}
                x2={innerWidth}
                y1={CORR_INNER_HEIGHT}
                y2={CORR_INNER_HEIGHT}
                stroke={CHART_AXIS}
                strokeWidth={1}
              />
              {tickIndices.map((index) => (
                <text
                  key={`x-tick-${rows[index].date}`}
                  x={x(new Date(times[index]))}
                  y={CORR_INNER_HEIGHT + 14}
                  textAnchor="middle"
                  dominantBaseline="hanging"
                  fontFamily={MONO}
                  fontSize="var(--text-meta)"
                  fill={CHART_AXIS_MUTED}
                >
                  {xTickFormat(new Date(times[index]))}
                </text>
              ))}
            </g>

            {/* ── Crosshair + hover surface across both panes ─── */}
            {hovered ? (
              <line
                x1={VIX_MARGIN.left + x(new Date(toTime(hovered.date)))}
                x2={VIX_MARGIN.left + x(new Date(toTime(hovered.date)))}
                y1={bandTop}
                y2={bandTop + bandHeight}
                stroke={CHART_AXIS}
                strokeWidth={1}
                strokeDasharray="3 3"
              />
            ) : null}
            <rect
              x={VIX_MARGIN.left}
              y={bandTop}
              width={Math.max(innerWidth, 0)}
              height={bandHeight}
              fill="transparent"
              onMouseMove={handleMove}
              onMouseLeave={() => setHover(null)}
            />
          </svg>
        </div>

        {hovered && hover ? (
          <div
            className="chart-tooltip vixcor-chart-tooltip"
            style={{
              ...(hover.x > innerWidth / 2
                ? { right: width - hover.x - VIX_MARGIN.left + 16 }
                : { left: hover.x + VIX_MARGIN.left + 16 }),
              top: Math.max(bandTop, hover.y),
            }}
          >
            <div className="chart-tooltip-date">{hovered.date}</div>
            <div className="chart-tooltip-row">
              <span className="chart-tooltip-label">VIX</span>
              <span className="chart-tooltip-value" style={{ color: vixColor }}>
                {formatVix(hovered.vix_close)}
              </span>
            </div>
            <div className="chart-tooltip-row">
              <span className="chart-tooltip-label">COR3M</span>
              <span className="chart-tooltip-value">{formatCor(hovered.cor3m_close)}</span>
            </div>
            <div className="chart-tooltip-row">
              <span className="chart-tooltip-label">CORR {CORR_WINDOW}D</span>
              <span className="chart-tooltip-value" style={{ color: corrColor }}>
                {formatCorr(hovered.corr20)}
              </span>
            </div>
            <div className="chart-tooltip-row">
              <span className="chart-tooltip-label">EPISODE</span>
              <span className="chart-tooltip-value">{hovered.episode ? "YES" : "NO"}</span>
            </div>
          </div>
        ) : null}
      </div>
    </ChartPanel>
  );
}
