"use client";

import { useEffect, useRef, useState } from "react";
import * as d3 from "d3";

import ChartPanel from "./charts/ChartPanel";
import {
  buildCriHistoryXAxisTickValues,
  shouldRotateCriHistoryXAxisLabels,
} from "./CriHistoryChart";
import { chartSeriesColor } from "@/lib/chartSystem";
import type { BpiChartEntry, BpiThresholds } from "@/lib/bpi";

/**
 * Bullish Percent time series inside the sanctioned d3-svg chart shell
 * (RvRatioChart sibling: same MARGIN idiom, curveMonotoneX, tooltip
 * side-flip, touch overlay, `.chart-empty-state`). Renders the 30/70
 * threshold lines with labels, a subtle warning wash below 30 (the
 * oversold zone the signal fires out of), and positive-token dots where
 * the series crosses UP through 30 — the chart's signal. Cross flags are
 * computed over the FULL history in lib/bpi.ts BEFORE slicing, so a dot
 * at the visible-range boundary is never lost.
 */

interface BpiChartProps {
  /** Visible slice (preset/brush windowed) of the built chart entries. */
  entries: BpiChartEntry[];
  thresholds: BpiThresholds;
  indexSymbol: string;
}

const MARGIN = { top: 20, right: 20, bottom: 44, left: 56 };
const FULL_HEIGHT = 440;
const COMPACT_HEIGHT = 300;
const COMPACT_BREAKPOINT = 700;
const CHART_GRID = "var(--chart-grid, var(--border-dim))";
const CHART_AXIS = "var(--chart-axis, var(--border-dim))";
const CHART_AXIS_MUTED = "var(--chart-axis-muted, var(--text-secondary))";
const OVERSOLD_WASH = "color-mix(in srgb, var(--warning) 10%, transparent)";
const THRESHOLD_STROKE = "color-mix(in srgb, var(--warning) 60%, transparent)";
const CROSS_DOT_FILL = "var(--positive)";

interface TooltipState {
  visible: boolean;
  x: number;
  y: number;
  d: BpiChartEntry | null;
}

function formatBpi(value: number): string {
  return value.toFixed(1);
}

export default function BpiChart({ entries, thresholds, indexSymbol }: BpiChartProps) {
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
      // Ignore zero widths (jsdom, display:none) — keep the last real width.
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

    if (entries.length < 2) return;

    const innerW = width - MARGIN.left - MARGIN.right;
    const innerH = height - MARGIN.top - MARGIN.bottom;

    const g = svg
      .attr("width", width)
      .attr("height", height)
      .append("g")
      .attr("transform", `translate(${MARGIN.left},${MARGIN.top})`);

    const dates = entries.map((d) => new Date(d.date));
    const xScale = d3
      .scaleTime()
      .domain(d3.extent(dates) as [Date, Date])
      .range([0, innerW]);

    // Y domain: visible extent joined with both thresholds so the 30/70
    // lines always render, padded and clamped to the index's 0-100 bounds.
    const bpiExtent = d3.extent(entries, (d) => d.bpi) as [number, number];
    const lo = Math.min(bpiExtent[0], thresholds.oversold);
    const hi = Math.max(bpiExtent[1], thresholds.overbought);
    const pad = (hi - lo) * 0.12 || 5;
    const yScale = d3
      .scaleLinear()
      .domain([Math.max(0, lo - pad), Math.min(100, hi + pad)])
      .range([innerH, 0]);
    const [domainLo] = yScale.domain();

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

    // Oversold zone wash: everything below the 30 line.
    g.append("rect")
      .attr("data-testid", "bpi-oversold-wash")
      .attr("x", 0)
      .attr("width", innerW)
      .attr("y", yScale(thresholds.oversold))
      .attr("height", Math.max(yScale(domainLo) - yScale(thresholds.oversold), 0))
      .attr("fill", OVERSOLD_WASH);

    // Labelled threshold band lines at 30 and 70.
    for (const [value, testId, label] of [
      [thresholds.oversold, "bpi-threshold-30", `${thresholds.oversold} OVERSOLD`],
      [thresholds.overbought, "bpi-threshold-70", `${thresholds.overbought} OVERBOUGHT`],
    ] as const) {
      g.append("line")
        .attr("data-testid", testId)
        .attr("x1", 0)
        .attr("x2", innerW)
        .attr("y1", yScale(value))
        .attr("y2", yScale(value))
        .attr("stroke", THRESHOLD_STROKE)
        .attr("stroke-width", 1)
        .attr("stroke-dasharray", "6,4");
      g.append("text")
        .attr("data-testid", `${testId}-label`)
        .attr("x", innerW - 4)
        .attr("y", yScale(value) - 4)
        .attr("text-anchor", "end")
        .attr("fill", CHART_AXIS_MUTED)
        .attr("font-size", "var(--text-meta)")
        .attr("letter-spacing", "0.08em")
        .attr("font-family", "IBM Plex Mono, monospace")
        .text(label);
    }

    const line = d3
      .line<BpiChartEntry>()
      .x((d) => xScale(new Date(d.date)))
      .y((d) => yScale(d.bpi))
      .curve(d3.curveMonotoneX);

    g.append("path")
      .attr("data-testid", "bpi-line")
      .attr("fill", "none")
      .attr("stroke", chartSeriesColor("primary"))
      .attr("stroke-width", 2)
      .attr("d", line(entries) ?? "");

    // Signal dots: sessions where the series crossed UP through 30.
    g.append("g")
      .attr("data-testid", "bpi-cross-dots")
      .selectAll("circle")
      .data(entries.filter((d) => d.crossUp))
      .enter()
      .append("circle")
      .attr("data-testid", "bpi-cross-dot")
      .attr("cx", (d) => xScale(new Date(d.date)))
      .attr("cy", (d) => yScale(d.bpi))
      .attr("r", 3.5)
      .attr("fill", CROSS_DOT_FILL)
      .attr("stroke", "var(--bg-panel, transparent)")
      .attr("stroke-width", 1);

    // Ringed last point.
    const last = entries[entries.length - 1];
    const lastPoint = g.append("g").attr("data-testid", "bpi-last-point");
    lastPoint
      .append("circle")
      .attr("cx", xScale(new Date(last.date)))
      .attr("cy", yScale(last.bpi))
      .attr("r", 4)
      .attr("fill", "none")
      .attr("stroke", chartSeriesColor("primary"))
      .attr("stroke-width", 1.5);
    lastPoint
      .append("circle")
      .attr("cx", xScale(new Date(last.date)))
      .attr("cy", yScale(last.bpi))
      .attr("r", 2)
      .attr("fill", chartSeriesColor("primary"));

    // Y-axis.
    g.append("g")
      .call(
        d3
          .axisLeft(yScale)
          .tickValues(yTicks)
          .tickFormat((d) => String(d)),
      )
      .call((axis) => {
        axis.select(".domain").remove();
        axis.selectAll(".tick line").attr("stroke", CHART_GRID);
        axis
          .selectAll(".tick text")
          .attr("fill", CHART_AXIS_MUTED)
          .attr("font-size", "var(--text-meta)")
          .attr("font-family", "IBM Plex Mono, monospace");
      });

    g.append("text")
      .attr("data-testid", "bpi-y-label")
      .attr("transform", "rotate(-90)")
      .attr("x", -innerH / 2)
      .attr("y", -MARGIN.left + 12)
      .attr("text-anchor", "middle")
      .attr("fill", CHART_AXIS_MUTED)
      .attr("font-size", "var(--text-meta)")
      .attr("letter-spacing", "0.1em")
      .attr("font-family", "IBM Plex Mono, monospace")
      .text("PERCENT ON P&F BUY SIGNAL");

    // X-axis with the shared sparse-tick idiom.
    const xTickValues = buildCriHistoryXAxisTickValues(dates, innerW);
    const rotateXAxisLabels = shouldRotateCriHistoryXAxisLabels(innerW, xTickValues.length);
    g.append("g")
      .attr("transform", `translate(0,${innerH})`)
      .call(
        d3
          .axisBottom(xScale)
          .tickValues(xTickValues)
          .tickFormat((d) => d3.timeFormat("%b %Y")(d as Date)),
      )
      .call((axis) => {
        axis.select(".domain").attr("stroke", CHART_AXIS);
        axis.selectAll(".tick line").attr("stroke", CHART_GRID);
        axis
          .selectAll(".tick text")
          .attr("fill", CHART_AXIS_MUTED)
          .attr("font-size", "var(--text-meta)")
          .attr("font-family", "IBM Plex Mono, monospace")
          .attr("text-anchor", rotateXAxisLabels ? "end" : "middle")
          .attr("dx", rotateXAxisLabels ? "-0.4em" : "0")
          .attr("dy", rotateXAxisLabels ? "0.6em" : "0.9em")
          .attr("transform", rotateXAxisLabels ? "rotate(-24)" : null);
      });

    // Tooltip overlay — mouse hover + touch drag, bisection over the slice.
    const updateTooltip = (clientX: number, clientY: number, mx: number) => {
      const hoveredDate = xScale.invert(mx);
      const bisect = d3.bisector((d: BpiChartEntry) => new Date(d.date)).left;
      let idx = bisect(entries, hoveredDate);
      idx = Math.max(0, Math.min(entries.length - 1, idx));
      if (idx > 0) {
        const before = entries[idx - 1];
        const after = entries[idx];
        const tBefore = Math.abs(new Date(before.date).getTime() - hoveredDate.getTime());
        const tAfter = Math.abs(new Date(after.date).getTime() - hoveredDate.getTime());
        if (tBefore < tAfter) idx -= 1;
      }
      const svgRect = svgRef.current?.getBoundingClientRect();
      setTooltip({
        visible: true,
        x: clientX - (svgRect?.left ?? 0),
        y: clientY - (svgRect?.top ?? 0),
        d: entries[idx],
      });
    };

    g.append("rect")
      .attr("data-testid", "bpi-overlay")
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
  }, [entries, thresholds, width, height]);

  const showEmpty = entries.length < 2;
  const tooltipSideStyle =
    tooltip.x > width / 2
      ? { right: width - tooltip.x + 12 }
      : { left: tooltip.x + 12 };

  return (
    <ChartPanel
      family="analytical-time-series"
      title={`${indexSymbol} BULLISH PERCENT`}
      legend={[
        { label: "BPI", color: chartSeriesColor("primary") },
        { label: "CROSS UP 30", color: CROSS_DOT_FILL },
      ]}
      className="chart-panel-inline"
      dataTestId="bpi-chart"
    >
      <div ref={containerRef} className="cri-history-chart-shell">
        <div className="chart-surface cri-history-chart-surface">
          {showEmpty ? (
            <div className="chart-empty-state">NO BPI HISTORY</div>
          ) : (
            <svg ref={svgRef} className="cri-history-chart-svg" data-testid="bpi-chart-svg" />
          )}
        </div>

        {tooltip.visible && tooltip.d && (
          <div className="chart-tooltip" style={{ ...tooltipSideStyle, top: tooltip.y - 10 }}>
            <div className="chart-tooltip-date">{tooltip.d.date}</div>
            <div className="chart-tooltip-row">
              <span className="chart-tooltip-label">BPI</span>
              <span className="chart-tooltip-value" style={{ color: chartSeriesColor("primary") }}>
                {formatBpi(tooltip.d.bpi)}
              </span>
            </div>
            {tooltip.d.crossUp && (
              <div className="chart-tooltip-row">
                <span className="chart-tooltip-label">SIGNAL</span>
                <span className="chart-tooltip-value" style={{ color: CROSS_DOT_FILL }}>
                  CROSS UP 30
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </ChartPanel>
  );
}
