"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as d3 from "d3";
import ChartPanel from "./charts/ChartPanel";
import { chartSeriesColor } from "@/lib/chartSystem";
import type { StreakEntry } from "@/lib/streaks";

/**
 * Two-pane daily chart in the style of the streak reference plate: the
 * close series on a log scale above a histogram of consecutive daily
 * gains, sharing one x index scale and one hover. Reuses the
 * `.regime-relationship-*` grid/axis classes and `.chart-tooltip` so the
 * visual stays in lockstep with the other regime signal charts.
 */

interface StreaksChartProps {
  points: StreakEntry[];
  title: string;
  /** Total SVG height in px. */
  height?: number;
}

const DEFAULT_HEIGHT = 460;
const MARGIN = { top: 16, right: 20, bottom: 32, left: 56 };
/** Vertical gap between the price pane and the streak pane. */
const PANE_GAP = 26;
/** Price pane share of the plot height (the histogram gets the rest). */
const PRICE_PANE_RATIO = 0.56;

function buildTickIndices(length: number, count: number): number[] {
  if (length <= count) return Array.from({ length }, (_, i) => i);
  const step = (length - 1) / (count - 1);
  const set = new Set<number>();
  for (let i = 0; i < count; i += 1) set.add(Math.round(step * i));
  set.add(0);
  set.add(length - 1);
  return Array.from(set).sort((a, b) => a - b);
}

function formatPriceTick(v: number): string {
  if (!Number.isFinite(v)) return "";
  if (v >= 10_000) return `${(v / 1000).toFixed(1)}k`;
  if (v >= 100) return v.toFixed(0);
  if (v >= 1) return v.toFixed(2);
  return v.toFixed(4);
}

interface HoverState {
  index: number;
  x: number;
}

export default function StreaksChart({
  points,
  title,
  height = DEFAULT_HEIGHT,
}: StreaksChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<HoverState | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setWidth(entry.contentRect.width);
    });
    ro.observe(el);
    setWidth(el.getBoundingClientRect().width || 640);
    return () => ro.disconnect();
  }, []);

  // NaN guard: only finite positive closes survive (the log scale needs > 0).
  const finite = useMemo(
    () =>
      points.filter(
        (p) =>
          Number.isFinite(p.close) && p.close > 0 && Number.isFinite(p.streak),
      ),
    [points],
  );

  const innerWidth = Math.max(width - MARGIN.left - MARGIN.right, 0);
  const plotHeight = Math.max(height - MARGIN.top - MARGIN.bottom - PANE_GAP, 0);
  const priceHeight = Math.round(plotHeight * PRICE_PANE_RATIO);
  const streakHeight = Math.max(plotHeight - priceHeight, 0);
  const streakTop = priceHeight + PANE_GAP;

  const xAt = useMemo(() => {
    const n = finite.length;
    return (i: number) => (n > 1 ? (i / (n - 1)) * innerWidth : 0);
  }, [finite.length, innerWidth]);

  const priceScale = useMemo(() => {
    if (finite.length === 0) {
      return { lnMin: 0, lnMax: 1 };
    }
    const closes = finite.map((p) => p.close);
    let lnMin = Math.log(Math.min(...closes) * 0.995);
    let lnMax = Math.log(Math.max(...closes) * 1.005);
    if (lnMax - lnMin < 1e-9) {
      lnMin -= 0.01;
      lnMax += 0.01;
    }
    return { lnMin, lnMax };
  }, [finite]);

  const yPrice = useMemo(() => {
    const { lnMin, lnMax } = priceScale;
    return (v: number) =>
      priceHeight - ((Math.log(v) - lnMin) / (lnMax - lnMin)) * priceHeight;
  }, [priceScale, priceHeight]);

  const priceTicks = useMemo(() => {
    const { lnMin, lnMax } = priceScale;
    return Array.from({ length: 5 }, (_, i) =>
      Math.exp(lnMin + (i / 4) * (lnMax - lnMin)),
    );
  }, [priceScale]);

  const maxStreakScale = useMemo(
    () => Math.max(4, ...finite.map((p) => p.streak)),
    [finite],
  );
  const yStreak = useMemo(
    () => (v: number) => streakHeight - (v / maxStreakScale) * streakHeight,
    [maxStreakScale, streakHeight],
  );
  const streakTicks = useMemo(() => {
    const step = Math.max(1, Math.ceil(maxStreakScale / 4));
    const ticks: number[] = [];
    for (let v = 0; v <= maxStreakScale; v += step) ticks.push(v);
    return ticks;
  }, [maxStreakScale]);

  const linePath = useMemo(() => {
    if (finite.length < 2) return "";
    return (
      d3
        .line<StreakEntry>()
        .x((_p, i) => xAt(i))
        .y((p) => yPrice(p.close))(finite) ?? ""
    );
  }, [finite, xAt, yPrice]);

  const tickIndices = useMemo(() => {
    const maxTicks = Math.max(4, Math.min(7, Math.floor(innerWidth / 110)));
    return buildTickIndices(finite.length, maxTicks);
  }, [finite.length, innerWidth]);

  const dateFormatter = useMemo(() => {
    if (finite.length < 2) {
      return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });
    }
    const spanDays =
      (Date.parse(finite[finite.length - 1].date) - Date.parse(finite[0].date)) /
      86_400_000;
    return new Intl.DateTimeFormat(
      "en-US",
      spanDays > 730
        ? { month: "short", year: "numeric" }
        : { month: "short", day: "numeric" },
    );
  }, [finite]);

  const formatDateLabel = (value: string): string => {
    const date = new Date(`${value}T00:00:00`);
    return Number.isNaN(date.getTime()) ? value : dateFormatter.format(date);
  };

  const priceColor = chartSeriesColor("primary");
  const barColor = chartSeriesColor("comparison");
  const barWidth =
    finite.length > 0 ? Math.max(innerWidth / finite.length - 0.5, 0.75) : 0;
  const latest = finite[finite.length - 1] ?? null;

  function handleMove(event: React.MouseEvent<SVGRectElement>) {
    if (finite.length === 0 || innerWidth <= 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - rect.left) / rect.width;
    const idx = Math.max(
      0,
      Math.min(finite.length - 1, Math.round(ratio * (finite.length - 1))),
    );
    setHover({ index: idx, x: xAt(idx) });
  }

  const legend = [
    { label: "DAILY CLOSE (LOG)", color: priceColor },
    { label: "CONSECUTIVE DAILY GAINS", color: barColor },
  ];

  return (
    <ChartPanel
      family="analytical-time-series"
      title={title}
      legend={legend}
      className="chart-panel-inline"
      dataTestId="streaks-chart-panel"
    >
      <div
        ref={containerRef}
        className="streaks-chart-shell"
        style={{ position: "relative", width: "100%" }}
      >
        {finite.length < 2 ? (
          <div className="chart-empty-state">NO HISTORY AVAILABLE</div>
        ) : (
          <svg width={width} height={height} data-testid="streaks-chart">
            <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
              {/* ── Price pane (log) ─────────────────────── */}
              {priceTicks.map((tick, i) => (
                <g key={`p-${i}`}>
                  <line
                    x1={0}
                    x2={innerWidth}
                    y1={yPrice(tick)}
                    y2={yPrice(tick)}
                    className="regime-relationship-grid-line"
                  />
                  <text
                    x={-10}
                    y={yPrice(tick) + 4}
                    textAnchor="end"
                    className="regime-relationship-axis-label"
                  >
                    {formatPriceTick(tick)}
                  </text>
                </g>
              ))}
              <path
                d={linePath}
                fill="none"
                stroke={priceColor}
                strokeWidth={1.5}
              />
              {latest && (
                <circle
                  cx={xAt(finite.length - 1)}
                  cy={yPrice(latest.close)}
                  r={3.5}
                  fill={priceColor}
                />
              )}

              {/* ── Streak pane (bars) ───────────────────── */}
              <g transform={`translate(0,${streakTop})`}>
                {streakTicks.map((tick) => (
                  <g key={`s-${tick}`}>
                    <line
                      x1={0}
                      x2={innerWidth}
                      y1={yStreak(tick)}
                      y2={yStreak(tick)}
                      className="regime-relationship-grid-line"
                    />
                    <text
                      x={-10}
                      y={yStreak(tick) + 4}
                      textAnchor="end"
                      className="regime-relationship-axis-label"
                    >
                      {tick}
                    </text>
                  </g>
                ))}
                {finite.map((p, i) =>
                  p.streak > 0 ? (
                    <rect
                      key={`bar-${p.date}`}
                      className="streaks-bar"
                      x={xAt(i) - barWidth / 2}
                      y={yStreak(p.streak)}
                      width={barWidth}
                      height={Math.max(streakHeight - yStreak(p.streak), 1)}
                      fill={barColor}
                      opacity={0.75}
                    />
                  ) : null,
                )}
                <line
                  x1={0}
                  x2={innerWidth}
                  y1={streakHeight}
                  y2={streakHeight}
                  className="regime-relationship-baseline"
                />
                {/* X-axis ticks under the streak pane */}
                {tickIndices.map((i) => (
                  <g key={`x-${i}`}>
                    <line
                      x1={xAt(i)}
                      x2={xAt(i)}
                      y1={streakHeight}
                      y2={streakHeight + 6}
                      className="regime-relationship-axis-tick"
                    />
                    <text
                      x={xAt(i)}
                      y={streakHeight + 20}
                      textAnchor="middle"
                      className="regime-relationship-axis-label"
                    >
                      {formatDateLabel(finite[i]?.date ?? "")}
                    </text>
                  </g>
                ))}
              </g>

              {/* ── Shared hover ─────────────────────────── */}
              {hover && (
                <line
                  x1={hover.x}
                  x2={hover.x}
                  y1={0}
                  y2={streakTop + streakHeight}
                  className="regime-relationship-hover-line"
                />
              )}
              <rect
                x={0}
                y={0}
                width={innerWidth}
                height={streakTop + streakHeight}
                fill="transparent"
                onMouseMove={handleMove}
                onMouseLeave={() => setHover(null)}
              />
            </g>
          </svg>
        )}

        {hover && finite[hover.index] && (
          <div
            className="chart-tooltip"
            style={{
              position: "absolute",
              ...(hover.x + MARGIN.left > width / 2
                ? { right: width - (hover.x + MARGIN.left) + 12 }
                : { left: hover.x + MARGIN.left + 12 }),
              top: MARGIN.top + 8,
            }}
          >
            <div className="chart-tooltip-date">{finite[hover.index].date}</div>
            <div className="chart-tooltip-row">
              <span className="chart-tooltip-label">CLOSE</span>
              <span className="chart-tooltip-value" style={{ color: priceColor }}>
                {finite[hover.index].close.toLocaleString("en-US", {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}
              </span>
            </div>
            <div className="chart-tooltip-row">
              <span className="chart-tooltip-label">STREAK</span>
              <span className="chart-tooltip-value" style={{ color: barColor }}>
                {finite[hover.index].streak}
              </span>
            </div>
          </div>
        )}
      </div>
    </ChartPanel>
  );
}
