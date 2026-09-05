"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
import * as d3 from "d3";
import InfoTooltip from "./InfoTooltip";
import ChartLegend from "./charts/ChartLegend";
import ChartPanel from "./charts/ChartPanel";
import { SECTION_TOOLTIPS } from "@/lib/sectionTooltips";
import {
  buildRegimeRelationshipEntries,
  REGIME_QUADRANT_DETAILS,
  summarizeRegimeRelationship,
  type RegimeRelationshipEntry,
  type RegimeQuadrant,
  type RegimeRelationshipLiveValues,
  type RegimeRelationshipSource,
} from "@/lib/regimeRelationships";

type RegimeRelationshipViewProps = {
  history: RegimeRelationshipSource[];
  liveValues?: RegimeRelationshipLiveValues;
};

const CHART_WIDTH = 760;
const CHART_HEIGHT = 240;
const BRUSH_HEIGHT = 40;
const BRUSH_HANDLE_WIDTH = 8;
const MARGIN = { top: 16, right: 20, bottom: 32, left: 44 };
const DATE_FORMATTER = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });

type RangePresetSlug = "1m" | "3m" | "6m" | "1y" | "all";
type ActiveRange = RangePresetSlug | "custom";

const RANGE_PRESETS: ReadonlyArray<{ slug: RangePresetSlug; label: string; sessions: number }> = [
  { slug: "1m", label: "1M", sessions: 21 },
  { slug: "3m", label: "3M", sessions: 63 },
  { slug: "6m", label: "6M", sessions: 126 },
  { slug: "1y", label: "1Y", sessions: 252 },
  { slug: "all", label: "All", sessions: Number.POSITIVE_INFINITY },
];

const DEFAULT_PRESET: RangePresetSlug = "1y";

function presetSessions(slug: RangePresetSlug): number {
  const preset = RANGE_PRESETS.find((entry) => entry.slug === slug);
  return preset?.sessions ?? Number.POSITIVE_INFINITY;
}

function presetRange(slug: RangePresetSlug, total: number): [number, number] {
  if (total === 0) return [0, 0];
  const sessions = Math.min(presetSessions(slug), total);
  const start = Math.max(0, total - sessions);
  return [start, total - 1];
}

function fmtSigned(value: number, digits = 2): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function formatDateLabel(value: string): string {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return DATE_FORMATTER.format(date);
}

export function buildTickIndices(length: number, count = 4): number[] {
  if (length <= count) {
    return Array.from({ length }, (_, index) => index);
  }

  const step = (length - 1) / (count - 1);
  const indices = new Set<number>();
  for (let tick = 0; tick < count; tick += 1) {
    indices.add(Math.round(step * tick));
  }
  indices.add(0);
  indices.add(length - 1);
  return Array.from(indices).sort((a, b) => a - b);
}

export function resolveRelationshipTickCount(innerWidth: number): number {
  return Math.max(4, Math.min(7, Math.floor(innerWidth / 110)));
}

export function nearestRegimeScatterIndex(
  points: ReadonlyArray<{ x: number; y: number }>,
  pointerX: number,
  pointerY: number,
): number {
  if (points.length === 0) return 0;
  let best = 0;
  let bestDist = Number.POSITIVE_INFINITY;
  for (let index = 0; index < points.length; index += 1) {
    const dx = points[index].x - pointerX;
    const dy = points[index].y - pointerY;
    const dist = dx * dx + dy * dy;
    if (dist < bestDist) {
      bestDist = dist;
      best = index;
    }
  }
  return best;
}

function spreadStateColor(state: string): string {
  if (state === "Fear Premium") return "var(--positive)";
  if (state === "Realized Lead") return "var(--negative)";
  return "var(--text-secondary)";
}

function displaySpreadState(state: string): string {
  if (state === "Fear Premium") return "IMPLIED PREMIUM";
  if (state === "Realized Lead") return "REALIZED LEAD";
  return "BALANCED";
}

function quadrantTone(quadrant: RegimeQuadrant): string {
  switch (quadrant) {
    case "Systemic Panic":
      return "var(--negative)";
    case "Fragile Calm":
      return "var(--dislocation)";
    case "Stock Picker's Market":
      return "var(--warning)";
    case "Goldilocks":
      return "var(--positive)";
  }
}

function relationshipBiasLabel(spreadState: string, priorSpread: number | null, latestSpread: number): string {
  const displayState = displaySpreadState(spreadState);
  if (priorSpread == null) {
    return `${displayState} regime`;
  }
  const delta = latestSpread - priorSpread;
  const direction = delta >= 0 ? "widening" : "compressing";
  return `${displayState} | ${direction} ${fmtSigned(delta)} pts`;
}

function quadrantSlug(quadrant: RegimeQuadrant): string {
  return quadrant.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

const QUADRANT_DISPLAY_ORDER: RegimeQuadrant[] = [
  "Systemic Panic",
  "Fragile Calm",
  "Stock Picker's Market",
  "Goldilocks",
];

type ZScoreHoverState = {
  entry: RegimeRelationshipEntry;
  index: number;
  x: number;
  y: number;
  width: number;
  height: number;
};

type SpreadHoverState = ZScoreHoverState;
type QuadrantHoverState = ZScoreHoverState;

type BrushDragMode = "left" | "right" | "window";

type BrushDragState = {
  mode: BrushDragMode;
  pointerId: number;
  originX: number;
  originStart: number;
  originEnd: number;
};

export default function RegimeRelationshipView({
  history,
  liveValues,
}: RegimeRelationshipViewProps) {
  const zScoreSvgRef = useRef<SVGSVGElement>(null);
  const spreadSvgRef = useRef<SVGSVGElement>(null);
  const quadrantSvgRef = useRef<SVGSVGElement>(null);
  const brushRef = useRef<HTMLDivElement>(null);
  const dragStateRef = useRef<BrushDragState | null>(null);
  const [zScoreHover, setZScoreHover] = useState<ZScoreHoverState | null>(null);
  const [spreadHover, setSpreadHover] = useState<SpreadHoverState | null>(null);
  const [quadrantHover, setQuadrantHover] = useState<QuadrantHoverState | null>(null);
  const entries = useMemo(
    () => buildRegimeRelationshipEntries(history, liveValues),
    [history, liveValues],
  );
  const summary = useMemo(
    () => summarizeRegimeRelationship(entries),
    [entries],
  );

  // Initial range covers the last 252 sessions (or all when shorter).
  // We persist [startIdx, endIdx] inclusive into the full `entries` array.
  const [range, setRange] = useState<[number, number]>(() =>
    presetRange(DEFAULT_PRESET, Math.max(entries.length, 1)),
  );
  const [activeRange, setActiveRange] = useState<ActiveRange>(DEFAULT_PRESET);

  // Re-clamp the range when the upstream history shrinks/grows.
  useEffect(() => {
    if (entries.length === 0) return;
    if (activeRange !== "custom") {
      setRange(presetRange(activeRange, entries.length));
      return;
    }
    setRange(([start, end]) => {
      const maxIdx = entries.length - 1;
      const clampedEnd = Math.min(end, maxIdx);
      const clampedStart = Math.max(0, Math.min(start, clampedEnd));
      if (clampedStart === start && clampedEnd === end) return [start, end];
      return [clampedStart, clampedEnd];
    });
  }, [activeRange, entries.length]);

  // Global pointer move/up listeners while a brush drag is in flight.
  // Listeners read the latest `range` via a closure-captured ref so the math
  // is stable across re-renders.
  useEffect(() => {
    function indexFromClientX(clientX: number): number {
      const rect = brushRef.current?.getBoundingClientRect();
      if (!rect || rect.width <= 0) return 0;
      const ratio = (clientX - rect.left) / rect.width;
      const clamped = Math.max(0, Math.min(1, ratio));
      return Math.round(clamped * Math.max(entries.length - 1, 0));
    }

    function handleMove(event: PointerEvent) {
      const drag = dragStateRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;

      const total = entries.length;
      if (total === 0) return;
      const maxIdx = total - 1;
      const idx = indexFromClientX(event.clientX);

      let nextStart = drag.originStart;
      let nextEnd = drag.originEnd;
      if (drag.mode === "left") {
        nextStart = Math.max(0, Math.min(idx, drag.originEnd - 1));
      } else if (drag.mode === "right") {
        nextEnd = Math.max(drag.originStart + 1, Math.min(idx, maxIdx));
      } else {
        const originIdx = indexFromClientX(drag.originX);
        const deltaIdx = idx - originIdx;
        const windowSize = drag.originEnd - drag.originStart;
        nextStart = Math.max(0, Math.min(maxIdx - windowSize, drag.originStart + deltaIdx));
        nextEnd = nextStart + windowSize;
      }

      setRange((prev) =>
        prev[0] === nextStart && prev[1] === nextEnd ? prev : [nextStart, nextEnd],
      );
      setActiveRange("custom");
    }

    function handleEnd(event: PointerEvent) {
      const drag = dragStateRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      dragStateRef.current = null;
    }

    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleEnd);
    window.addEventListener("pointercancel", handleEnd);
    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleEnd);
      window.removeEventListener("pointercancel", handleEnd);
    };
  }, [entries.length]);

  if (!summary || entries.length < 2) {
    return null;
  }

  const innerWidth = CHART_WIDTH - MARGIN.left - MARGIN.right;
  const innerHeight = CHART_HEIGHT - MARGIN.top - MARGIN.bottom;

  // Slice the entries to the active range for the spread chart only.
  // Z-score and scatter charts continue to consume the full history.
  const visibleStart = Math.max(0, Math.min(range[0], entries.length - 1));
  const visibleEnd = Math.max(visibleStart, Math.min(range[1], entries.length - 1));
  const visibleEntries = entries.slice(visibleStart, visibleEnd + 1);
  const visibleCount = visibleEntries.length;

  const spreadXScale = d3
    .scaleLinear()
    .domain([0, Math.max(visibleCount - 1, 1)])
    .range([0, innerWidth]);
  const spreadTickIndices = buildTickIndices(
    visibleCount,
    resolveRelationshipTickCount(innerWidth),
  );

  const xScale = d3.scaleLinear().domain([0, entries.length - 1]).range([0, innerWidth]);
  const tickIndices = buildTickIndices(entries.length, resolveRelationshipTickCount(innerWidth));

  const spreadMax = Math.max(
    ...visibleEntries.map((entry) => Math.abs(entry.spread)),
    Math.abs(summary.meanSpread),
    1,
  );
  const spreadScale = d3
    .scaleLinear()
    .domain([-(spreadMax * 1.15), spreadMax * 1.15])
    .range([innerHeight, 0]);
  const spreadLine = d3
    .line<RegimeRelationshipEntry>()
    .x((_entry, index) => spreadXScale(index))
    .y((entry) => spreadScale(entry.spread))
    .curve(d3.curveMonotoneX)(visibleEntries);

  const realizedExtent = d3.extent(entries, (entry) => entry.realizedVol) as [number, number];
  const cor1mExtent = d3.extent(entries, (entry) => entry.cor1m) as [number, number];
  const realizedPad = (realizedExtent[1] - realizedExtent[0]) * 0.18 || 1;
  const cor1mPad = (cor1mExtent[1] - cor1mExtent[0]) * 0.18 || 1;
  const scatterXScale = d3
    .scaleLinear()
    .domain([realizedExtent[0] - realizedPad, realizedExtent[1] + realizedPad])
    .range([0, innerWidth]);
  const scatterYScale = d3
    .scaleLinear()
    .domain([cor1mExtent[0] - cor1mPad, cor1mExtent[1] + cor1mPad])
    .range([innerHeight, 0]);
  const realizedMean = d3.mean(entries, (entry) => entry.realizedVol) ?? 0;
  const cor1mMean = d3.mean(entries, (entry) => entry.cor1m) ?? 0;

  const zMax = Math.max(
    ...entries.map((entry) => Math.max(Math.abs(entry.realizedVolZ), Math.abs(entry.cor1mZ))),
    Math.abs(summary.latestDivergence),
    1,
  );
  const zScale = d3
    .scaleLinear()
    .domain([-(zMax * 1.15), zMax * 1.15])
    .range([innerHeight, 0]);
  const zRvolLine = d3
    .line<(typeof entries)[number]>()
    .x((entry, index) => xScale(index))
    .y((entry) => zScale(entry.realizedVolZ))
    .curve(d3.curveMonotoneX)(entries);
  const zCor1mLine = d3
    .line<(typeof entries)[number]>()
    .x((entry, index) => xScale(index))
    .y((entry) => zScale(entry.cor1mZ))
    .curve(d3.curveMonotoneX)(entries);

  const latest = entries[entries.length - 1];
  const latestVisible = visibleEntries[visibleCount - 1] ?? latest;
  const spreadColor = spreadStateColor(summary.spreadState);
  const quadrantColor = quadrantTone(summary.latestQuadrant);
  const latestQuadrantColor = quadrantTone(latest.quadrant);
  const zScoreTooltipSideStyle = zScoreHover
    ? zScoreHover.x > zScoreHover.width / 2
      ? { right: zScoreHover.width - zScoreHover.x + 12 }
      : { left: zScoreHover.x + 12 }
    : {};
  const zScoreTooltipTop = zScoreHover
    ? Math.max(12, Math.min(zScoreHover.y - 54, zScoreHover.height - 96))
    : 0;
  const spreadTooltipSideStyle = spreadHover
    ? spreadHover.x > spreadHover.width / 2
      ? { right: spreadHover.width - spreadHover.x + 12 }
      : { left: spreadHover.x + 12 }
    : {};
  const spreadTooltipTop = spreadHover
    ? Math.max(12, Math.min(spreadHover.y - 54, spreadHover.height - 96))
    : 0;
  const quadrantTooltipSideStyle = quadrantHover
    ? quadrantHover.x > quadrantHover.width / 2
      ? { right: quadrantHover.width - quadrantHover.x + 12 }
      : { left: quadrantHover.x + 12 }
    : {};
  const quadrantTooltipTop = quadrantHover
    ? Math.max(12, Math.min(quadrantHover.y - 54, quadrantHover.height - 96))
    : 0;

  // Brush window dimensions as percentages of the brush track.
  const totalSpan = Math.max(entries.length - 1, 1);
  const brushLeftPct = (visibleStart / totalSpan) * 100;
  const brushWidthPct = Math.max(
    ((visibleEnd - visibleStart) / totalSpan) * 100,
    0.5,
  );

  function updateZScoreHover(clientX: number, clientY: number) {
    const svgRect = zScoreSvgRef.current?.getBoundingClientRect();
    if (!svgRect) return;

    const pointerX = clientX - svgRect.left;
    const pointerY = clientY - svgRect.top;
    const chartX = (pointerX / svgRect.width) * CHART_WIDTH;
    const clampedInnerX = Math.max(0, Math.min(innerWidth, chartX - MARGIN.left));
    const index = Math.max(
      0,
      Math.min(entries.length - 1, Math.round((clampedInnerX / innerWidth) * (entries.length - 1))),
    );

    setZScoreHover({
      entry: entries[index],
      index,
      x: pointerX,
      y: pointerY,
      width: svgRect.width,
      height: svgRect.height,
    });
  }

  function handleZScoreHover(event: ReactMouseEvent<HTMLElement | SVGRectElement>) {
    updateZScoreHover(event.clientX, event.clientY);
  }

  function updateSpreadHover(clientX: number, clientY: number) {
    const svgRect = spreadSvgRef.current?.getBoundingClientRect();
    if (!svgRect || visibleCount === 0) return;

    const pointerX = clientX - svgRect.left;
    const pointerY = clientY - svgRect.top;
    const chartX = (pointerX / svgRect.width) * CHART_WIDTH;
    const clampedInnerX = Math.max(0, Math.min(innerWidth, chartX - MARGIN.left));
    const localIndex = Math.max(
      0,
      Math.min(
        visibleCount - 1,
        Math.round((clampedInnerX / innerWidth) * Math.max(visibleCount - 1, 1)),
      ),
    );

    setSpreadHover({
      entry: visibleEntries[localIndex],
      index: localIndex,
      x: pointerX,
      y: pointerY,
      width: svgRect.width,
      height: svgRect.height,
    });
  }

  function handleSpreadHover(event: ReactMouseEvent<HTMLElement | SVGRectElement>) {
    updateSpreadHover(event.clientX, event.clientY);
  }

  function updateQuadrantHover(clientX: number, clientY: number) {
    const svgRect = quadrantSvgRef.current?.getBoundingClientRect();
    if (!svgRect || entries.length === 0) return;

    const pointerX = clientX - svgRect.left;
    const pointerY = clientY - svgRect.top;
    const chartX = (pointerX / svgRect.width) * CHART_WIDTH;
    const chartY = (pointerY / svgRect.height) * CHART_HEIGHT;
    const innerX = chartX - MARGIN.left;
    const innerY = chartY - MARGIN.top;
    const points = entries.map((entry) => ({
      x: scatterXScale(entry.realizedVol),
      y: scatterYScale(entry.cor1m),
    }));
    const index = nearestRegimeScatterIndex(points, innerX, innerY);

    setQuadrantHover({
      entry: entries[index],
      index,
      x: pointerX,
      y: pointerY,
      width: svgRect.width,
      height: svgRect.height,
    });
  }

  function handleQuadrantHover(event: ReactMouseEvent<HTMLElement | SVGRectElement>) {
    updateQuadrantHover(event.clientX, event.clientY);
  }

  function applyPreset(slug: RangePresetSlug) {
    setRange(presetRange(slug, entries.length));
    setActiveRange(slug);
  }

  function handleBrushPointerDown(mode: BrushDragMode) {
    return (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      try {
        (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
      } catch {
        // jsdom may not implement setPointerCapture; ignore.
      }
      dragStateRef.current = {
        mode,
        pointerId: event.pointerId,
        originX: event.clientX,
        originStart: range[0],
        originEnd: range[1],
      };
    };
  }

  return (
    <ChartPanel
      family="analytical-time-series"
      title={
        <>
          <span>RVOL / COR1M RELATIONSHIP</span>
          <InfoTooltip text={SECTION_TOOLTIPS["RELATIONSHIP VIEW"]} />
        </>
      }
      badge={
        <div className="regime-relationship-meta">
          <span className="regime-relationship-chip" style={{ color: spreadColor }}>
            {displaySpreadState(summary.spreadState)}
          </span>
          <span className="regime-relationship-chip" style={{ color: quadrantColor }}>
            {summary.latestQuadrant}
          </span>
        </div>
      }
      className="chart-panel-inline regime-relationship-view"
      contentClassName="regime-relationship-content"
      dataTestId="regime-relationship-view"
    >
      <div className="regime-relationship-grid">
        <section
          className="regime-relationship-panel regime-relationship-panel-wide"
          data-testid="regime-spread-card"
        >
          <div className="regime-relationship-panel-head">
            <div>
              <div className="regime-panel-title">CORRELATION RISK PREMIUM</div>
              <div className="regime-relationship-note">Spread = COR1M - RVOL</div>
            </div>
            <div className="regime-relationship-summary">
              <div
                className="regime-relationship-value"
                data-testid="regime-current-spread"
                style={{ color: spreadColor }}
              >
                {fmtSigned(summary.latestSpread)} pts
              </div>
              <div className="regime-relationship-note">
                {relationshipBiasLabel(summary.spreadState, summary.priorSpread, summary.latestSpread)}
              </div>
            </div>
          </div>

          <div
            className="regime-spread-range-chips"
            data-testid="regime-spread-range-chips"
            role="group"
            aria-label="Visible date range"
          >
            {RANGE_PRESETS.map((preset) => {
              const isActive = activeRange === preset.slug;
              return (
                <button
                  key={preset.slug}
                  type="button"
                  className="regime-spread-range-chip"
                  data-testid={`regime-spread-range-${preset.slug}`}
                  data-active={isActive ? "true" : "false"}
                  onClick={() => applyPreset(preset.slug)}
                >
                  {preset.label}
                </button>
              );
            })}
            {activeRange === "custom" ? (
              <span
                className="regime-spread-range-chip regime-spread-range-chip-custom"
                data-testid="regime-spread-range-custom"
                data-active="true"
                aria-label="Custom range from brush selection"
              >
                Custom
              </span>
            ) : null}
          </div>

          <div
            className="regime-relationship-chart-shell"
            data-testid="regime-spread-chart-shell"
            onPointerMove={handleSpreadHover}
            onPointerLeave={() => setSpreadHover(null)}
          >
            <svg
              ref={spreadSvgRef}
              className="regime-relationship-chart"
              data-testid="regime-spread-chart"
              viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
              role="img"
              aria-label="COR1M minus RVOL spread across the visible history window"
            >
              <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
                {spreadScale.ticks(5).map((tick) => (
                  <g key={`spread-grid-${tick}`}>
                    <line
                      x1={0}
                      x2={innerWidth}
                      y1={spreadScale(tick)}
                      y2={spreadScale(tick)}
                      className="regime-relationship-grid-line"
                    />
                    <text
                      x={-10}
                      y={spreadScale(tick) + 4}
                      textAnchor="end"
                      className="regime-relationship-axis-label"
                    >
                      {fmtSigned(tick, 1)}
                    </text>
                  </g>
                ))}

                <line
                  x1={0}
                  x2={innerWidth}
                  y1={spreadScale(0)}
                  y2={spreadScale(0)}
                  className="regime-relationship-baseline"
                />

                {visibleEntries.map((entry, index) => {
                  const x = spreadXScale(index);
                  const zeroY = spreadScale(0);
                  const y = spreadScale(entry.spread);
                  const width = Math.max(innerWidth / Math.max(visibleCount, 1) - 6, 1);
                  const fill = entry.spread >= 0 ? "var(--positive)" : "var(--negative)";
                  return (
                    <rect
                      key={`spread-bar-${entry.date}`}
                      data-testid={`regime-spread-bar-${entry.date}`}
                      x={x - width / 2}
                      y={Math.min(y, zeroY)}
                      width={width}
                      height={Math.max(Math.abs(zeroY - y), 1)}
                      fill={fill}
                      opacity={0.22}
                    />
                  );
                })}

                <path d={spreadLine ?? ""} className="regime-relationship-line regime-relationship-line-spread" />

                <circle
                  cx={spreadXScale(Math.max(visibleCount - 1, 0))}
                  cy={spreadScale(latestVisible.spread)}
                  r={5}
                  className="regime-relationship-marker regime-relationship-marker-spread"
                />

                {spreadTickIndices.map((index) => (
                  <g key={`spread-x-${visibleEntries[index]?.date}`}>
                    <line
                      x1={spreadXScale(index)}
                      x2={spreadXScale(index)}
                      y1={innerHeight}
                      y2={innerHeight + 6}
                      className="regime-relationship-axis-tick"
                    />
                    <text
                      x={spreadXScale(index)}
                      y={innerHeight + 20}
                      textAnchor="middle"
                      className="regime-relationship-axis-label"
                    >
                      {formatDateLabel(visibleEntries[index]?.date ?? "")}
                    </text>
                  </g>
                ))}

                {spreadHover && (
                  <>
                    <line
                      x1={spreadXScale(spreadHover.index)}
                      x2={spreadXScale(spreadHover.index)}
                      y1={0}
                      y2={innerHeight}
                      className="regime-relationship-hover-line"
                    />
                    <circle
                      cx={spreadXScale(spreadHover.index)}
                      cy={spreadScale(spreadHover.entry.spread)}
                      r={5}
                      className="regime-relationship-marker regime-relationship-marker-spread"
                    />
                  </>
                )}

                <rect
                  x={0}
                  y={0}
                  width={innerWidth}
                  height={innerHeight}
                  fill="transparent"
                  pointerEvents="all"
                  className="regime-relationship-chart-overlay"
                  data-testid="regime-spread-chart-overlay"
                  onPointerMove={handleSpreadHover}
                />
              </g>
            </svg>

            {spreadHover && (
              <div
                className="chart-tooltip regime-relationship-chart-tooltip"
                data-testid="regime-spread-hover-tooltip"
                style={{
                  top: `${spreadTooltipTop}px`,
                  ...spreadTooltipSideStyle,
                }}
              >
                <div className="chart-tooltip-date" data-testid="regime-spread-hover-date">
                  {formatDateLabel(spreadHover.entry.date)}
                </div>
                <div className="chart-tooltip-row">
                  <span className="chart-tooltip-label">Spread</span>
                  <span className="chart-tooltip-value">{fmtSigned(spreadHover.entry.spread)}</span>
                </div>
                <div className="chart-tooltip-row">
                  <span className="chart-tooltip-label">RVOL z</span>
                  <span className="chart-tooltip-value">{fmtSigned(spreadHover.entry.realizedVolZ)}σ</span>
                </div>
                <div className="chart-tooltip-row">
                  <span className="chart-tooltip-label">COR1M z</span>
                  <span className="chart-tooltip-value">{fmtSigned(spreadHover.entry.cor1mZ)}σ</span>
                </div>
                <div className="chart-tooltip-row">
                  <span className="chart-tooltip-label">Quadrant</span>
                  <span className="chart-tooltip-value">{spreadHover.entry.quadrant}</span>
                </div>
              </div>
            )}
          </div>

          <div
            ref={brushRef}
            className="regime-spread-brush"
            data-testid="regime-spread-brush"
            style={{ height: `${BRUSH_HEIGHT}px` }}
            aria-label="Range brush minimap"
            role="group"
          >
            <svg
              className="regime-spread-brush-context"
              viewBox={`0 0 ${CHART_WIDTH} ${BRUSH_HEIGHT}`}
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              <path
                d={
                  d3
                    .line<RegimeRelationshipEntry>()
                    .x((_entry, index) => (index / Math.max(entries.length - 1, 1)) * CHART_WIDTH)
                    .y((entry) => {
                      const max = Math.max(
                        ...entries.map((e) => Math.abs(e.spread)),
                        1,
                      );
                      return BRUSH_HEIGHT / 2 - (entry.spread / max) * (BRUSH_HEIGHT / 2 - 4);
                    })
                    .curve(d3.curveMonotoneX)(entries) ?? ""
                }
                className="regime-spread-brush-line"
              />
            </svg>

            <div
              className="regime-spread-brush-window"
              data-testid="regime-spread-brush-window"
              style={{
                left: `${brushLeftPct}%`,
                width: `${brushWidthPct}%`,
              }}
              onPointerDown={handleBrushPointerDown("window")}
            />

            <div
              className="regime-spread-brush-handle regime-spread-brush-handle-left"
              data-testid="regime-spread-brush-handle-left"
              style={{
                left: `calc(${brushLeftPct}% - ${BRUSH_HANDLE_WIDTH / 2}px)`,
                width: `${BRUSH_HANDLE_WIDTH}px`,
              }}
              onPointerDown={handleBrushPointerDown("left")}
              role="slider"
              aria-label="Start of visible range"
              aria-valuemin={0}
              aria-valuemax={entries.length - 1}
              aria-valuenow={visibleStart}
              tabIndex={0}
            />
            <div
              className="regime-spread-brush-handle regime-spread-brush-handle-right"
              data-testid="regime-spread-brush-handle-right"
              style={{
                left: `calc(${brushLeftPct + brushWidthPct}% - ${BRUSH_HANDLE_WIDTH / 2}px)`,
                width: `${BRUSH_HANDLE_WIDTH}px`,
              }}
              onPointerDown={handleBrushPointerDown("right")}
              role="slider"
              aria-label="End of visible range"
              aria-valuemin={0}
              aria-valuemax={entries.length - 1}
              aria-valuenow={visibleEnd}
              tabIndex={0}
            />
          </div>
        </section>

        <section className="regime-relationship-panel regime-relationship-panel-wide" data-testid="regime-quadrant-card">
          <div className="regime-relationship-panel-head">
            <div>
              <div className="regime-panel-title">REGIME QUADRANTS</div>
              <div className="regime-relationship-note">RVOL on X, COR1M on Y</div>
            </div>
            <div className="regime-relationship-summary">
              <div
                className="regime-relationship-value regime-relationship-value-compact"
                data-testid="regime-current-quadrant"
                style={{ color: quadrantColor }}
              >
                {summary.latestQuadrant.toUpperCase()}
              </div>
              <div className="regime-relationship-note">
                Latest: RVOL {latest.realizedVol.toFixed(2)} | COR1M {latest.cor1m.toFixed(2)}
              </div>
            </div>
          </div>

          <div
            className="regime-relationship-chart-shell"
            data-testid="regime-quadrant-chart-shell"
            onPointerMove={handleQuadrantHover}
            onMouseMove={handleQuadrantHover}
            onPointerLeave={() => setQuadrantHover(null)}
            onMouseLeave={() => setQuadrantHover(null)}
          >
            <svg
              ref={quadrantSvgRef}
              className="regime-relationship-chart"
              data-testid="regime-quadrant-chart"
              viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
              role="img"
              aria-label="RVOL versus COR1M regime quadrant"
            >
              <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
                {scatterXScale.ticks(4).map((tick) => (
                  <g key={`scatter-x-${tick}`}>
                    <line
                      x1={scatterXScale(tick)}
                      x2={scatterXScale(tick)}
                      y1={0}
                      y2={innerHeight}
                      className="regime-relationship-grid-line"
                    />
                    <text
                      x={scatterXScale(tick)}
                      y={innerHeight + 20}
                      textAnchor="middle"
                      className="regime-relationship-axis-label"
                    >
                      {tick.toFixed(1)}
                    </text>
                  </g>
                ))}
                {scatterYScale.ticks(4).map((tick) => (
                  <g key={`scatter-y-${tick}`}>
                    <line
                      x1={0}
                      x2={innerWidth}
                      y1={scatterYScale(tick)}
                      y2={scatterYScale(tick)}
                      className="regime-relationship-grid-line"
                    />
                    <text
                      x={-10}
                      y={scatterYScale(tick) + 4}
                      textAnchor="end"
                      className="regime-relationship-axis-label"
                    >
                      {tick.toFixed(1)}
                    </text>
                  </g>
                ))}

                <line
                  x1={scatterXScale(realizedMean)}
                  x2={scatterXScale(realizedMean)}
                  y1={0}
                  y2={innerHeight}
                  className="regime-relationship-baseline"
                />
                <line
                  x1={0}
                  x2={innerWidth}
                  y1={scatterYScale(cor1mMean)}
                  y2={scatterYScale(cor1mMean)}
                  className="regime-relationship-baseline"
                />

                <text x={10} y={18} className="regime-relationship-quadrant-label">Fragile Calm</text>
                <text x={innerWidth - 10} y={18} textAnchor="end" className="regime-relationship-quadrant-label">Systemic Panic</text>
                <text x={10} y={innerHeight - 10} className="regime-relationship-quadrant-label">Goldilocks</text>
                <text x={innerWidth - 10} y={innerHeight - 10} textAnchor="end" className="regime-relationship-quadrant-label">Stock Picker&apos;s</text>

                {entries.map((entry, index) => {
                  const isLatest = index === entries.length - 1;
                  return (
                    <circle
                      key={`scatter-point-${entry.date}`}
                      data-testid={`regime-quadrant-point-${entry.date}`}
                      cx={scatterXScale(entry.realizedVol)}
                      cy={scatterYScale(entry.cor1m)}
                      r={isLatest ? 6 : 3.5}
                      fill={isLatest ? latestQuadrantColor : "var(--signal-core)"}
                      opacity={isLatest ? 1 : 0.18 + (index / entries.length) * 0.45}
                      stroke={isLatest ? latestQuadrantColor : "none"}
                      className={isLatest ? "regime-relationship-marker" : undefined}
                    />
                  );
                })}

                {quadrantHover && (
                  <circle
                    cx={scatterXScale(quadrantHover.entry.realizedVol)}
                    cy={scatterYScale(quadrantHover.entry.cor1m)}
                    r={7}
                    fill={quadrantTone(quadrantHover.entry.quadrant)}
                    stroke={quadrantTone(quadrantHover.entry.quadrant)}
                    className="regime-relationship-hover-marker"
                  />
                )}

                <text
                  x={innerWidth / 2}
                  y={innerHeight + 30}
                  textAnchor="middle"
                  className="regime-relationship-axis-title"
                >
                  RVOL
                </text>
                <text
                  x={-innerHeight / 2}
                  y={-30}
                  textAnchor="middle"
                  transform="rotate(-90)"
                  className="regime-relationship-axis-title"
                >
                  COR1M
                </text>

                <rect
                  x={0}
                  y={0}
                  width={innerWidth}
                  height={innerHeight}
                  fill="transparent"
                  pointerEvents="all"
                  className="regime-relationship-chart-overlay"
                  data-testid="regime-quadrant-chart-overlay"
                  onPointerMove={handleQuadrantHover}
                />
              </g>
            </svg>

            {quadrantHover && (
              <div
                className="chart-tooltip regime-relationship-chart-tooltip"
                data-testid="regime-quadrant-hover-tooltip"
                style={{
                  top: `${quadrantTooltipTop}px`,
                  ...quadrantTooltipSideStyle,
                }}
              >
                <div className="chart-tooltip-date" data-testid="regime-quadrant-hover-date">
                  {formatDateLabel(quadrantHover.entry.date)}
                </div>
                <div className="chart-tooltip-row">
                  <span className="chart-tooltip-label">Quadrant</span>
                  <span className="chart-tooltip-value">{quadrantHover.entry.quadrant}</span>
                </div>
                <div className="chart-tooltip-row">
                  <span className="chart-tooltip-label">RVOL</span>
                  <span className="chart-tooltip-value">{quadrantHover.entry.realizedVol.toFixed(2)}</span>
                </div>
                <div className="chart-tooltip-row">
                  <span className="chart-tooltip-label">COR1M</span>
                  <span className="chart-tooltip-value">{quadrantHover.entry.cor1m.toFixed(2)}</span>
                </div>
                <div className="chart-tooltip-row">
                  <span className="chart-tooltip-label">RVOL z</span>
                  <span className="chart-tooltip-value">{fmtSigned(quadrantHover.entry.realizedVolZ)}σ</span>
                </div>
                <div className="chart-tooltip-row">
                  <span className="chart-tooltip-label">COR1M z</span>
                  <span className="chart-tooltip-value">{fmtSigned(quadrantHover.entry.cor1mZ)}σ</span>
                </div>
              </div>
            )}
          </div>

          <div className="regime-state-key" data-testid="regime-state-key">
            <div className="regime-panel-title">STATE KEY</div>
            <div className="regime-state-key-grid">
              {QUADRANT_DISPLAY_ORDER.map((quadrant) => {
                const slug = quadrantSlug(quadrant);
                const isCurrent = summary.latestQuadrant === quadrant;
                return (
                  <div
                    key={quadrant}
                    className={`regime-state-key-item${isCurrent ? " regime-state-key-item-active" : ""}`}
                    data-testid={`regime-state-item-${slug}`}
                  >
                    <span
                      className="regime-state-key-label"
                      style={{ color: quadrantTone(quadrant) }}
                    >
                      {quadrant.toUpperCase()}
                    </span>
                    <InfoTooltip
                      text={REGIME_QUADRANT_DETAILS[quadrant]}
                      ariaLabel={`Explain ${quadrant}`}
                      triggerTestId={`regime-state-tooltip-trigger-${slug}`}
                      contentTestId={`regime-state-tooltip-bubble-${slug}`}
                    />
                  </div>
                );
              })}
            </div>
          </div>
        </section>

        <section className="regime-relationship-panel regime-relationship-panel-wide" data-testid="regime-zscore-card">
          <div className="regime-relationship-panel-head">
            <div>
              <div className="regime-panel-title">
                NORMALIZED DIVERGENCE
                <InfoTooltip
                  text={SECTION_TOOLTIPS["NORMALIZED DIVERGENCE"]}
                  ariaLabel="Explain normalized divergence"
                  triggerTestId="regime-zscore-tooltip-trigger"
                  contentTestId="regime-zscore-tooltip-bubble"
                />
              </div>
              <div className="regime-relationship-note">20-session z-score overlay</div>
            </div>
            <div className="regime-relationship-summary">
              <div
                className="regime-relationship-value regime-relationship-value-compact"
                data-testid="regime-current-zgap"
                style={{ color: spreadStateColor(summary.zScoreBias) }}
              >
                {fmtSigned(summary.latestDivergence)}σ
              </div>
              <div className="regime-relationship-note">
                {summary.zScoreBias} | COR1M z - RVOL z
              </div>
            </div>
          </div>

          <div
            className="regime-relationship-chart-shell"
            data-testid="regime-zscore-chart-shell"
            onPointerMove={handleZScoreHover}
            onPointerLeave={() => setZScoreHover(null)}
          >
            <svg
              ref={zScoreSvgRef}
              className="regime-relationship-chart"
              data-testid="regime-zscore-chart"
              viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
              role="img"
              aria-label="Normalized COR1M and RVOL z-score comparison"
            >
              <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
                {zScale.ticks(5).map((tick) => (
                  <g key={`z-grid-${tick}`}>
                    <line
                      x1={0}
                      x2={innerWidth}
                      y1={zScale(tick)}
                      y2={zScale(tick)}
                      className="regime-relationship-grid-line"
                    />
                    <text
                      x={-10}
                      y={zScale(tick) + 4}
                      textAnchor="end"
                      className="regime-relationship-axis-label"
                    >
                      {fmtSigned(tick, 1)}
                    </text>
                  </g>
                ))}

                <line
                  x1={0}
                  x2={innerWidth}
                  y1={zScale(0)}
                  y2={zScale(0)}
                  className="regime-relationship-baseline"
                />

                <path d={zRvolLine ?? ""} className="regime-relationship-line regime-relationship-line-rvol" />
                <path d={zCor1mLine ?? ""} className="regime-relationship-line regime-relationship-line-cor1m" />

                <circle
                  cx={xScale(entries.length - 1)}
                  cy={zScale(latest.realizedVolZ)}
                  r={4}
                  className="regime-relationship-marker regime-relationship-marker-rvol"
                />
                <circle
                  cx={xScale(entries.length - 1)}
                  cy={zScale(latest.cor1mZ)}
                  r={4}
                  className="regime-relationship-marker regime-relationship-marker-cor1m"
                />

                {zScoreHover && (
                  <>
                    <line
                      x1={xScale(zScoreHover.index)}
                      x2={xScale(zScoreHover.index)}
                      y1={0}
                      y2={innerHeight}
                      className="regime-relationship-hover-line"
                    />
                    <circle
                      cx={xScale(zScoreHover.index)}
                      cy={zScale(zScoreHover.entry.realizedVolZ)}
                      r={5}
                      className="regime-relationship-marker regime-relationship-marker-rvol"
                    />
                    <circle
                      cx={xScale(zScoreHover.index)}
                      cy={zScale(zScoreHover.entry.cor1mZ)}
                      r={5}
                      className="regime-relationship-marker regime-relationship-marker-cor1m"
                    />
                  </>
                )}

                {tickIndices.map((index) => (
                  <g key={`z-x-${entries[index]?.date}`}>
                    <line
                      x1={xScale(index)}
                      x2={xScale(index)}
                      y1={innerHeight}
                      y2={innerHeight + 6}
                      className="regime-relationship-axis-tick"
                    />
                    <text
                      x={xScale(index)}
                      y={innerHeight + 20}
                      textAnchor="middle"
                      className="regime-relationship-axis-label"
                    >
                      {formatDateLabel(entries[index]?.date ?? "")}
                    </text>
                  </g>
                ))}

                <rect
                  x={0}
                  y={0}
                  width={innerWidth}
                  height={innerHeight}
                  fill="transparent"
                  pointerEvents="all"
                  className="regime-relationship-chart-overlay"
                  data-testid="regime-zscore-chart-overlay"
                  onPointerMove={handleZScoreHover}
                />
              </g>
            </svg>

            {zScoreHover && (
              <div
                className="chart-tooltip regime-relationship-chart-tooltip"
                data-testid="regime-zscore-hover-tooltip"
                style={{
                  top: `${zScoreTooltipTop}px`,
                  ...zScoreTooltipSideStyle,
                }}
              >
                <div className="chart-tooltip-date" data-testid="regime-zscore-hover-date">
                  {formatDateLabel(zScoreHover.entry.date)}
                </div>
                <div className="chart-tooltip-row">
                  <span className="chart-tooltip-label">RVOL z-score</span>
                  <span className="chart-tooltip-value">{fmtSigned(zScoreHover.entry.realizedVolZ)}σ</span>
                </div>
                <div className="chart-tooltip-row">
                  <span className="chart-tooltip-label">COR1M z-score</span>
                  <span className="chart-tooltip-value">{fmtSigned(zScoreHover.entry.cor1mZ)}σ</span>
                </div>
                <div className="chart-tooltip-row">
                  <span className="chart-tooltip-label">Divergence</span>
                  <span className="chart-tooltip-value">{fmtSigned(zScoreHover.entry.zDivergence)}σ</span>
                </div>
              </div>
            )}
          </div>

          <ChartLegend
            className="regime-relationship-shared-legend"
            items={[
              { label: "RVOL z-score", role: "caution" },
              { label: "COR1M z-score", role: "dislocation" },
            ]}
          />
        </section>
      </div>
    </ChartPanel>
  );
}
