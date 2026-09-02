"use client";

import { useRef, useEffect, useMemo, useState } from "react";
import * as d3 from "d3";
import ChartPanel from "./charts/ChartPanel";

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

// The chart was originally CRI-only. The internals are key-driven via d3
// and never reference CRI-specific fields, so we expose it as a generic
// time-series chart over any `{ date: string }` entry. The CRI usage in
// RegimePanel keeps inferring `T = CriHistoryEntry` with zero code change;
// VcgPanel now drives the same component with `T = VcgHistoryEntry`.
export interface ChartSeries<T = CriHistoryEntry> {
  key: keyof T;
  label: string;
  color: string;
  axis: "left" | "right";
  format?: (v: number) => string;
  /** Y-scale for this series; log domains clamp to the smallest positive value. */
  scaleType?: "log" | "linear";
}

interface TooltipState<T> {
  visible: boolean;
  x: number;
  y: number;
  d: T | null;
}

export interface ReferenceLevel {
  value: number;
  label: string;
  color?: string;
}

export interface ReferenceBand {
  from: number;
  to: number;
  label: string;
  color?: string;
  /** Which y-scale the band belongs to; its bounds fold into that scale's domain. */
  axis: "left" | "right";
}

interface CriHistoryChartProps<T extends { date: string }> {
  history: T[];
  series: [ChartSeries<T>, ChartSeries<T>];
  title: string;
  /** Plot both series on ONE y-scale (built from the union of their values
   *  and any reference levels) — for an indicator and its moving average. */
  sharedAxis?: boolean;
  /** Dashed horizontal guide lines on the left scale (e.g. signal zones);
   *  their values are folded into the scale domain so they are always visible. */
  referenceLevels?: ReferenceLevel[];
  /** Shaded horizontal zones (e.g. the MA RATIO 0.25-0.5 signal zone), each
   *  drawn on the scale of its declared axis and folded into that domain. */
  referenceBands?: ReferenceBand[];
  /** Override for today's live values — keys match the entry type fields */
  liveValues?: Partial<Record<keyof T, number>>;
  /** X-axis tick label override; defaults to "%b %-d" (e.g. "Mar 5"). */
  xTickFormat?: (d: Date) => string;
}

const MARGIN = { top: 20, right: 56, bottom: 44, left: 48 };
const HEIGHT = 440;
const CHART_GRID = "var(--chart-grid, var(--border-dim))";
const CHART_AXIS = "var(--chart-axis, var(--border-dim))";
const CHART_AXIS_MUTED = "var(--chart-axis-muted, var(--text-secondary))";
const CHART_SURFACE = "var(--chart-surface, var(--bg-panel))";

function defaultFormat(v: number): string {
  return v.toFixed(2);
}

export function buildCriHistoryXAxisTickValues(dates: Date[], innerWidth: number): Date[] {
  if (dates.length <= 1) return dates;

  const maxLabels = Math.max(4, Math.min(7, Math.floor(innerWidth / 110)));
  if (dates.length <= maxLabels) return dates;

  const step = (dates.length - 1) / (maxLabels - 1);
  const indices = new Set<number>();
  for (let i = 0; i < maxLabels; i += 1) {
    indices.add(Math.round(i * step));
  }
  indices.add(0);
  indices.add(dates.length - 1);

  return [...indices]
    .sort((a, b) => a - b)
    .map((index) => dates[index]);
}

export function shouldRotateCriHistoryXAxisLabels(innerWidth: number, tickCount: number): boolean {
  return tickCount > 5 || innerWidth < 560;
}

export default function CriHistoryChart<T extends { date: string }>({
  history,
  series,
  title,
  liveValues,
  xTickFormat,
  sharedAxis = false,
  referenceLevels,
  referenceBands,
}: CriHistoryChartProps<T>) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [tooltip, setTooltip] = useState<TooltipState<T>>({
    visible: false,
    x: 0,
    y: 0,
    d: null,
  });
  const [width, setWidth] = useState(400);

  // ResizeObserver for responsive width
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

  // Merge live values into the last data point
  const chartData = useMemo<T[]>(() => {
    if (!history || history.length === 0) return [];
    if (!liveValues || Object.keys(liveValues).length === 0) return history;
    const result = [...history];
    const last = { ...result[result.length - 1] };
    for (const [k, v] of Object.entries(liveValues)) {
      if (v != null) {
        (last as Record<string, unknown>)[k] = v;
      }
    }
    result[result.length - 1] = last;
    return result;
  }, [history, liveValues]);

  const [leftSeries, rightSeries] = series;

  useEffect(() => {
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    if (!chartData || chartData.length < 2) return;

    const innerW = width - MARGIN.left - MARGIN.right;
    const innerH = HEIGHT - MARGIN.top - MARGIN.bottom;

    const g = svg
      .attr("width", width)
      .attr("height", HEIGHT)
      .append("g")
      .attr("transform", `translate(${MARGIN.left},${MARGIN.top})`);

    // Parse dates
    const dates = chartData.map((d) => new Date(d.date));

    // Scales
    const xScale = d3
      .scaleTime()
      .domain(d3.extent(dates) as [Date, Date])
      .range([0, innerW]);

    // A value is plottable on a series' scale — log scales reject <= 0.
    function isPlottable(s: ChartSeries<T>, v: number | null | undefined): v is number {
      if (v == null || !Number.isFinite(v)) return false;
      return s.scaleType !== "log" || v > 0;
    }

    // Helper: build Y scale for a series (or, on a shared axis, for both
    // series plus the reference levels).
    function buildYScale(s: ChartSeries<T>): d3.ScaleContinuousNumeric<number, number> {
      const sources = sharedAxis ? [leftSeries, rightSeries] : [s];
      const vals = sources
        .flatMap((src) => chartData.map((d) => d[src.key] as number | null | undefined))
        .filter((v): v is number => isPlottable(s, v));
      // Reference levels draw on the LEFT scale, so they fold into the left
      // domain only (or the shared one). Folding them into an independent
      // right scale dragged a percent axis to a vol-point guide (IV SPREAD:
      // SPX 1M IV read 0..600% against the 5.32 AVG line).
      for (const level of referenceLevels ?? []) {
        if (s.axis !== "left" && !sharedAxis) break;
        if (isPlottable(s, level.value)) vals.push(level.value);
      }
      for (const band of referenceBands ?? []) {
        if (band.axis !== s.axis && !sharedAxis) continue;
        if (isPlottable(s, band.from)) vals.push(band.from);
        if (isPlottable(s, band.to)) vals.push(band.to);
      }
      if (vals.length === 0) return d3.scaleLinear().domain([0, 100]).range([innerH, 0]);
      const ext = d3.extent(vals) as [number, number];
      if (s.scaleType === "log") {
        // `vals` is clamped to strictly positive, so ext[0] is the smallest
        // positive value; multiplicative padding keeps the domain positive.
        return d3.scaleLog().domain([ext[0] / 1.1, ext[1] * 1.1]).range([innerH, 0]);
      }
      const pad = (ext[1] - ext[0]) * 0.15 || 2;
      return d3.scaleLinear().domain([ext[0] - pad, ext[1] + pad]).range([innerH, 0]);
    }

    // Axis/grid tick values — d3's log ticks explode into every mantissa step
    // across multi-decade domains, so use five geometric stops instead.
    function buildYTickValues(
      s: ChartSeries<T>,
      scale: d3.ScaleContinuousNumeric<number, number>,
    ): number[] {
      if (s.scaleType !== "log") return scale.ticks(5);
      const [lo, hi] = scale.domain();
      return d3.range(5).map((i) => lo * Math.pow(hi / lo, i / 4));
    }

    const yLeft = buildYScale(leftSeries);
    const yRight = sharedAxis ? yLeft : buildYScale(rightSeries);
    const leftTickValues = buildYTickValues(leftSeries, yLeft);
    const rightTickValues = buildYTickValues(rightSeries, yRight);

    // Grid lines (based on left axis)
    const gridLines = leftTickValues;
    g.append("g")
      .selectAll("line")
      .data(gridLines)
      .enter()
      .append("line")
      .attr("x1", 0)
      .attr("x2", innerW)
      .attr("y1", (d) => yLeft(d))
      .attr("y2", (d) => yLeft(d))
      .attr("stroke", CHART_GRID)
      .attr("stroke-width", 1);

    // Draw a line series
    function drawLine(
      s: ChartSeries<T>,
      yScale: d3.ScaleContinuousNumeric<number, number>,
    ) {
      const validData = chartData.filter((d) =>
        isPlottable(s, d[s.key] as number | null | undefined),
      );
      if (validData.length < 2) return;

      const line = d3
        .line<T>()
        .x((d) => xScale(new Date(d.date)))
        .y((d) => yScale(d[s.key] as number))
        .curve(d3.curveMonotoneX);

      g.append("path")
        .datum(validData)
        .attr("fill", "none")
        .attr("stroke", s.color)
        .attr("stroke-width", 2)
        .attr("d", line);

      // Dots
      g.selectAll(`.dot-${String(s.key)}`)
        .data(validData)
        .enter()
        .append("circle")
        .attr("class", `dot-${String(s.key)}`)
        .attr("cx", (d) => xScale(new Date(d.date)))
        .attr("cy", (d) => yScale(d[s.key] as number))
        .attr("r", 2)
        .attr("fill", s.color)
        .attr("stroke", CHART_SURFACE)
        .attr("stroke-width", 1);

      // Highlight the last dot (live) with a larger radius and a pulse ring
      const lastValid = validData[validData.length - 1];
      if (liveValues && Object.keys(liveValues).length > 0 && lastValid) {
        g.append("circle")
          .attr("cx", xScale(new Date(lastValid.date)))
          .attr("cy", yScale(lastValid[s.key] as number))
          .attr("r", 4)
          .attr("fill", s.color)
          .attr("stroke", s.color)
          .attr("stroke-width", 1)
          .attr("opacity", 0.5);
      }
    }

    // Reference bands: shaded zones under the data lines, on the scale of
    // their declared axis, with dashed edge guides and a right-edge label.
    for (const band of referenceBands ?? []) {
      const scale = band.axis === "left" || sharedAxis ? yLeft : yRight;
      const bandSeries = band.axis === "left" ? leftSeries : rightSeries;
      if (!isPlottable(bandSeries, band.from) || !isPlottable(bandSeries, band.to)) continue;
      const color = band.color ?? CHART_AXIS_MUTED;
      const yTop = scale(Math.max(band.from, band.to));
      const yBottom = scale(Math.min(band.from, band.to));
      g.append("rect")
        .attr("class", "reference-band")
        .attr("data-testid", "chart-reference-band")
        .attr("x", 0)
        .attr("width", innerW)
        .attr("y", yTop)
        .attr("height", Math.max(0, yBottom - yTop))
        .attr("fill", `color-mix(in srgb, ${color} 12%, transparent)`);
      for (const edge of [band.from, band.to]) {
        g.append("line")
          .attr("class", "reference-band-edge")
          .attr("x1", 0)
          .attr("x2", innerW)
          .attr("y1", scale(edge))
          .attr("y2", scale(edge))
          .attr("stroke", color)
          .attr("stroke-width", 1)
          .attr("stroke-dasharray", "4 4");
      }
      g.append("text")
        .attr("x", innerW - 4)
        .attr("y", yTop - 4)
        .attr("text-anchor", "end")
        .attr("fill", color)
        .attr("font-size", 9)
        .attr("font-family", "var(--font-mono)")
        .text(band.label);
    }

    // Reference levels: dashed guides under the data lines, labelled at the
    // right edge so they read as zones rather than as a third series.
    for (const level of referenceLevels ?? []) {
      if (!isPlottable(leftSeries, level.value)) continue;
      const y = yLeft(level.value);
      const color = level.color ?? CHART_AXIS_MUTED;
      g.append("line")
        .attr("class", "reference-level")
        .attr("x1", 0)
        .attr("x2", innerW)
        .attr("y1", y)
        .attr("y2", y)
        .attr("stroke", color)
        .attr("stroke-width", 1)
        .attr("stroke-dasharray", "4 4");
      g.append("text")
        .attr("x", innerW - 4)
        .attr("y", y - 4)
        .attr("text-anchor", "end")
        .attr("fill", color)
        .attr("font-size", 9)
        .attr("font-family", "var(--font-mono)")
        .text(level.label);
    }

    drawLine(leftSeries, yLeft);
    drawLine(rightSeries, yRight);

    // Left Y-axis
    const leftFormat = leftSeries.format ?? defaultFormat;
    g.append("g")
      .call(
        d3
          .axisLeft(yLeft)
          .tickValues(leftTickValues)
          .tickFormat((d) => leftFormat(d as number)),
      )
      .call((axis) => {
        axis.select(".domain").remove();
        axis.selectAll(".tick line").attr("stroke", CHART_GRID);
        axis
          .selectAll(".tick text")
          .attr("fill", leftSeries.color)
          .attr("font-size", "10px")
          .attr("font-family", "IBM Plex Mono, monospace");
      });

    // Right Y-axis
    const rightFormat = rightSeries.format ?? defaultFormat;
    g.append("g")
      .attr("transform", `translate(${innerW},0)`)
      .call(
        d3
          .axisRight(yRight)
          .tickValues(rightTickValues)
          .tickFormat((d) => rightFormat(d as number)),
      )
      .call((axis) => {
        axis.select(".domain").remove();
        axis.selectAll(".tick line").attr("stroke", CHART_GRID);
        axis
          .selectAll(".tick text")
          .attr("fill", rightSeries.color)
          .attr("font-size", "10px")
          .attr("font-family", "IBM Plex Mono, monospace");
      });

    // X-axis — use explicit sparse ticks so labels stay legible on 20-session charts
    const xTickValues = buildCriHistoryXAxisTickValues(dates, innerW);
    const rotateXAxisLabels = shouldRotateCriHistoryXAxisLabels(innerW, xTickValues.length);
    const xAxis = d3
      .axisBottom(xScale)
      .tickValues(xTickValues)
      .tickFormat((d) => (xTickFormat ?? d3.timeFormat("%b %-d"))(d as Date));

    g.append("g")
      .attr("transform", `translate(0,${innerH})`)
      .call(xAxis)
      .call((axis) => {
        axis.select(".domain").attr("stroke", CHART_AXIS);
        axis.selectAll(".tick line").attr("stroke", CHART_GRID);
        axis
          .selectAll(".tick text")
          .attr("fill", CHART_AXIS_MUTED)
          .attr("font-size", "10px")
          .attr("font-family", "IBM Plex Mono, monospace")
          .attr("text-anchor", rotateXAxisLabels ? "end" : "middle")
          .attr("dx", rotateXAxisLabels ? "-0.4em" : "0")
          .attr("dy", rotateXAxisLabels ? "0.6em" : "0.9em")
          .attr("transform", rotateXAxisLabels ? "rotate(-24)" : null);
      });

    // Invisible overlay for tooltip — supports both mouse hover and touch drag.
    const updateTooltip = (clientX: number, clientY: number, mx: number) => {
      const hoveredDate = xScale.invert(mx);
      const bisect = d3.bisector((d: T) => new Date(d.date)).left;
      let idx = bisect(chartData, hoveredDate);
      idx = Math.max(0, Math.min(chartData.length - 1, idx));
      if (idx > 0) {
        const before = chartData[idx - 1];
        const after = chartData[idx];
        const tBefore = Math.abs(new Date(before.date).getTime() - hoveredDate.getTime());
        const tAfter = Math.abs(new Date(after.date).getTime() - hoveredDate.getTime());
        if (tBefore < tAfter) idx = idx - 1;
      }
      const entry = chartData[idx];
      const svgRect = svgRef.current?.getBoundingClientRect();
      const ex = clientX - (svgRect?.left ?? 0);
      const ey = clientY - (svgRect?.top ?? 0);
      setTooltip({ visible: true, x: ex, y: ey, d: entry });
    };

    g.append("rect")
      .attr("width", innerW)
      .attr("height", innerH)
      .attr("fill", "transparent")
      .style("touch-action", "pan-y")
      .on("mousemove", function (event: MouseEvent) {
        const [mx] = d3.pointer(event, this);
        updateTooltip(event.clientX, event.clientY, mx);
      })
      .on("mouseleave", function () {
        setTooltip({ visible: false, x: 0, y: 0, d: null });
      })
      .on("touchstart touchmove", function (event: TouchEvent) {
        if (event.touches.length === 0) return;
        event.preventDefault();
        const [mx] = d3.pointer(event.touches[0], this);
        const t = event.touches[0];
        updateTooltip(t.clientX, t.clientY, mx);
      })
      .on("touchend touchcancel", function () {
        setTooltip({ visible: false, x: 0, y: 0, d: null });
      });
  }, [chartData, width, series, leftSeries, rightSeries, xTickFormat, sharedAxis, referenceLevels, referenceBands]);

  const showEmpty = !chartData || chartData.length < 2;
  const tooltipSideStyle =
    tooltip.x > width / 2
      ? { right: width - tooltip.x + 12 }
      : { left: tooltip.x + 12 };

  return (
    <ChartPanel
      family="analytical-time-series"
      title={title}
      legend={series.map((item) => ({ label: item.label, color: item.color }))}
      className="chart-panel-inline"
      bodyClassName="cri-history-chart-panel"
      contentClassName="cri-history-chart-content"
      dataTestId="cri-history-chart"
    >
      <div ref={containerRef} className="cri-history-chart-shell">
        <div className="chart-surface cri-history-chart-surface">
          {showEmpty ? (
            <div className="chart-empty-state cri-history-chart-empty">
              NO HISTORY AVAILABLE
            </div>
          ) : (
            <svg ref={svgRef} className="cri-history-chart-svg" />
          )}
        </div>

        {tooltip.visible && tooltip.d && (
          <div
            className="chart-tooltip"
            style={{
              ...tooltipSideStyle,
              top: tooltip.y - 10,
            }}
          >
            <div className="chart-tooltip-date">{tooltip.d.date}</div>
            {series.map((s) => {
              const val = tooltip.d![s.key];
              const fmt = s.format ?? defaultFormat;
              return (
                <div key={String(s.key)} className="chart-tooltip-row">
                  <span className="chart-tooltip-label">{s.label}</span>
                  <span className="chart-tooltip-value" style={{ color: s.color }}>
                    {val != null && Number.isFinite(val as number)
                      ? fmt(val as number)
                      : "---"}
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
