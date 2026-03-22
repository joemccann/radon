"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import * as d3 from "d3";
import ChartPanel from "./charts/ChartPanel";

type SkewHistoryPoint = {
  date: string;
  value: number;
};

type InternalsSkewChartProps = {
  history: SkewHistoryPoint[];
  title: string;
  seriesLabel: string;
  dataTestId?: string;
  lineColor?: string;
  decimals?: number;
};

/* ------------------------------------------------------------------ */
/*  Layout constants                                                   */
/* ------------------------------------------------------------------ */
const FOCUS_HEIGHT = 340;
const CONTEXT_HEIGHT = 52;
const GAP = 16;
const TOTAL_HEIGHT = FOCUS_HEIGHT + GAP + CONTEXT_HEIGHT;

const FOCUS_MARGIN = { top: 16, right: 48, bottom: 28, left: 52 };
const CTX_MARGIN = { top: 4, right: 48, bottom: 20, left: 52 };

/* ------------------------------------------------------------------ */
/*  CSS variable references                                            */
/* ------------------------------------------------------------------ */
const CHART_GRID = "var(--chart-grid, var(--border-dim))";
const CHART_AXIS = "var(--chart-axis, var(--border-dim))";
const CHART_AXIS_MUTED = "var(--chart-axis-muted, var(--text-secondary))";
const MONO = "IBM Plex Mono, monospace";

function fmtSigned(v: number, decimals = 3): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(decimals)}`;
}

function formatTooltipDate(dateString: string): string {
  const d = new Date(dateString);
  if (Number.isNaN(d.getTime())) return dateString;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export default function InternalsSkewChart({
  history,
  title,
  seriesLabel,
  dataTestId,
  lineColor = "var(--signal-core)",
  decimals = 3,
}: InternalsSkewChartProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [tooltip, setTooltip] = useState<{
    visible: boolean;
    date: string;
    value: number;
    cx: number;
    cy: number;
  }>({ visible: false, date: "", value: 0, cx: 0, cy: 0 });

  // Persist D3 objects across renders for brush interaction
  const scalesRef = useRef<{
    xFull: d3.ScaleTime<number, number>;
    xFocus: d3.ScaleTime<number, number>;
    yFocus: d3.ScaleLinear<number, number>;
    data: SkewHistoryPoint[];
  } | null>(null);

  /* ---------------------------------------------------------------- */
  /*  Responsive width                                                 */
  /* ---------------------------------------------------------------- */
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

  /* ---------------------------------------------------------------- */
  /*  Update focus chart when brush selection changes                   */
  /* ---------------------------------------------------------------- */
  const updateFocus = useCallback(
    (domain: [Date, Date]) => {
      const svg = d3.select(svgRef.current);
      const scales = scalesRef.current;
      if (!scales) return;

      const { data, yFocus, xFocus } = scales;
      const innerW = width - FOCUS_MARGIN.left - FOCUS_MARGIN.right;
      const innerH = FOCUS_HEIGHT - FOCUS_MARGIN.top - FOCUS_MARGIN.bottom;

      xFocus.domain(domain);

      // Filter visible data for Y-domain recalculation
      const visible = data.filter((d) => {
        const t = new Date(d.date).getTime();
        return t >= domain[0].getTime() && t <= domain[1].getTime();
      });
      if (visible.length > 0) {
        const [yMin, yMax] = d3.extent(visible, (d) => d.value) as [number, number];
        const pad = (yMax - yMin) * 0.15 || 0.01;
        yFocus.domain([yMin - pad, yMax + pad]).nice();
      }

      const focusG = svg.select<SVGGElement>(".focus-group");

      // Update line
      const line = d3
        .line<SkewHistoryPoint>()
        .x((d) => xFocus(new Date(d.date)))
        .y((d) => yFocus(d.value))
        .curve(d3.curveMonotoneX);
      focusG.select<SVGPathElement>(".focus-line").attr("d", line(data) ?? "");

      // Update area
      const area = d3
        .area<SkewHistoryPoint>()
        .x((d) => xFocus(new Date(d.date)))
        .y0(innerH)
        .y1((d) => yFocus(d.value))
        .curve(d3.curveMonotoneX);
      focusG.select<SVGPathElement>(".focus-area").attr("d", area(data) ?? "");

      // Update zero-line
      const zy = yFocus(0);
      focusG
        .select<SVGLineElement>(".zero-line")
        .attr("y1", zy)
        .attr("y2", zy)
        .attr("visibility", zy >= 0 && zy <= innerH ? "visible" : "hidden");

      // Update grid
      const yTicks = yFocus.ticks(6);
      const gridLines = focusG.select(".grid-lines").selectAll<SVGLineElement, number>("line").data(yTicks);
      gridLines.join("line")
        .attr("x1", 0).attr("x2", innerW)
        .attr("y1", (d) => yFocus(d)).attr("y2", (d) => yFocus(d))
        .attr("stroke", CHART_GRID).attr("stroke-width", 1);

      // Update latest dot
      const last = data[data.length - 1];
      if (last) {
        focusG
          .select<SVGCircleElement>(".latest-dot")
          .attr("cx", xFocus(new Date(last.date)))
          .attr("cy", yFocus(last.value));
      }

      // Update axes
      focusG
        .select<SVGGElement>(".y-axis")
        .call(
          d3.axisLeft(yFocus).ticks(6).tickFormat((v) => fmtSigned(v as number, decimals)),
        )
        .call((g) => {
          g.select(".domain").remove();
          g.selectAll(".tick line").attr("stroke", CHART_GRID);
          g.selectAll(".tick text")
            .attr("fill", "var(--text-muted)")
            .attr("font-size", "10px")
            .attr("font-family", MONO);
        });

      const tickCount = Math.max(3, Math.min(12, Math.floor(innerW / 80)));
      focusG
        .select<SVGGElement>(".x-axis")
        .call(
          d3.axisBottom(xFocus).ticks(tickCount).tickFormat((d) => d3.timeFormat("%b %d, '%y")(d as Date)),
        )
        .call((g) => {
          g.select(".domain").attr("stroke", CHART_AXIS);
          g.selectAll(".tick line").attr("stroke", CHART_GRID);
          g.selectAll(".tick text")
            .attr("fill", CHART_AXIS_MUTED)
            .attr("font-size", "10px")
            .attr("font-family", MONO);
        });
    },
    [width, decimals],
  );

  /* ---------------------------------------------------------------- */
  /*  Main D3 render                                                   */
  /* ---------------------------------------------------------------- */
  useEffect(() => {
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    setTooltip((prev) => ({ ...prev, visible: false }));

    if (!history || history.length < 2) return;

    const data = history.slice().sort((a, b) => a.date.localeCompare(b.date));
    const validData = data.filter((d) => Number.isFinite(d.value));
    if (validData.length < 2) return;

    const innerW = width - FOCUS_MARGIN.left - FOCUS_MARGIN.right;
    const innerH = FOCUS_HEIGHT - FOCUS_MARGIN.top - FOCUS_MARGIN.bottom;
    const ctxInnerW = width - CTX_MARGIN.left - CTX_MARGIN.right;
    const ctxInnerH = CONTEXT_HEIGHT - CTX_MARGIN.top - CTX_MARGIN.bottom;

    svg.attr("width", width).attr("height", TOTAL_HEIGHT);

    // Clip path for focus area
    svg
      .append("defs")
      .append("clipPath")
      .attr("id", `clip-${dataTestId ?? "skew"}`)
      .append("rect")
      .attr("width", innerW)
      .attr("height", innerH);

    /* ============================================================== */
    /*  SCALES                                                         */
    /* ============================================================== */
    const dates = validData.map((d) => new Date(d.date));
    const fullExtent = d3.extent(dates) as [Date, Date];

    const xFull = d3.scaleTime().domain(fullExtent).range([0, ctxInnerW]);

    const xFocus = d3.scaleTime().domain(fullExtent).range([0, innerW]);

    const [yMin, yMax] = d3.extent(validData, (d) => d.value) as [number, number];
    const yPad = (yMax - yMin) * 0.15 || 0.01;
    const yFocus = d3.scaleLinear().domain([yMin - yPad, yMax + yPad]).range([innerH, 0]).nice();

    const yCtx = d3.scaleLinear().domain(yFocus.domain()).range([ctxInnerH, 0]);

    // Stash for brush callback
    scalesRef.current = { xFull, xFocus, yFocus, data: validData };

    /* ============================================================== */
    /*  FOCUS CHART                                                    */
    /* ============================================================== */
    const focusG = svg
      .append("g")
      .attr("class", "focus-group")
      .attr("transform", `translate(${FOCUS_MARGIN.left},${FOCUS_MARGIN.top})`);

    // Grid lines
    focusG.append("g").attr("class", "grid-lines");

    // Zero-line
    const zy = yFocus(0);
    focusG
      .append("line")
      .attr("class", "zero-line")
      .attr("x1", 0)
      .attr("x2", innerW)
      .attr("y1", zy)
      .attr("y2", zy)
      .attr("stroke", "color-mix(in srgb, var(--text-muted) 55%, transparent)")
      .attr("stroke-dasharray", "4 4")
      .attr("visibility", zy >= 0 && zy <= innerH ? "visible" : "hidden");

    // Clipped content group
    const clip = focusG
      .append("g")
      .attr("clip-path", `url(#clip-${dataTestId ?? "skew"})`);

    // Area fill
    const area = d3
      .area<SkewHistoryPoint>()
      .x((d) => xFocus(new Date(d.date)))
      .y0(innerH)
      .y1((d) => yFocus(d.value))
      .curve(d3.curveMonotoneX);

    clip
      .append("path")
      .attr("class", "focus-area")
      .datum(validData)
      .attr("fill", `color-mix(in srgb, ${lineColor} 12%, transparent)`)
      .attr("d", area);

    // Line
    const line = d3
      .line<SkewHistoryPoint>()
      .x((d) => xFocus(new Date(d.date)))
      .y((d) => yFocus(d.value))
      .curve(d3.curveMonotoneX);

    clip
      .append("path")
      .attr("class", "focus-line")
      .datum(validData)
      .attr("fill", "none")
      .attr("stroke", lineColor)
      .attr("stroke-width", 1.5)
      .attr("d", line);

    // Latest data point dot
    const last = validData[validData.length - 1];
    clip
      .append("circle")
      .attr("class", "latest-dot")
      .attr("cx", xFocus(new Date(last.date)))
      .attr("cy", yFocus(last.value))
      .attr("r", 4)
      .attr("fill", lineColor)
      .attr("stroke", "var(--bg-panel)")
      .attr("stroke-width", 2);

    // Y-axis
    focusG.append("g").attr("class", "y-axis");

    // X-axis
    focusG
      .append("g")
      .attr("class", "x-axis")
      .attr("transform", `translate(0,${innerH})`);

    // Crosshair group (hidden by default)
    const crosshair = focusG.append("g").attr("class", "crosshair").style("display", "none");
    crosshair
      .append("line")
      .attr("class", "crosshair-v")
      .attr("y1", 0)
      .attr("y2", innerH)
      .attr("stroke", "var(--text-muted)")
      .attr("stroke-width", 1)
      .attr("stroke-dasharray", "3 3")
      .attr("opacity", 0.6);
    crosshair
      .append("line")
      .attr("class", "crosshair-h")
      .attr("x1", 0)
      .attr("x2", innerW)
      .attr("stroke", "var(--text-muted)")
      .attr("stroke-width", 1)
      .attr("stroke-dasharray", "3 3")
      .attr("opacity", 0.4);
    crosshair
      .append("circle")
      .attr("class", "crosshair-dot")
      .attr("r", 4)
      .attr("fill", lineColor)
      .attr("stroke", "var(--bg-panel)")
      .attr("stroke-width", 2);

    // Value badge on right axis
    crosshair
      .append("rect")
      .attr("class", "crosshair-badge-bg")
      .attr("x", innerW + 4)
      .attr("width", 44)
      .attr("height", 18)
      .attr("rx", 2)
      .attr("fill", "var(--bg-panel-raised)")
      .attr("stroke", CHART_GRID);
    crosshair
      .append("text")
      .attr("class", "crosshair-badge-text")
      .attr("x", innerW + 26)
      .attr("text-anchor", "middle")
      .attr("fill", lineColor)
      .attr("font-size", "9px")
      .attr("font-family", MONO)
      .attr("dominant-baseline", "central");

    // Initial axis render
    updateFocus(fullExtent);

    // Hover overlay
    const bisector = d3.bisector((d: SkewHistoryPoint) => new Date(d.date).getTime()).left;

    focusG
      .append("rect")
      .attr("class", "hover-overlay")
      .attr("width", innerW)
      .attr("height", innerH)
      .attr("fill", "transparent")
      .style("cursor", "crosshair")
      .on("mousemove", (event: MouseEvent) => {
        const [mx] = d3.pointer(event, focusG.node());
        const currentX = scalesRef.current?.xFocus;
        const currentY = scalesRef.current?.yFocus;
        const currentData = scalesRef.current?.data;
        if (!currentX || !currentY || !currentData) return;

        const date = currentX.invert(mx);
        let idx = bisector(currentData, date.getTime());
        idx = Math.max(0, Math.min(currentData.length - 1, idx));
        if (idx > 0) {
          const prev = currentData[idx - 1];
          const curr = currentData[idx];
          if (
            Math.abs(new Date(prev.date).getTime() - date.getTime()) <
            Math.abs(new Date(curr.date).getTime() - date.getTime())
          ) {
            idx -= 1;
          }
        }

        const point = currentData[idx];
        const cx = currentX(new Date(point.date));
        const cy = currentY(point.value);

        // Show crosshair
        crosshair.style("display", null);
        crosshair.select(".crosshair-v").attr("x1", cx).attr("x2", cx);
        crosshair.select(".crosshair-h").attr("y1", cy).attr("y2", cy);
        crosshair.select(".crosshair-dot").attr("cx", cx).attr("cy", cy);
        crosshair.select(".crosshair-badge-bg").attr("y", cy - 9);
        crosshair
          .select(".crosshair-badge-text")
          .attr("y", cy)
          .text(fmtSigned(point.value, decimals));

        setTooltip({ visible: true, date: point.date, value: point.value, cx, cy });
      })
      .on("mouseleave", () => {
        crosshair.style("display", "none");
        setTooltip((prev) => ({ ...prev, visible: false }));
      });

    /* ============================================================== */
    /*  CONTEXT (MINIMAP) CHART                                        */
    /* ============================================================== */
    const ctxTop = FOCUS_HEIGHT + GAP;
    const ctxG = svg
      .append("g")
      .attr("class", "context-group")
      .attr("transform", `translate(${CTX_MARGIN.left},${ctxTop + CTX_MARGIN.top})`);

    // Context area fill
    const ctxArea = d3
      .area<SkewHistoryPoint>()
      .x((d) => xFull(new Date(d.date)))
      .y0(ctxInnerH)
      .y1((d) => yCtx(d.value))
      .curve(d3.curveMonotoneX);

    ctxG
      .append("path")
      .datum(validData)
      .attr("fill", `color-mix(in srgb, ${lineColor} 18%, transparent)`)
      .attr("d", ctxArea);

    // Context line
    const ctxLine = d3
      .line<SkewHistoryPoint>()
      .x((d) => xFull(new Date(d.date)))
      .y((d) => yCtx(d.value))
      .curve(d3.curveMonotoneX);

    ctxG
      .append("path")
      .datum(validData)
      .attr("fill", "none")
      .attr("stroke", lineColor)
      .attr("stroke-width", 1)
      .attr("opacity", 0.7)
      .attr("d", ctxLine);

    // Context X-axis
    ctxG
      .append("g")
      .attr("transform", `translate(0,${ctxInnerH})`)
      .call(
        d3.axisBottom(xFull).ticks(6).tickFormat((d) => d3.timeFormat("%b '%y")(d as Date)),
      )
      .call((g) => {
        g.select(".domain").attr("stroke", CHART_AXIS);
        g.selectAll(".tick line").attr("stroke", CHART_GRID);
        g.selectAll(".tick text")
          .attr("fill", CHART_AXIS_MUTED)
          .attr("font-size", "9px")
          .attr("font-family", MONO);
      });

    // Brush
    const brush = d3
      .brushX()
      .extent([
        [0, 0],
        [ctxInnerW, ctxInnerH],
      ])
      .on("brush end", (event: d3.D3BrushEvent<unknown>) => {
        if (!event.selection) {
          updateFocus(fullExtent);
          return;
        }
        const [x0, x1] = event.selection as [number, number];
        updateFocus([xFull.invert(x0), xFull.invert(x1)]);
      });

    const brushG = ctxG.append("g").attr("class", "context-brush").call(brush);

    // Style brush handles
    brushG
      .selectAll(".selection")
      .attr("fill", `color-mix(in srgb, ${lineColor} 20%, transparent)`)
      .attr("stroke", lineColor)
      .attr("stroke-width", 1);

    brushG
      .selectAll(".handle")
      .attr("fill", lineColor)
      .attr("rx", 2);

    // Border around context area
    ctxG
      .append("rect")
      .attr("width", ctxInnerW)
      .attr("height", ctxInnerH)
      .attr("fill", "none")
      .attr("stroke", CHART_GRID)
      .attr("stroke-width", 1);

  }, [history, width, updateFocus, dataTestId, lineColor, decimals]);

  /* ---------------------------------------------------------------- */
  /*  Render                                                           */
  /* ---------------------------------------------------------------- */
  const latest = history && history.length > 0 ? history[history.length - 1] : null;

  return (
    <ChartPanel
      family="analytical-time-series"
      title={title}
      legend={[{ label: seriesLabel, color: lineColor }]}
      className="chart-panel-inline"
      bodyClassName="skew-chart-panel"
      contentClassName="skew-chart-content"
      dataTestId={dataTestId}
    >
      <div ref={containerRef} className="skew-chart-shell">
        <div className="chart-surface skew-chart-surface">
          {history.length < 2 ? (
            <div className="chart-empty-state skew-chart-empty">NO HISTORY AVAILABLE</div>
          ) : (
            <svg ref={svgRef} className="skew-chart-svg" />
          )}
        </div>

        {tooltip.visible ? (
          <div
            className="chart-tooltip skew-chart-tooltip"
            style={{
              ...(tooltip.cx > width / 2
                ? { right: width - tooltip.cx - FOCUS_MARGIN.left + 16 }
                : { left: tooltip.cx + FOCUS_MARGIN.left + 16 }),
              top: Math.max(FOCUS_MARGIN.top, tooltip.cy + FOCUS_MARGIN.top - 20),
            }}
          >
            <div className="chart-tooltip-date">{formatTooltipDate(tooltip.date)}</div>
            <div className="chart-tooltip-row">
              <span className="chart-tooltip-label">{seriesLabel}</span>
              <span className="chart-tooltip-value" style={{ color: lineColor }}>
                {fmtSigned(tooltip.value, decimals)}
              </span>
            </div>
          </div>
        ) : null}

        <div
          className="regime-strip-sub"
          style={{ display: "flex", justifyContent: "space-between", padding: "8px 10px" }}
        >
          <span>{latest ? `Latest: ${latest.date}` : "No history yet"}</span>
          <span>{latest ? `Latest skew: ${fmtSigned(latest.value, decimals)}` : ""}</span>
        </div>
      </div>
    </ChartPanel>
  );
}
