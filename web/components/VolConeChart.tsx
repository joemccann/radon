"use client";

import { useEffect, useRef, useState } from "react";
import * as d3 from "d3";

import ChartPanel from "./charts/ChartPanel";
import {
  buildCriHistoryXAxisTickValues,
  shouldRotateCriHistoryXAxisLabels,
} from "./CriHistoryChart";
import { chartSeriesColor } from "@/lib/chartSystem";
import { formatIvPct, type VolConeChartRow } from "@/lib/volCone";

/**
 * Expiry-local vol cone: ATM + 10% OTM call + 10% OTM put lines over the
 * name's 90/10 ATM band. Sibling of BpiChart / RvRatioChart (same shell,
 * curveMonotoneX, tooltip flip, NaN-safe paths). CriHistoryChart is
 * two-series only, so this chart owns the three IV traces + cone wash.
 */

interface VolConeChartProps {
  rows: VolConeChartRow[];
  p10: number | null;
  p90: number | null;
  title: string;
}

type SeriesKey = "atm_iv" | "call_10_iv" | "put_10_iv";

const SERIES: ReadonlyArray<{ key: SeriesKey; label: string; role: "primary" | "caution" | "fault" }> = [
  { key: "atm_iv", label: "ATM", role: "primary" },
  { key: "call_10_iv", label: "10C", role: "caution" },
  { key: "put_10_iv", label: "10P", role: "fault" },
];

const MARGIN = { top: 20, right: 20, bottom: 44, left: 56 };
const FULL_HEIGHT = 440;
const COMPACT_HEIGHT = 300;
const COMPACT_BREAKPOINT = 700;
const CHART_GRID = "var(--chart-grid, var(--border-dim))";
const CHART_AXIS = "var(--chart-axis, var(--border-dim))";
const CHART_AXIS_MUTED = "var(--chart-axis-muted, var(--text-secondary))";
const BAND_WASH = "color-mix(in srgb, var(--chart-axis-muted, var(--text-secondary)) 12%, transparent)";

interface TooltipState {
  visible: boolean;
  x: number;
  y: number;
  d: VolConeChartRow | null;
}

function isFiniteNumber(v: number | null | undefined): v is number {
  return v != null && Number.isFinite(v);
}

function finitePath(d: string | null | undefined): string {
  if (!d || d.includes("NaN")) return "";
  return d;
}

function formatDayTick(date: Date): string {
  return date.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "2-digit",
    timeZone: "UTC",
  });
}

export default function VolConeChart({ rows, p10, p90, title }: VolConeChartProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [tooltip, setTooltip] = useState<TooltipState>({ visible: false, x: 0, y: 0, d: null });
  const [width, setWidth] = useState(400);
  const height = width <= COMPACT_BREAKPOINT ? COMPACT_HEIGHT : FULL_HEIGHT;

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((observed) => {
      const entry = observed[0];
      if (entry && entry.contentRect.width > 0) setWidth(entry.contentRect.width);
    });
    ro.observe(el);
    const initial = el.getBoundingClientRect().width;
    if (initial > 0) setWidth(initial);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    if (rows.length < 2 || width <= 0) return;

    const innerW = width - MARGIN.left - MARGIN.right;
    const innerH = height - MARGIN.top - MARGIN.bottom;
    if (innerW <= 0 || innerH <= 0) return;

    const g = svg
      .attr("width", width)
      .attr("height", height)
      .append("g")
      .attr("transform", `translate(${MARGIN.left},${MARGIN.top})`);

    const dates = rows.map((row) => new Date(row.date));
    const xDomain = d3.extent(dates);
    if (!xDomain[0] || !xDomain[1]) return;

    const xScale = d3.scaleTime().domain(xDomain as [Date, Date]).range([0, innerW]);

    const yValues: number[] = [];
    for (const row of rows) {
      if (isFiniteNumber(row.atm_iv)) yValues.push(row.atm_iv);
      if (isFiniteNumber(row.call_10_iv)) yValues.push(row.call_10_iv);
      if (isFiniteNumber(row.put_10_iv)) yValues.push(row.put_10_iv);
    }
    if (isFiniteNumber(p10)) yValues.push(p10);
    if (isFiniteNumber(p90)) yValues.push(p90);
    if (yValues.length === 0) return;

    const extent = d3.extent(yValues) as [number, number];
    const pad = (extent[1] - extent[0]) * 0.12 || 0.02;
    const yScale = d3.scaleLinear().domain([extent[0] - pad, extent[1] + pad]).range([innerH, 0]);

    const yTicks = yScale.ticks(5);
    g.append("g")
      .selectAll("line")
      .data(yTicks)
      .enter()
      .append("line")
      .attr("x1", 0)
      .attr("x2", innerW)
      .attr("y1", (d) => yScale(d))
      .attr("y2", (d) => yScale(d))
      .attr("stroke", CHART_GRID)
      .attr("stroke-width", 1);

    if (isFiniteNumber(p10) && isFiniteNumber(p90)) {
      const top = yScale(Math.max(p10, p90));
      const bottom = yScale(Math.min(p10, p90));
      g.append("rect")
        .attr("data-testid", "vol-cone-band")
        .attr("x", 0)
        .attr("width", innerW)
        .attr("y", top)
        .attr("height", Math.max(bottom - top, 0))
        .attr("fill", BAND_WASH);

      for (const [value, testId] of [
        [p90, "vol-cone-p90"],
        [p10, "vol-cone-p10"],
      ] as const) {
        g.append("line")
          .attr("data-testid", testId)
          .attr("x1", 0)
          .attr("x2", innerW)
          .attr("y1", yScale(value))
          .attr("y2", yScale(value))
          .attr("stroke", CHART_AXIS_MUTED)
          .attr("stroke-width", 1)
          .attr("stroke-dasharray", "6,4");
      }
    }

    for (const series of SERIES) {
      const line = d3
        .line<VolConeChartRow>()
        .defined((row) => isFiniteNumber(row[series.key]))
        .x((row) => xScale(new Date(row.date)))
        .y((row) => yScale(row[series.key] as number))
        .curve(d3.curveMonotoneX);
      const path = finitePath(line(rows));
      if (!path) continue;
      g.append("path")
        .attr("data-testid", `vol-cone-line-${series.key}`)
        .attr("fill", "none")
        .attr("stroke", chartSeriesColor(series.role))
        .attr("stroke-width", 2)
        .attr("d", path);
    }

    g.append("g")
      .call(
        d3
          .axisLeft(yScale)
          .tickValues(yTicks)
          .tickFormat((d) => formatIvPct(d as number)),
      )
      .call((axis) => {
        axis.select(".domain").remove();
        axis.selectAll(".tick line").attr("stroke", CHART_GRID);
        axis
          .selectAll(".tick text")
          .attr("fill", CHART_AXIS_MUTED)
          .attr("font-size", "10px")
          .attr("font-family", "IBM Plex Mono, monospace");
      });

    const xTickValues = buildCriHistoryXAxisTickValues(dates, innerW);
    const rotateXAxisLabels = shouldRotateCriHistoryXAxisLabels(innerW, xTickValues.length);
    g.append("g")
      .attr("transform", `translate(0,${innerH})`)
      .call(
        d3
          .axisBottom(xScale)
          .tickValues(xTickValues)
          .tickFormat((d) => formatDayTick(d as Date)),
      )
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

    const updateTooltip = (clientX: number, clientY: number, mx: number) => {
      const hoveredDate = xScale.invert(mx);
      const bisect = d3.bisector((row: VolConeChartRow) => new Date(row.date)).left;
      let idx = bisect(rows, hoveredDate);
      idx = Math.max(0, Math.min(rows.length - 1, idx));
      if (idx > 0) {
        const before = rows[idx - 1];
        const after = rows[idx];
        const tBefore = Math.abs(new Date(before.date).getTime() - hoveredDate.getTime());
        const tAfter = Math.abs(new Date(after.date).getTime() - hoveredDate.getTime());
        if (tBefore < tAfter) idx -= 1;
      }
      const svgRect = svgRef.current?.getBoundingClientRect();
      setTooltip({
        visible: true,
        x: clientX - (svgRect?.left ?? 0),
        y: clientY - (svgRect?.top ?? 0),
        d: rows[idx],
      });
    };

    g.append("rect")
      .attr("data-testid", "vol-cone-overlay")
      .attr("width", innerW)
      .attr("height", innerH)
      .attr("fill", "transparent")
      .style("touch-action", "pan-y")
      .on("mousemove", function (event: MouseEvent) {
        const [mx] = d3.pointer(event, this);
        updateTooltip(event.clientX, event.clientY, mx);
      })
      .on("mouseleave", () => setTooltip({ visible: false, x: 0, y: 0, d: null }))
      .on("touchstart touchmove", function (event: TouchEvent) {
        if (event.touches.length === 0) return;
        event.preventDefault();
        const [mx] = d3.pointer(event.touches[0], this);
        const touch = event.touches[0];
        updateTooltip(touch.clientX, touch.clientY, mx);
      })
      .on("touchend touchcancel", () => setTooltip({ visible: false, x: 0, y: 0, d: null }));
  }, [rows, p10, p90, width, height]);

  const showEmpty = rows.length < 2;
  const tooltipSideStyle =
    tooltip.x > width / 2
      ? { right: width - tooltip.x + 12 }
      : { left: tooltip.x + 12 };

  return (
    <ChartPanel
      family="analytical-time-series"
      title={title}
      legend={SERIES.map((series) => ({
        label: series.label,
        color: chartSeriesColor(series.role),
      }))}
      className="chart-panel-inline"
      bodyClassName="cri-history-chart-panel"
      contentClassName="cri-history-chart-content"
      dataTestId="vol-cone-chart"
    >
      <div ref={containerRef} className="cri-history-chart-shell">
        <div className="chart-surface cri-history-chart-surface">
          {showEmpty ? (
            <div className="chart-empty-state cri-history-chart-empty">NO HISTORY AVAILABLE</div>
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
            {SERIES.map((series) => (
              <div key={series.key} className="chart-tooltip-row">
                <span className="chart-tooltip-label">{series.label}</span>
                <span className="chart-tooltip-value" style={{ color: chartSeriesColor(series.role) }}>
                  {formatIvPct(tooltip.d![series.key])}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </ChartPanel>
  );
}
