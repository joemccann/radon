"use client";

import { useEffect, useId, useRef, useState } from "react";
import * as d3 from "d3";

import ChartPanel from "./charts/ChartPanel";
import {
  buildCriHistoryXAxisTickValues,
  shouldRotateCriHistoryXAxisLabels,
} from "./CriHistoryChart";
import { chartSeriesColor } from "@/lib/chartSystem";
import type { RvRatioEntry, RvRatioStats } from "@/lib/rvRatio";

/**
 * Single-axis RV-ratio time series inside the sanctioned d3-svg chart shell
 * (CriHistoryChart sibling: same MARGIN idiom, curveMonotoneX, tooltip
 * side-flip, touch overlay, `.chart-empty-state`). Renders the FULL-HISTORY
 * ±1σ band as a fixed wash with dashed avg midline and dotted ±2σ guides, a
 * hairline at 1.00x parity, and regime-tinted line segments via clipped
 * paths — because the band is full-history-fixed, the line exiting it IS the
 * divergence signal. All colors route through chartSeriesColor()/tokens.
 */

interface RvRatioChartProps {
  /** Visible slice (preset/brush windowed) of the zipped payload entries. */
  entries: RvRatioEntry[];
  /** Full-history stats from the payload — the band never re-derives from the slice. */
  stats: RvRatioStats;
  symbol: string;
  benchmark: string;
}

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
  d: RvRatioEntry | null;
}

function formatRatio(value: number): string {
  return `${value.toFixed(2)}x`;
}

function formatRv(value: number): string {
  return `${value.toFixed(1)}%`;
}

export default function RvRatioChart({ entries, stats, symbol, benchmark }: RvRatioChartProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const clipIdBase = useId().replace(/[^a-zA-Z0-9_-]/g, "");
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

    // Y domain: visible ratio extent joined with the full-history band so the
    // band always renders even when the slice sits entirely inside it.
    const ratioExtent = d3.extent(entries, (d) => d.ratio) as [number, number];
    const lo = Math.min(ratioExtent[0], stats.band_lower);
    const hi = Math.max(ratioExtent[1], stats.band_upper);
    const pad = (hi - lo) * 0.12 || 0.05;
    const yScale = d3.scaleLinear().domain([lo - pad, hi + pad]).range([innerH, 0]);
    const [domainLo, domainHi] = yScale.domain();

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

    // Full-history ±1σ band wash + dashed avg midline.
    g.append("rect")
      .attr("data-testid", "rv-ratio-band")
      .attr("x", 0)
      .attr("width", innerW)
      .attr("y", yScale(stats.band_upper))
      .attr("height", Math.max(yScale(stats.band_lower) - yScale(stats.band_upper), 0))
      .attr("fill", BAND_WASH);

    g.append("line")
      .attr("data-testid", "rv-ratio-band-avg")
      .attr("x1", 0)
      .attr("x2", innerW)
      .attr("y1", yScale(stats.avg))
      .attr("y2", yScale(stats.avg))
      .attr("stroke", CHART_AXIS_MUTED)
      .attr("stroke-width", 1)
      .attr("stroke-dasharray", "6,4");

    // Dotted ±2σ guides, drawn only when inside the y-domain.
    const upper2 = stats.avg + 2 * stats.stddev;
    const lower2 = stats.avg - 2 * stats.stddev;
    for (const [value, testId] of [
      [upper2, "rv-ratio-guide-upper2"],
      [lower2, "rv-ratio-guide-lower2"],
    ] as const) {
      if (value < domainLo || value > domainHi) continue;
      g.append("line")
        .attr("data-testid", testId)
        .attr("x1", 0)
        .attr("x2", innerW)
        .attr("y1", yScale(value))
        .attr("y2", yScale(value))
        .attr("stroke", CHART_AXIS_MUTED)
        .attr("stroke-width", 1)
        .attr("stroke-dasharray", "2,3");
    }

    // Parity hairline at 1.00x.
    if (domainLo <= 1 && 1 <= domainHi) {
      g.append("line")
        .attr("data-testid", "rv-ratio-parity")
        .attr("x1", 0)
        .attr("x2", innerW)
        .attr("y1", yScale(1))
        .attr("y2", yScale(1))
        .attr("stroke", CHART_AXIS)
        .attr("stroke-width", 1);
    }

    const line = d3
      .line<RvRatioEntry>()
      .x((d) => xScale(new Date(d.date)))
      .y((d) => yScale(d.ratio))
      .curve(d3.curveMonotoneX);
    const linePath = line(entries) ?? "";

    g.append("path")
      .attr("data-testid", "rv-ratio-line")
      .attr("fill", "none")
      .attr("stroke", chartSeriesColor("primary"))
      .attr("stroke-width", 2)
      .attr("d", linePath);

    // Regime tinting: re-stroke the same path clipped to each sigma region.
    const defs = g.append("defs");
    const regions: Array<{ id: string; role: "caution" | "dislocation" | "neutral"; yTop: number; yBottom: number }> = [
      { id: "caution", role: "caution", yTop: Math.max(yScale(upper2), 0), yBottom: yScale(stats.band_upper) },
      { id: "dislocation", role: "dislocation", yTop: 0, yBottom: Math.max(yScale(upper2), 0) },
      { id: "neutral", role: "neutral", yTop: yScale(stats.band_lower), yBottom: innerH },
    ];
    for (const region of regions) {
      const regionHeight = region.yBottom - region.yTop;
      if (regionHeight <= 0) continue;
      const clipId = `${clipIdBase}-${region.id}`;
      defs
        .append("clipPath")
        .attr("id", clipId)
        .append("rect")
        .attr("x", 0)
        .attr("width", innerW)
        .attr("y", region.yTop)
        .attr("height", regionHeight);
      g.append("path")
        .attr("data-testid", `rv-ratio-line-${region.id}`)
        .attr("fill", "none")
        .attr("stroke", chartSeriesColor(region.role))
        .attr("stroke-width", 2)
        .attr("clip-path", `url(#${clipId})`)
        .attr("d", linePath);
    }

    // Ringed last point.
    const last = entries[entries.length - 1];
    const lastPoint = g
      .append("g")
      .attr("data-testid", "rv-ratio-last-point");
    lastPoint
      .append("circle")
      .attr("cx", xScale(new Date(last.date)))
      .attr("cy", yScale(last.ratio))
      .attr("r", 4)
      .attr("fill", "none")
      .attr("stroke", chartSeriesColor("primary"))
      .attr("stroke-width", 1.5);
    lastPoint
      .append("circle")
      .attr("cx", xScale(new Date(last.date)))
      .attr("cy", yScale(last.ratio))
      .attr("r", 2)
      .attr("fill", chartSeriesColor("primary"));

    // Y-axis.
    g.append("g")
      .call(
        d3
          .axisLeft(yScale)
          .tickValues(yTicks)
          .tickFormat((d) => formatRatio(d as number)),
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

    g.append("text")
      .attr("data-testid", "rv-ratio-y-label")
      .attr("transform", "rotate(-90)")
      .attr("x", -innerH / 2)
      .attr("y", -MARGIN.left + 12)
      .attr("text-anchor", "middle")
      .attr("fill", CHART_AXIS_MUTED)
      .attr("font-size", "9px")
      .attr("letter-spacing", "0.1em")
      .attr("font-family", "IBM Plex Mono, monospace")
      .text(`VOL RATIO (x ${benchmark})`);

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
          .attr("font-size", "10px")
          .attr("font-family", "IBM Plex Mono, monospace")
          .attr("text-anchor", rotateXAxisLabels ? "end" : "middle")
          .attr("dx", rotateXAxisLabels ? "-0.4em" : "0")
          .attr("dy", rotateXAxisLabels ? "0.6em" : "0.9em")
          .attr("transform", rotateXAxisLabels ? "rotate(-24)" : null);
      });

    // Tooltip overlay — mouse hover + touch drag, bisection over the slice.
    const updateTooltip = (clientX: number, clientY: number, mx: number) => {
      const hoveredDate = xScale.invert(mx);
      const bisect = d3.bisector((d: RvRatioEntry) => new Date(d.date)).left;
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
      .attr("data-testid", "rv-ratio-overlay")
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
  }, [entries, stats, width, height, benchmark, clipIdBase]);

  const showEmpty = entries.length < 2;
  const tooltipSideStyle =
    tooltip.x > width / 2
      ? { right: width - tooltip.x + 12 }
      : { left: tooltip.x + 12 };

  return (
    <ChartPanel
      family="analytical-time-series"
      title={`${symbol} RV / ${benchmark} RV`}
      legend={[{ label: "RV RATIO", color: chartSeriesColor("primary") }]}
      className="chart-panel-inline"
      dataTestId="rv-ratio-chart"
    >
      <div ref={containerRef} className="cri-history-chart-shell">
        <div className="chart-surface cri-history-chart-surface">
          {showEmpty ? (
            <div className="chart-empty-state">NO RATIO HISTORY</div>
          ) : (
            <svg ref={svgRef} className="cri-history-chart-svg" data-testid="rv-ratio-chart-svg" />
          )}
        </div>

        {tooltip.visible && tooltip.d && (
          <div className="chart-tooltip" style={{ ...tooltipSideStyle, top: tooltip.y - 10 }}>
            <div className="chart-tooltip-date">{tooltip.d.date}</div>
            <div className="chart-tooltip-row">
              <span className="chart-tooltip-label">RATIO</span>
              <span className="chart-tooltip-value" style={{ color: chartSeriesColor("primary") }}>
                {formatRatio(tooltip.d.ratio)}
              </span>
            </div>
            <div className="chart-tooltip-row">
              <span className="chart-tooltip-label">{symbol} RV</span>
              <span className="chart-tooltip-value">{formatRv(tooltip.d.assetRv)}</span>
            </div>
            <div className="chart-tooltip-row">
              <span className="chart-tooltip-label">{benchmark} RV</span>
              <span className="chart-tooltip-value">{formatRv(tooltip.d.benchmarkRv)}</span>
            </div>
          </div>
        )}
      </div>
    </ChartPanel>
  );
}
