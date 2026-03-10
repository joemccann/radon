"use client";

import { useRef, useEffect, useState } from "react";
import * as d3 from "d3";

export interface CriHistoryEntry {
  date: string;
  vix: number;
  vvix: number;
  spy: number;
  spx_vs_ma_pct: number;
  vix_5d_roc: number;
}

interface TooltipState {
  visible: boolean;
  x: number;
  y: number;
  d: CriHistoryEntry | null;
}

interface CriHistoryChartProps {
  history: CriHistoryEntry[];
  criScore?: number;
}

const MARGIN = { top: 16, right: 60, bottom: 32, left: 48 };
const HEIGHT = 220;

function vixColor(vix: number): string {
  if (vix < 20) return "#22c55e";
  if (vix <= 30) return "#f59e0b";
  return "#ef4444";
}

export default function CriHistoryChart({ history, criScore }: CriHistoryChartProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [tooltip, setTooltip] = useState<TooltipState>({ visible: false, x: 0, y: 0, d: null });
  const [width, setWidth] = useState(600);

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

  useEffect(() => {
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    if (!history || history.length < 2) return;

    const innerW = width - MARGIN.left - MARGIN.right;
    const innerH = HEIGHT - MARGIN.top - MARGIN.bottom;

    const g = svg
      .attr("width", width)
      .attr("height", HEIGHT)
      .append("g")
      .attr("transform", `translate(${MARGIN.left},${MARGIN.top})`);

    // Parse dates
    const dates = history.map((d) => new Date(d.date));

    // Scales
    const xScale = d3
      .scaleTime()
      .domain(d3.extent(dates) as [Date, Date])
      .range([0, innerW]);

    const vixExtent = d3.extent(history, (d) => d.vix) as [number, number];
    const vixPad = (vixExtent[1] - vixExtent[0]) * 0.15 || 2;
    const yVix = d3
      .scaleLinear()
      .domain([vixExtent[0] - vixPad, vixExtent[1] + vixPad])
      .range([innerH, 0]);

    const spyExtent = d3.extent(history, (d) => d.spy) as [number, number];
    const spyPad = (spyExtent[1] - spyExtent[0]) * 0.1 || 5;
    const ySpy = d3
      .scaleLinear()
      .domain([spyExtent[0] - spyPad, spyExtent[1] + spyPad])
      .range([innerH, 0]);

    // CRI score bar scale (0-100)
    const yCri = d3.scaleLinear().domain([0, 100]).range([innerH, 0]);

    // Grid lines
    const gridLines = yVix.ticks(4);
    g.append("g")
      .attr("class", "grid")
      .selectAll("line")
      .data(gridLines)
      .enter()
      .append("line")
      .attr("x1", 0)
      .attr("x2", innerW)
      .attr("y1", (d) => yVix(d))
      .attr("y2", (d) => yVix(d))
      .attr("stroke", "#1C1C1C")
      .attr("stroke-width", 1);

    // CRI score bars (if criScore provided, render as a horizontal band)
    // Render CRI history as subtle gray bars if available via spx_vs_ma_pct as proxy
    // Actually draw CRI score as a constant horizontal reference line if provided
    if (criScore !== undefined) {
      const criY = yCri(Math.min(100, Math.max(0, criScore)));
      g.append("line")
        .attr("x1", 0)
        .attr("x2", innerW)
        .attr("y1", criY)
        .attr("y2", criY)
        .attr("stroke", "#444444")
        .attr("stroke-width", 1)
        .attr("stroke-dasharray", "3,3");

      g.append("text")
        .attr("x", innerW + 4)
        .attr("y", criY + 3)
        .attr("fill", "#666666")
        .attr("font-size", "9px")
        .attr("font-family", "JetBrains Mono, monospace")
        .text(`CRI ${criScore.toFixed(0)}`);
    }

    // SPY line (right y-axis, white/muted)
    const spyLine = d3
      .line<CriHistoryEntry>()
      .x((d) => xScale(new Date(d.date)))
      .y((d) => ySpy(d.spy))
      .curve(d3.curveMonotoneX);

    g.append("path")
      .datum(history)
      .attr("fill", "none")
      .attr("stroke", "#555555")
      .attr("stroke-width", 1.5)
      .attr("d", spyLine);

    // VIX line with color segments
    // Draw VIX as individual colored segments between points
    for (let i = 0; i < history.length - 1; i++) {
      const a = history[i];
      const b = history[i + 1];
      const color = vixColor((a.vix + b.vix) / 2);
      g.append("line")
        .attr("x1", xScale(new Date(a.date)))
        .attr("y1", yVix(a.vix))
        .attr("x2", xScale(new Date(b.date)))
        .attr("y2", yVix(b.vix))
        .attr("stroke", color)
        .attr("stroke-width", 2);
    }

    // VIX dots
    g.selectAll(".vix-dot")
      .data(history)
      .enter()
      .append("circle")
      .attr("class", "vix-dot")
      .attr("cx", (d) => xScale(new Date(d.date)))
      .attr("cy", (d) => yVix(d.vix))
      .attr("r", 2.5)
      .attr("fill", (d) => vixColor(d.vix))
      .attr("stroke", "#050505")
      .attr("stroke-width", 1);

    // Left y-axis (VIX)
    const yAxisLeft = d3
      .axisLeft(yVix)
      .ticks(4)
      .tickFormat((d) => String(d));

    g.append("g")
      .call(yAxisLeft)
      .call((axis) => {
        axis.select(".domain").remove();
        axis.selectAll(".tick line").attr("stroke", "#1C1C1C");
        axis
          .selectAll(".tick text")
          .attr("fill", "#666666")
          .attr("font-size", "10px")
          .attr("font-family", "JetBrains Mono, monospace");
      });

    // Right y-axis (SPY)
    const yAxisRight = d3
      .axisRight(ySpy)
      .ticks(4)
      .tickFormat((d) => `$${d}`);

    g.append("g")
      .attr("transform", `translate(${innerW},0)`)
      .call(yAxisRight)
      .call((axis) => {
        axis.select(".domain").remove();
        axis.selectAll(".tick line").attr("stroke", "#1C1C1C");
        axis
          .selectAll(".tick text")
          .attr("fill", "#555555")
          .attr("font-size", "10px")
          .attr("font-family", "JetBrains Mono, monospace");
      });

    // X-axis
    const tickCount = Math.max(2, Math.min(history.length, Math.floor(innerW / 60)));
    const xAxis = d3
      .axisBottom(xScale)
      .ticks(tickCount)
      .tickFormat((d) => {
        const dt = d as Date;
        return d3.timeFormat("%b %-d")(dt);
      });

    g.append("g")
      .attr("transform", `translate(0,${innerH})`)
      .call(xAxis)
      .call((axis) => {
        axis.select(".domain").attr("stroke", "#1C1C1C");
        axis.selectAll(".tick line").attr("stroke", "#1C1C1C");
        axis
          .selectAll(".tick text")
          .attr("fill", "#666666")
          .attr("font-size", "10px")
          .attr("font-family", "JetBrains Mono, monospace");
      });

    // Legend
    const legendY = -4;
    const legendItems: { label: string; color: string }[] = [
      { label: "VIX", color: "#22c55e" },
      { label: "SPY", color: "#555555" },
    ];
    legendItems.forEach((item, i) => {
      const lx = i * 56;
      g.append("line")
        .attr("x1", lx)
        .attr("x2", lx + 12)
        .attr("y1", legendY)
        .attr("y2", legendY)
        .attr("stroke", item.color)
        .attr("stroke-width", 2);
      g.append("text")
        .attr("x", lx + 15)
        .attr("y", legendY + 4)
        .attr("fill", "#666666")
        .attr("font-size", "9px")
        .attr("font-family", "JetBrains Mono, monospace")
        .text(item.label);
    });

    // Invisible overlay for tooltip
    g.append("rect")
      .attr("width", innerW)
      .attr("height", innerH)
      .attr("fill", "transparent")
      .on("mousemove", function (event: MouseEvent) {
        const [mx] = d3.pointer(event, this);
        const hoveredDate = xScale.invert(mx);
        const bisect = d3.bisector((d: CriHistoryEntry) => new Date(d.date)).left;
        let idx = bisect(history, hoveredDate);
        idx = Math.max(0, Math.min(history.length - 1, idx));
        // Snap to nearest
        if (idx > 0) {
          const before = history[idx - 1];
          const after = history[idx];
          const tBefore = Math.abs(new Date(before.date).getTime() - hoveredDate.getTime());
          const tAfter = Math.abs(new Date(after.date).getTime() - hoveredDate.getTime());
          if (tBefore < tAfter) idx = idx - 1;
        }
        const entry = history[idx];
        const svgRect = svgRef.current?.getBoundingClientRect();
        const ex = event.clientX - (svgRect?.left ?? 0);
        const ey = event.clientY - (svgRect?.top ?? 0);
        setTooltip({ visible: true, x: ex, y: ey, d: entry });
      })
      .on("mouseleave", function () {
        setTooltip({ visible: false, x: 0, y: 0, d: null });
      });
  }, [history, width, criScore]);

  const showEmpty = !history || history.length < 2;

  return (
    <div
      ref={containerRef}
      data-testid="cri-history-chart"
      style={{ position: "relative", width: "100%", height: HEIGHT }}
    >
      {showEmpty ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            height: HEIGHT,
            color: "#666666",
            fontFamily: "JetBrains Mono, monospace",
            fontSize: 11,
            letterSpacing: "0.05em",
          }}
        >
          NO HISTORY AVAILABLE
        </div>
      ) : (
        <svg ref={svgRef} style={{ display: "block", width: "100%", height: HEIGHT }} />
      )}

      {tooltip.visible && tooltip.d && (
        <div
          style={{
            position: "absolute",
            // Flip to left of cursor when past the midpoint to avoid right-edge clipping
            ...(tooltip.x > width / 2
              ? { right: width - tooltip.x + 12 }
              : { left: tooltip.x + 12 }),
            top: tooltip.y - 10,
            background: "var(--bg-panel, #111111)",
            border: "1px solid var(--border-dim, #1C1C1C)",
            padding: "8px 10px",
            pointerEvents: "none",
            zIndex: 10,
            minWidth: 160,
          }}
        >
          <div
            style={{
              fontFamily: "JetBrains Mono, monospace",
              fontSize: 10,
              color: "#666666",
              marginBottom: 4,
              letterSpacing: "0.05em",
            }}
          >
            {tooltip.d.date}
          </div>
          {(
            [
              { label: "VIX", value: tooltip.d.vix.toFixed(2), color: vixColor(tooltip.d.vix) },
              { label: "VVIX", value: tooltip.d.vvix.toFixed(2), color: "#aaaaaa" },
              { label: "SPY", value: `$${tooltip.d.spy.toFixed(2)}`, color: "#888888" },
              {
                label: "SPX/MA%",
                value: `${tooltip.d.spx_vs_ma_pct >= 0 ? "+" : ""}${tooltip.d.spx_vs_ma_pct.toFixed(2)}%`,
                color: tooltip.d.spx_vs_ma_pct >= 0 ? "#22c55e" : "#ef4444",
              },
              {
                label: "VIX 5D ROC",
                value: `${tooltip.d.vix_5d_roc >= 0 ? "+" : ""}${tooltip.d.vix_5d_roc.toFixed(2)}%`,
                color: tooltip.d.vix_5d_roc >= 0 ? "#ef4444" : "#22c55e",
              },
            ] as { label: string; value: string; color: string }[]
          ).map((row) => (
            <div
              key={row.label}
              style={{
                display: "flex",
                justifyContent: "space-between",
                gap: 12,
                fontFamily: "JetBrains Mono, monospace",
                fontSize: 10,
                color: "#888888",
                lineHeight: "1.6",
              }}
            >
              <span style={{ color: "#555555" }}>{row.label}</span>
              <span style={{ color: row.color }}>{row.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
