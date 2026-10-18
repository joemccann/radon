"use client";

import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import * as d3 from "d3";
import ChartPanel from "./charts/ChartPanel";
import { chartSeriesColor } from "@/lib/chartSystem";
import { buildChartXAxisTickIndices, chartXAxisTickAnchor } from "@/lib/chartXAxis";
import { formatVolPoints, spreadWindowStats, type SpreadWindow, type VixModelSpreadEntry } from "@/lib/vixModelSpread";

const HEIGHT = 360;
const MARGIN = { top: 16, left: 46, right: 16, bottom: 30 };
const INNER_HEIGHT = HEIGHT - MARGIN.top - MARGIN.bottom;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const time = (date: string) => new Date(`${date}T00:00:00Z`).getTime();
const day = (date: string) => new Date(`${date}T00:00:00Z`).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });

export default function VixModelSpreadChart({ history, window }: { history: VixModelSpreadEntry[]; window: SpreadWindow }) {
  const container = useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = useState(720);
  const [hover, setHover] = useState<{ date: string; x: number } | null>(null);
  useEffect(() => {
    const el = container.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      if (entries[0]?.contentRect.width > 0) setMeasured(entries[0].contentRect.width);
    });
    observer.observe(el);
    const initial = el.getBoundingClientRect().width;
    if (initial > 0) setMeasured(initial);
    return () => observer.disconnect();
  }, []);
  const width = Math.max(240, measured);
  const innerWidth = width - MARGIN.left - MARGIN.right;
  const frame = useMemo(() => {
    const rows = history.filter((row) => finite(time(row.date)) && finite(row.spread) && finite(row.model_vix) && finite(row.vix));
    if (!rows.length) return null;
    const times = rows.map((row) => time(row.date));
    const halfDay = 43_200_000;
    const x = d3.scaleTime().domain([new Date(times[0] - halfDay), new Date(times.at(-1)! + halfDay)]).range([0, innerWidth]);
    const values = rows.flatMap((row) => {
      const stats = spreadWindowStats(row, window);
      return [row.spread, stats.mean, stats.upper, stats.lower].filter(finite);
    });
    const min = Math.min(0, ...values);
    const max = Math.max(0, ...values);
    const pad = (max - min) * 0.08 || 1;
    const y = d3.scaleLinear().domain([min - pad, max + pad]).nice().range([INNER_HEIGHT, 0]);
    const paths = (["mean", "upper", "lower"] as const).map((key) => ({
      key,
      d: d3.line<VixModelSpreadEntry>()
        .defined((row) => finite(spreadWindowStats(row, window)[key]))
        .x((row) => x(new Date(time(row.date))))
        .y((row) => y(spreadWindowStats(row, window)[key]!))(rows),
    }));
    // A bar occupies one source day; missing sessions remain unpainted.
    const barWidth = Math.max(0.4, Math.min(12, x(new Date(times[0] + 86_400_000)) - x(new Date(times[0]))));
    return { rows, times, x, y, paths, barWidth };
  }, [history, innerWidth, window]);
  const hovered = frame?.rows.find((row) => row.date === hover?.date);
  const meanColor = chartSeriesColor("comparison");
  const bandColor = chartSeriesColor("caution");
  function move(event: MouseEvent<SVGRectElement>) {
    if (!frame) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    if (bounds.width <= 0) return;
    const offset = (event.clientX - bounds.left) / bounds.width * innerWidth;
    const at = frame.x.invert(offset).getTime();
    let i = Math.min(frame.rows.length - 1, d3.bisectLeft(frame.times, at));
    if (i > 0 && Math.abs(frame.times[i - 1] - at) < Math.abs(frame.times[i] - at)) i -= 1;
    setHover({ date: frame.rows[i].date, x: frame.x(new Date(frame.times[i])) });
  }
  const stats = hovered ? spreadWindowStats(hovered, window) : null;
  return <ChartPanel family="analytical-time-series" title="VIX MODEL SPREAD" dataTestId="vix-model-spread-chart"
    className="chart-panel-inline" legend={[
      { label: "Positive spread", color: "var(--negative)" },
      { label: "Negative spread", color: "var(--positive)" },
      { label: `${window}-session mean`, color: meanColor },
      { label: "±2 standard deviations", color: bandColor },
    ]}>
    <div ref={container} style={{ position: "relative", minWidth: 0 }}>
      {frame ? <svg width={width} height={HEIGHT} style={{ display: "block", maxWidth: "100%" }} role="img" aria-label={`VIX minus model VIX with ${window}-session mean and two standard deviation bands`}>
        <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
          {frame.y.ticks(5).map((tick) => <g key={tick}>
            <line x1={0} x2={innerWidth} y1={frame.y(tick)} y2={frame.y(tick)} stroke="var(--chart-grid, var(--border-dim))" />
            <text x={-8} y={frame.y(tick)} textAnchor="end" dominantBaseline="middle" fill="var(--text-muted)" fontSize="12" fontFamily="var(--font-mono)">{tick.toFixed(1)}</text>
          </g>)}
          <line data-testid="vix-model-spread-zero" x1={0} x2={innerWidth} y1={frame.y(0)} y2={frame.y(0)} stroke="var(--text-muted)" />
          {frame.rows.map((row) => <rect key={row.date} data-testid="vix-model-spread-bar" data-date={row.date} data-spread={row.spread}
            x={frame.x(new Date(time(row.date))) - frame.barWidth / 2} y={Math.min(frame.y(0), frame.y(row.spread))}
            width={frame.barWidth} height={Math.abs(frame.y(row.spread) - frame.y(0))}
            fill={row.spread >= 0 ? "var(--negative)" : "var(--positive)"} />)}
          {frame.paths.map(({ key, d }) => d ? <path key={key} data-testid={`vix-model-spread-${key}-path`} d={d} fill="none"
            stroke={key === "mean" ? meanColor : bandColor} strokeWidth={key === "mean" ? 1.7 : 1.2} strokeDasharray={key === "mean" ? undefined : "4 3"} /> : null)}
          {buildChartXAxisTickIndices(frame.rows.length, innerWidth).map((index, tickIndex, indices) => <text key={frame.rows[index].date}
            x={frame.x(new Date(frame.times[index]))} y={INNER_HEIGHT + 18} textAnchor={chartXAxisTickAnchor(tickIndex, indices.length)}
            fill="var(--text-muted)" fontSize="12" fontFamily="var(--font-mono)">{new Date(frame.times[index]).toLocaleDateString("en-GB", { month: "short", year: "2-digit", timeZone: "UTC" })}</text>)}
          {hovered && hover ? <line x1={hover.x} x2={hover.x} y1={0} y2={INNER_HEIGHT} stroke="var(--text-muted)" strokeDasharray="3 3" /> : null}
          <rect data-testid="vix-model-spread-hover" width={innerWidth} height={INNER_HEIGHT} fill="transparent" onMouseMove={move} onMouseLeave={() => setHover(null)} />
        </g>
      </svg> : <div className="chart-empty-state" data-testid="vix-model-spread-chart-empty">No finite aligned observations</div>}
      {hovered && hover && stats ? <div className="chart-tooltip" data-testid="vix-model-spread-tooltip" style={{ position: "absolute", top: 20, pointerEvents: "none", ...(hover.x > innerWidth / 2 ? { right: 20 } : { left: MARGIN.left + 12 }) }}>
        <div className="chart-tooltip-date">{day(hovered.date)}</div>
        {([
          ["Spread", hovered.spread], ["Model VIX", hovered.model_vix], ["VIX", hovered.vix],
          [`Mean (${window} sessions)`, stats.mean], ["Upper +2 SD", stats.upper], ["Lower −2 SD", stats.lower],
        ] as const).map(([label, value]) => <div className="chart-tooltip-row" key={label}><span className="chart-tooltip-label">{label}</span><span className="chart-tooltip-value">{formatVolPoints(value)}</span></div>)}
      </div> : null}
    </div>
  </ChartPanel>;
}
