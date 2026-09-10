"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as d3 from "d3";
import { buildChartXAxisTickIndices, chartXAxisTickAnchor } from "@/lib/chartXAxis";

/**
 * Single-series time-series chart with positive/negative band coloring,
 * a smooth line overlay, and a latest-value dot. Mirrors the visual
 * treatment of the "Correlation Risk Premium" chart on /regime/cri
 * (built by RegimeRelationshipView) so any signal-vs-zero chart in
 * the regime tabs reads the same way.
 *
 * Reuses the `.regime-relationship-*` CSS classes for grid lines,
 * baseline, axis labels, and the spread line/marker so the visual
 * stays in sync if those tokens evolve.
 */

export interface SignalAreaPoint {
  date: string;
  value: number | null;
}

interface SignalAreaChartProps {
  data: SignalAreaPoint[];
  /** Format function for the y-axis tick labels (e.g. "+1.45"). */
  formatValue?: (v: number) => string;
  /** Format function for the latest-value tooltip header. */
  formatTooltipValue?: (v: number) => string;
  /** Optional zero-line label suppression. */
  hideZeroLabel?: boolean;
  /** Override height in pixels. Defaults to 360. */
  height?: number;
  /** Pass-through testid hook for the SVG element. */
  dataTestId?: string;
}

const DEFAULT_HEIGHT = 360;
// Matches the CRI "Correlation Risk Premium" chart (RegimeRelationshipView)
// so every regime signal-history chart shares the same plot gutter.
const MARGIN = { top: 16, right: 20, bottom: 32, left: 44 };
const DATE_FORMATTER = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });

function defaultFormat(v: number): string {
  return v.toFixed(2);
}

function formatDateLabel(value: string): string {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return DATE_FORMATTER.format(date);
}

interface HoverState {
  index: number;
  x: number;
  y: number;
}

export default function SignalAreaChart({
  data,
  formatValue = defaultFormat,
  formatTooltipValue,
  hideZeroLabel,
  height = DEFAULT_HEIGHT,
  dataTestId,
}: SignalAreaChartProps) {
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

  // Filter to points with a finite value — the model's burn-in window
  // (OLS + Z) produces nulls at the start, which we drop entirely
  // rather than rendering a 0-valued bar at the leading edge.
  const points = useMemo(
    () => data.filter((p) => p.value != null && Number.isFinite(p.value)) as Array<{ date: string; value: number }>,
    [data],
  );

  const innerWidth = Math.max(width - MARGIN.left - MARGIN.right, 0);
  const innerHeight = Math.max(height - MARGIN.top - MARGIN.bottom, 0);

  const xScale = useMemo(
    () =>
      d3
        .scaleLinear()
        .domain([0, Math.max(points.length - 1, 1)])
        .range([0, innerWidth]),
    [points.length, innerWidth],
  );

  const yScale = useMemo(() => {
    if (points.length === 0) return d3.scaleLinear().domain([-1, 1]).range([innerHeight, 0]);
    const maxAbs = Math.max(...points.map((p) => Math.abs(p.value)), 0.5);
    const pad = maxAbs * 0.18;
    return d3
      .scaleLinear()
      .domain([-(maxAbs + pad), maxAbs + pad])
      .range([innerHeight, 0])
      .nice(5);
  }, [points, innerHeight]);

  const linePath = useMemo(() => {
    if (points.length < 2) return "";
    return (
      d3
        .line<{ date: string; value: number }>()
        .x((_p, i) => xScale(i))
        .y((p) => yScale(p.value))
        .curve(d3.curveMonotoneX)(points) ?? ""
    );
  }, [points, xScale, yScale]);

  const tickIndices = useMemo(() => {
    return buildChartXAxisTickIndices(points.length, innerWidth);
  }, [points.length, innerWidth]);

  const latest = points[points.length - 1] ?? null;
  const zeroY = yScale(0);
  const barWidth = points.length > 0 ? Math.max(innerWidth / points.length - 1, 1) : 0;
  const tooltipFmt = formatTooltipValue ?? formatValue;

  function handleMove(event: React.MouseEvent<SVGRectElement>) {
    if (points.length === 0) return;
    const rect = (event.currentTarget as SVGRectElement).getBoundingClientRect();
    const localX = event.clientX - rect.left;
    const ratio = innerWidth > 0 ? localX / innerWidth : 0;
    const idx = Math.max(0, Math.min(points.length - 1, Math.round(ratio * (points.length - 1))));
    setHover({ index: idx, x: xScale(idx), y: yScale(points[idx].value) });
  }

  function handleLeave() {
    setHover(null);
  }

  if (points.length < 2) {
    return (
      <div ref={containerRef} className="signal-area-chart-shell">
        <div className="signal-area-chart-empty">NO HISTORY AVAILABLE</div>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="signal-area-chart-shell">
      <svg
        width={width}
        height={height}
        data-testid={dataTestId}
        className="signal-area-chart-svg"
      >
        <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
          {/* Y-axis grid + labels */}
          {yScale.ticks(5).map((tick) => (
            <g key={`y-${tick}`}>
              <line
                x1={0}
                x2={innerWidth}
                y1={yScale(tick)}
                y2={yScale(tick)}
                className="regime-relationship-grid-line"
              />
              {!(tick === 0 && hideZeroLabel) && (
                <text
                  x={-10}
                  y={yScale(tick) + 4}
                  textAnchor="end"
                  className="regime-relationship-axis-label"
                >
                  {formatValue(tick)}
                </text>
              )}
            </g>
          ))}

          {/* Zero baseline */}
          <line
            x1={0}
            x2={innerWidth}
            y1={zeroY}
            y2={zeroY}
            className="regime-relationship-baseline"
          />

          {/* Positive/negative bars */}
          {points.map((p, i) => {
            const cx = xScale(i);
            const y = yScale(p.value);
            const fill = p.value >= 0 ? "var(--positive)" : "var(--negative)";
            return (
              <rect
                key={`bar-${p.date}`}
                x={cx - barWidth / 2}
                y={Math.min(y, zeroY)}
                width={barWidth}
                height={Math.max(Math.abs(zeroY - y), 1)}
                fill={fill}
                opacity={0.22}
              />
            );
          })}

          {/* Line */}
          <path d={linePath} className="regime-relationship-line regime-relationship-line-spread" />

          {/* Latest dot */}
          {latest && (
            <circle
              cx={xScale(points.length - 1)}
              cy={yScale(latest.value)}
              r={5}
              className="regime-relationship-marker regime-relationship-marker-spread"
            />
          )}

          {/* X-axis ticks */}
          {tickIndices.map((i, tickIndex) => (
            <g key={`x-${i}`}>
              <line
                x1={xScale(i)}
                x2={xScale(i)}
                y1={innerHeight}
                y2={innerHeight + 6}
                className="regime-relationship-axis-tick"
              />
              <text
                x={xScale(i)}
                y={innerHeight + 20}
                textAnchor={chartXAxisTickAnchor(tickIndex, tickIndices.length)}
                className="regime-relationship-axis-label"
              >
                {formatDateLabel(points[i]?.date ?? "")}
              </text>
            </g>
          ))}

          {/* Hover guide */}
          {hover && (
            <>
              <line
                x1={hover.x}
                x2={hover.x}
                y1={0}
                y2={innerHeight}
                className="regime-relationship-hover-line"
              />
              <circle
                cx={hover.x}
                cy={hover.y}
                r={4}
                className="regime-relationship-marker regime-relationship-marker-spread"
              />
            </>
          )}

          {/* Hover capture */}
          <rect
            x={0}
            y={0}
            width={innerWidth}
            height={innerHeight}
            fill="transparent"
            onMouseMove={handleMove}
            onMouseLeave={handleLeave}
            className="regime-relationship-chart-overlay"
          />
        </g>
      </svg>

      {hover && points[hover.index] && (
        <div
          className="chart-tooltip"
          style={{
            position: "absolute",
            left:
              hover.x + MARGIN.left > width / 2
                ? undefined
                : hover.x + MARGIN.left + 12,
            right:
              hover.x + MARGIN.left > width / 2
                ? width - (hover.x + MARGIN.left) + 12
                : undefined,
            top: hover.y + MARGIN.top + 4,
          }}
        >
          <div className="chart-tooltip-date">{points[hover.index].date}</div>
          <div className="chart-tooltip-row">
            <span className="chart-tooltip-value">
              {tooltipFmt(points[hover.index].value)}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
