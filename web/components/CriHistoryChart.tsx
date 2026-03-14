"use client";

import { useCallback, useMemo, useRef, useState, useEffect, type MouseEvent } from "react";
import { scaleLinear, scaleTime, type ScaleLinear } from "@/lib/scales";
import { linePath } from "@/lib/svgPath";
import { extent, bisectLeft } from "@/lib/arrayUtils";
import ChartPanel from "./charts/ChartPanel";

const shortDate = (d: Date) => d.toLocaleDateString("en-US", { month: "short", day: "numeric" });

export interface CriHistoryEntry {
  date: string;
  vix: number;
  vvix: number;
  spy: number;
  cor1m?: number;
  realized_vol?: number | null;
  spx_vs_ma_pct: number;
  vix_5d_roc: number;
}

export interface ChartSeries {
  key: keyof CriHistoryEntry;
  label: string;
  color: string;
  axis: "left" | "right";
  format?: (v: number) => string;
}

interface TooltipState {
  visible: boolean;
  x: number;
  y: number;
  d: CriHistoryEntry | null;
}

interface CriHistoryChartProps {
  history: CriHistoryEntry[];
  series: [ChartSeries, ChartSeries];
  title: string;
  liveValues?: Partial<Record<keyof CriHistoryEntry, number>>;
}

const MARGIN = { top: 20, right: 56, bottom: 32, left: 48 };
const HEIGHT = 440;

function defaultFormat(v: number): string {
  return v.toFixed(2);
}

/* ── Pure-React SVG chart (no d3-selection / d3-axis) ────── */

export default function CriHistoryChart({
  history,
  series,
  title,
  liveValues,
}: CriHistoryChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [tooltip, setTooltip] = useState<TooltipState>({ visible: false, x: 0, y: 0, d: null });
  const [width, setWidth] = useState(400);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setWidth(entry.contentRect.width);
    });
    ro.observe(el);
    setWidth(el.getBoundingClientRect().width);
    return () => ro.disconnect();
  }, []);

  // Merge live values into last data point
  const chartData = useMemo(() => {
    if (!history || history.length === 0) return [];
    if (!liveValues || Object.keys(liveValues).length === 0) return history;
    const result = [...history];
    const last = { ...result[result.length - 1] };
    for (const [k, v] of Object.entries(liveValues)) {
      if (v != null) (last as Record<string, unknown>)[k] = v;
    }
    result[result.length - 1] = last;
    return result;
  }, [history, liveValues]);

  const [leftSeries, rightSeries] = series;
  const innerW = width - MARGIN.left - MARGIN.right;
  const innerH = HEIGHT - MARGIN.top - MARGIN.bottom;

  // Scales
  const dates = useMemo(() => chartData.map((d) => new Date(d.date)), [chartData]);
  const xScale = useMemo(
    () => {
      if (dates.length === 0) return scaleTime().domain([new Date(), new Date()]).range([0, innerW]);
      const sorted = dates.slice().sort((a, b) => a.getTime() - b.getTime());
      return scaleTime().domain([sorted[0], sorted[sorted.length - 1]]).range([0, innerW]);
    },
    [dates, innerW],
  );

  const buildYScale = useCallback(
    (s: ChartSeries) => {
      const vals = chartData
        .map((d) => d[s.key] as number | null | undefined)
        .filter((v): v is number => v != null && Number.isFinite(v));
      if (vals.length === 0) return scaleLinear().domain([0, 100]).range([innerH, 0]);
      const ext = extent(vals) as [number, number];
      const pad = (ext[1] - ext[0]) * 0.15 || 2;
      return scaleLinear().domain([ext[0] - pad, ext[1] + pad]).range([innerH, 0]);
    },
    [chartData, innerH],
  );

  const yLeft = useMemo(() => buildYScale(leftSeries), [buildYScale, leftSeries]);
  const yRight = useMemo(() => buildYScale(rightSeries), [buildYScale, rightSeries]);

  // Tick arrays
  const yLeftTicks = useMemo(() => yLeft.ticks(5), [yLeft]);
  const yRightTicks = useMemo(() => yRight.ticks(5), [yRight]);
  const xTicks = useMemo(() => {
    const count = Math.max(2, Math.min(chartData.length, Math.floor(innerW / 50)));
    return xScale.ticks(count);
  }, [xScale, chartData.length, innerW]);

  // Line path generators
  const buildPath = useCallback(
    (s: ChartSeries, yScale: ScaleLinear<number>) => {
      const valid = chartData.filter(
        (d) => d[s.key] != null && Number.isFinite(d[s.key] as number),
      );
      if (valid.length < 2) return { path: null, dots: [], lastDot: null };
      const pathFn = linePath<CriHistoryEntry>()
        .x((d) => xScale(new Date(d.date)))
        .y((d) => yScale(d[s.key] as number));
      const dots = valid.map((d) => ({
        cx: xScale(new Date(d.date)),
        cy: yScale(d[s.key] as number),
      }));
      const hasLive = liveValues && Object.keys(liveValues).length > 0;
      const lastDot = hasLive ? dots[dots.length - 1] : null;
      return { path: pathFn(valid), dots, lastDot };
    },
    [chartData, xScale, liveValues],
  );

  const leftLine = useMemo(() => buildPath(leftSeries, yLeft), [buildPath, leftSeries, yLeft]);
  const rightLine = useMemo(() => buildPath(rightSeries, yRight), [buildPath, rightSeries, yRight]);

  // Tooltip mouse handler
  const onMouseMove = useCallback(
    (e: MouseEvent<SVGRectElement>) => {
      const svg = svgRef.current;
      if (!svg || chartData.length === 0) return;
      const rect = svg.getBoundingClientRect();
      const mx = e.clientX - rect.left - MARGIN.left;
      const hoveredDate = xScale.invert(mx);
      let idx = bisectLeft(chartData, hoveredDate, (d: CriHistoryEntry) => new Date(d.date));
      idx = Math.max(0, Math.min(chartData.length - 1, idx));
      if (idx > 0) {
        const tBefore = Math.abs(new Date(chartData[idx - 1].date).getTime() - hoveredDate.getTime());
        const tAfter = Math.abs(new Date(chartData[idx].date).getTime() - hoveredDate.getTime());
        if (tBefore < tAfter) idx -= 1;
      }
      setTooltip({
        visible: true,
        x: e.clientX - rect.left,
        y: e.clientY - rect.top,
        d: chartData[idx],
      });
    },
    [chartData, xScale],
  );

  const onMouseLeave = useCallback(() => {
    setTooltip({ visible: false, x: 0, y: 0, d: null });
  }, []);

  const showEmpty = chartData.length < 2;
  const leftFmt = leftSeries.format ?? defaultFormat;
  const rightFmt = rightSeries.format ?? defaultFormat;
  const tooltipSideStyle =
    tooltip.x > width / 2 ? { right: width - tooltip.x + 12 } : { left: tooltip.x + 12 };

  return (
    <ChartPanel
      family="analytical-time-series"
      title={title}
      legend={series.map((item) => ({ label: item.label, color: item.color }))}
      className="cp-i"
      bodyClassName="cri-history-chart-panel"
      contentClassName="cri-history-chart-content"
      dataTestId="cri-history-chart"
    >
      <div ref={containerRef} className="cri-history-chart-shell">
        <div className="chart-surface cri-history-chart-surface">
          {showEmpty ? (
            <div className="chart-empty-state cri-history-chart-empty">NO HISTORY AVAILABLE</div>
          ) : (
            <svg ref={svgRef} width={width} height={HEIGHT} className="cri-history-chart-svg">
              <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
                {/* Grid lines */}
                {yLeftTicks.map((t) => (
                  <line
                    key={t}
                    x1={0}
                    x2={innerW}
                    y1={yLeft(t)}
                    y2={yLeft(t)}
                    stroke="var(--chart-grid, var(--border-dim))"
                    strokeWidth={1}
                  />
                ))}

                {/* Left line + dots */}
                {leftLine.path && (
                  <path d={leftLine.path} fill="none" stroke={leftSeries.color} strokeWidth={2} />
                )}
                {leftLine.dots.map((dot, i) => (
                  <circle
                    key={i}
                    cx={dot.cx}
                    cy={dot.cy}
                    r={2}
                    fill={leftSeries.color}
                    stroke="var(--chart-surface, var(--bg-panel))"
                    strokeWidth={1}
                  />
                ))}
                {leftLine.lastDot && (
                  <circle
                    cx={leftLine.lastDot.cx}
                    cy={leftLine.lastDot.cy}
                    r={4}
                    fill={leftSeries.color}
                    stroke={leftSeries.color}
                    strokeWidth={1}
                    opacity={0.5}
                  />
                )}

                {/* Right line + dots */}
                {rightLine.path && (
                  <path d={rightLine.path} fill="none" stroke={rightSeries.color} strokeWidth={2} />
                )}
                {rightLine.dots.map((dot, i) => (
                  <circle
                    key={i}
                    cx={dot.cx}
                    cy={dot.cy}
                    r={2}
                    fill={rightSeries.color}
                    stroke="var(--chart-surface, var(--bg-panel))"
                    strokeWidth={1}
                  />
                ))}
                {rightLine.lastDot && (
                  <circle
                    cx={rightLine.lastDot.cx}
                    cy={rightLine.lastDot.cy}
                    r={4}
                    fill={rightSeries.color}
                    stroke={rightSeries.color}
                    strokeWidth={1}
                    opacity={0.5}
                  />
                )}

                {/* Left Y-axis ticks */}
                {yLeftTicks.map((t) => (
                  <g key={t} transform={`translate(0,${yLeft(t)})`}>
                    <line x1={-6} x2={0} stroke="var(--chart-grid, var(--border-dim))" />
                    <text
                      x={-9}
                      dy="0.32em"
                      textAnchor="end"
                      fill={leftSeries.color}
                      fontSize="10px"
                      fontFamily="IBM Plex Mono, monospace"
                    >
                      {leftFmt(t)}
                    </text>
                  </g>
                ))}

                {/* Right Y-axis ticks */}
                {yRightTicks.map((t) => (
                  <g key={t} transform={`translate(${innerW},${yRight(t)})`}>
                    <line x1={0} x2={6} stroke="var(--chart-grid, var(--border-dim))" />
                    <text
                      x={9}
                      dy="0.32em"
                      textAnchor="start"
                      fill={rightSeries.color}
                      fontSize="10px"
                      fontFamily="IBM Plex Mono, monospace"
                    >
                      {rightFmt(t)}
                    </text>
                  </g>
                ))}

                {/* X-axis */}
                <line x1={0} x2={innerW} y1={innerH} y2={innerH} stroke="var(--chart-axis, var(--border-dim))" />
                {xTicks.map((t) => (
                  <g key={t.getTime()} transform={`translate(${xScale(t)},${innerH})`}>
                    <line y1={0} y2={6} stroke="var(--chart-grid, var(--border-dim))" />
                    <text
                      y={18}
                      textAnchor="middle"
                      fill="var(--chart-axis-muted, var(--text-secondary))"
                      fontSize="10px"
                      fontFamily="IBM Plex Mono, monospace"
                    >
                      {shortDate(t)}
                    </text>
                  </g>
                ))}

                {/* Invisible overlay for tooltip */}
                <rect
                  width={innerW}
                  height={innerH}
                  fill="transparent"
                  onMouseMove={onMouseMove}
                  onMouseLeave={onMouseLeave}
                />
              </g>
            </svg>
          )}
        </div>

        {tooltip.visible && tooltip.d && (
          <div className="chart-tooltip" style={{ ...tooltipSideStyle, top: tooltip.y - 10 }}>
            <div className="chart-tooltip-date">{tooltip.d.date}</div>
            {series.map((s) => {
              const val = tooltip.d![s.key];
              const fmt = s.format ?? defaultFormat;
              return (
                <div key={String(s.key)} className="ct-r">
                  <span className="ct-l">{s.label}</span>
                  <span className="ct-v" style={{ color: s.color }}>
                    {val != null && Number.isFinite(val as number) ? fmt(val as number) : "---"}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </ChartPanel>
  );
}
