"use client";

import { useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { scaleLinear } from "@/lib/scales";
import { linePath } from "@/lib/svgPath";
import { extent, mean } from "@/lib/arrayUtils";
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
const MARGIN = { top: 16, right: 20, bottom: 32, left: 44 };
const DATE_FORMATTER = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });

import { fmtSigned } from "@/lib/format";

function formatDateLabel(value: string): string {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return DATE_FORMATTER.format(date);
}

function buildTickIndices(length: number, count = 4): number[] {
  if (length <= count) {
    return Array.from({ length }, (_, index) => index);
  }

  const step = (length - 1) / (count - 1);
  const indices = new Set<number>();
  for (let tick = 0; tick < count; tick += 1) {
    indices.add(Math.round(step * tick));
  }
  return Array.from(indices).sort((a, b) => a - b);
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

export default function RegimeRelationshipView({
  history,
  liveValues,
}: RegimeRelationshipViewProps) {
  const zScoreSvgRef = useRef<SVGSVGElement>(null);
  const [zScoreHover, setZScoreHover] = useState<ZScoreHoverState | null>(null);
  const entries = useMemo(
    () => buildRegimeRelationshipEntries(history, liveValues),
    [history, liveValues],
  );
  const summary = useMemo(
    () => summarizeRegimeRelationship(entries),
    [entries],
  );

  if (!summary || entries.length < 2) {
    return null;
  }

  const innerWidth = CHART_WIDTH - MARGIN.left - MARGIN.right;
  const innerHeight = CHART_HEIGHT - MARGIN.top - MARGIN.bottom;
  const xScale = scaleLinear().domain([0, entries.length - 1]).range([0, innerWidth]);
  const tickIndices = buildTickIndices(entries.length);

  const spreadMax = Math.max(
    ...entries.map((entry) => Math.abs(entry.spread)),
    Math.abs(summary.meanSpread),
    1,
  );
  const spreadScale = scaleLinear()
    .domain([-(spreadMax * 1.15), spreadMax * 1.15])
    .range([innerHeight, 0]);
  const spreadLine = linePath<(typeof entries)[number]>()
    .x((_entry, index) => xScale(index))
    .y((entry) => spreadScale(entry.spread))(entries);

  const realizedExtent = extent(entries, (entry) => entry.realizedVol) as [number, number];
  const cor1mExtent = extent(entries, (entry) => entry.cor1m) as [number, number];
  const realizedPad = (realizedExtent[1] - realizedExtent[0]) * 0.18 || 1;
  const cor1mPad = (cor1mExtent[1] - cor1mExtent[0]) * 0.18 || 1;
  const scatterXScale = scaleLinear()
    .domain([realizedExtent[0] - realizedPad, realizedExtent[1] + realizedPad])
    .range([0, innerWidth]);
  const scatterYScale = scaleLinear()
    .domain([cor1mExtent[0] - cor1mPad, cor1mExtent[1] + cor1mPad])
    .range([innerHeight, 0]);
  const realizedMean = mean(entries, (entry) => entry.realizedVol) ?? 0;
  const cor1mMean = mean(entries, (entry) => entry.cor1m) ?? 0;

  const zMax = Math.max(
    ...entries.map((entry) => Math.max(Math.abs(entry.realizedVolZ), Math.abs(entry.cor1mZ))),
    Math.abs(summary.latestDivergence),
    1,
  );
  const zScale = scaleLinear()
    .domain([-(zMax * 1.15), zMax * 1.15])
    .range([innerHeight, 0]);
  const zRvolLine = linePath<(typeof entries)[number]>()
    .x((_entry, index) => xScale(index))
    .y((entry) => zScale(entry.realizedVolZ))(entries);
  const zCor1mLine = linePath<(typeof entries)[number]>()
    .x((_entry, index) => xScale(index))
    .y((entry) => zScale(entry.cor1mZ))(entries);

  const latest = entries[entries.length - 1];
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
      className="cp-i regime-relationship-view"
      contentClassName="regime-relationship-content"
      dataTestId="regime-relationship-view"
    >
      <div className="regime-relationship-grid">
        <section
          className="rr-pa regime-relationship-panel-wide"
          data-testid="regime-spread-card"
        >
          <div className="rr-ph">
            <div>
              <div className="rp-t">CORRELATION RISK PREMIUM</div>
              <div className="rr-n">Spread = COR1M - RVOL</div>
            </div>
            <div className="rr-su">
              <div
                className="rr-va"
                data-testid="regime-current-spread"
                style={{ color: spreadColor }}
              >
                {fmtSigned(summary.latestSpread)} pts
              </div>
              <div className="rr-n">
                {relationshipBiasLabel(summary.spreadState, summary.priorSpread, summary.latestSpread)}
              </div>
            </div>
          </div>

          <svg
            className="rr-ch"
            data-testid="regime-spread-chart"
            viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
            role="img"
            aria-label="COR1M minus RVOL spread over the last 20 sessions"
          >
            <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
              {spreadScale.ticks(5).map((tick) => (
                <g key={`spread-grid-${tick}`}>
                  <line
                    x1={0}
                    x2={innerWidth}
                    y1={spreadScale(tick)}
                    y2={spreadScale(tick)}
                    className="rr-gl"
                  />
                  <text
                    x={-10}
                    y={spreadScale(tick) + 4}
                    textAnchor="end"
                    className="rr-al"
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
                className="rr-bl"
              />

              {entries.map((entry, index) => {
                const x = xScale(index);
                const zeroY = spreadScale(0);
                const y = spreadScale(entry.spread);
                const width = Math.max(innerWidth / entries.length - 6, 6);
                const fill = entry.spread >= 0 ? "var(--positive)" : "var(--negative)";
                return (
                  <rect
                    key={`spread-bar-${entry.date}`}
                    x={x - width / 2}
                    y={Math.min(y, zeroY)}
                    width={width}
                    height={Math.max(Math.abs(zeroY - y), 1)}
                    fill={fill}
                    opacity={0.22}
                  />
                );
              })}

              <path d={spreadLine ?? ""} className="rr-li regime-relationship-line-spread" />

              <circle
                cx={xScale(entries.length - 1)}
                cy={spreadScale(latest.spread)}
                r={5}
                className="rr-m rr-m-spread"
              />

              {tickIndices.map((index) => (
                <g key={`spread-x-${entries[index]?.date}`}>
                  <line
                    x1={xScale(index)}
                    x2={xScale(index)}
                    y1={innerHeight}
                    y2={innerHeight + 6}
                    className="rr-ak"
                  />
                  <text
                    x={xScale(index)}
                    y={innerHeight + 20}
                    textAnchor="middle"
                    className="rr-al"
                  >
                    {formatDateLabel(entries[index]?.date ?? "")}
                  </text>
                </g>
              ))}
            </g>
          </svg>
        </section>

        <section className="rr-pa" data-testid="regime-quadrant-card">
          <div className="rr-ph">
            <div>
              <div className="rp-t">REGIME QUADRANTS</div>
              <div className="rr-n">RVOL on X, COR1M on Y</div>
            </div>
            <div className="rr-su">
              <div
                className="rr-va rr-vc"
                data-testid="regime-current-quadrant"
                style={{ color: quadrantColor }}
              >
                {summary.latestQuadrant.toUpperCase()}
              </div>
              <div className="rr-n">
                Latest: RVOL {latest.realizedVol.toFixed(2)} | COR1M {latest.cor1m.toFixed(2)}
              </div>
            </div>
          </div>

          <svg
            className="rr-ch"
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
                    className="rr-gl"
                  />
                  <text
                    x={scatterXScale(tick)}
                    y={innerHeight + 20}
                    textAnchor="middle"
                    className="rr-al"
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
                    className="rr-gl"
                  />
                  <text
                    x={-10}
                    y={scatterYScale(tick) + 4}
                    textAnchor="end"
                    className="rr-al"
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
                className="rr-bl"
              />
              <line
                x1={0}
                x2={innerWidth}
                y1={scatterYScale(cor1mMean)}
                y2={scatterYScale(cor1mMean)}
                className="rr-bl"
              />

              <text x={10} y={18} className="rr-ql">Fragile Calm</text>
              <text x={innerWidth - 10} y={18} textAnchor="end" className="rr-ql">Systemic Panic</text>
              <text x={10} y={innerHeight - 10} className="rr-ql">Goldilocks</text>
              <text x={innerWidth - 10} y={innerHeight - 10} textAnchor="end" className="rr-ql">Stock Picker&apos;s</text>

              {entries.map((entry, index) => {
                const isLatest = index === entries.length - 1;
                return (
                  <circle
                    key={`scatter-point-${entry.date}`}
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

              <text
                x={innerWidth / 2}
                y={innerHeight + 30}
                textAnchor="middle"
                className="rr-at"
              >
                RVOL
              </text>
              <text
                x={-innerHeight / 2}
                y={-30}
                textAnchor="middle"
                transform="rotate(-90)"
                className="rr-at"
              >
                COR1M
              </text>
            </g>
          </svg>

          <div className="regime-state-key" data-testid="regime-state-key">
            <div className="rp-t">STATE KEY</div>
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

        <section className="rr-pa" data-testid="regime-zscore-card">
          <div className="rr-ph">
            <div>
              <div className="rp-t">
                NORMALIZED DIVERGENCE
                <InfoTooltip
                  text={SECTION_TOOLTIPS["NORMALIZED DIVERGENCE"]}
                  ariaLabel="Explain normalized divergence"
                  triggerTestId="regime-zscore-tooltip-trigger"
                  contentTestId="regime-zscore-tooltip-bubble"
                />
              </div>
              <div className="rr-n">20-session z-score overlay</div>
            </div>
            <div className="rr-su">
              <div
                className="rr-va rr-vc"
                data-testid="regime-current-zgap"
                style={{ color: spreadStateColor(summary.zScoreBias) }}
              >
                {fmtSigned(summary.latestDivergence)}σ
              </div>
              <div className="rr-n">
                {summary.zScoreBias} | COR1M z - RVOL z
              </div>
            </div>
          </div>

          <div
            className="regime-relationship-chart-shell"
            data-testid="regime-zscore-chart-shell"
            onMouseMove={handleZScoreHover}
            onMouseLeave={() => setZScoreHover(null)}
          >
            <svg
              ref={zScoreSvgRef}
              className="rr-ch"
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
                      className="rr-gl"
                    />
                    <text
                      x={-10}
                      y={zScale(tick) + 4}
                      textAnchor="end"
                      className="rr-al"
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
                  className="rr-bl"
                />

                <path d={zRvolLine ?? ""} className="rr-li regime-relationship-line-rvol" />
                <path d={zCor1mLine ?? ""} className="rr-li regime-relationship-line-cor1m" />

                <circle
                  cx={xScale(entries.length - 1)}
                  cy={zScale(latest.realizedVolZ)}
                  r={4}
                  className="rr-m rr-m-rvol"
                />
                <circle
                  cx={xScale(entries.length - 1)}
                  cy={zScale(latest.cor1mZ)}
                  r={4}
                  className="rr-m rr-m-cor1m"
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
                      className="rr-m rr-m-rvol"
                    />
                    <circle
                      cx={xScale(zScoreHover.index)}
                      cy={zScale(zScoreHover.entry.cor1mZ)}
                      r={5}
                      className="rr-m rr-m-cor1m"
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
                      className="rr-ak"
                    />
                    <text
                      x={xScale(index)}
                      y={innerHeight + 20}
                      textAnchor="middle"
                      className="rr-al"
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
                  onMouseMove={handleZScoreHover}
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
                <div className="ct-r">
                  <span className="ct-l">RVOL z-score</span>
                  <span className="ct-v">{fmtSigned(zScoreHover.entry.realizedVolZ)}σ</span>
                </div>
                <div className="ct-r">
                  <span className="ct-l">COR1M z-score</span>
                  <span className="ct-v">{fmtSigned(zScoreHover.entry.cor1mZ)}σ</span>
                </div>
                <div className="ct-r">
                  <span className="ct-l">Divergence</span>
                  <span className="ct-v">{fmtSigned(zScoreHover.entry.zDivergence)}σ</span>
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
