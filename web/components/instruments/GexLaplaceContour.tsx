"use client";

/**
 * GexLaplaceContour — brand-true Laplace-grammar visualization for net GEX.
 *
 * Gamma is the second derivative of option price w.r.t. underlying, so the
 * GEX profile is fundamentally a curvature surface. The brand identity
 * (docs/brand-identity.md § 7) assigns Laplace its own visual grammar:
 * filled curvature areas, projection-geometry guides, and field-line traces.
 * This component renders the cached GexBucket[] profile under that grammar
 * with hand-drawn SVG paths and brand tokens only — no Recharts, no Tremor.
 *
 * Positive curvature (net_gex >= 0) fills above the zero line in the
 * signal-core teal. Negative curvature fills below in the dislocation
 * magenta. A 2px contour trace rides the strike axis, the flip line marks
 * the zero crossing, and a vertical spot marker anchors the underlying.
 */

import { useMemo, useState, useCallback } from "react";

import type { GexBucket } from "@/lib/useGex";

const MARKER_LABEL_FONT_SIZE = 12; // Clear financial annotation floor
const MARKER_LABEL_CHAR_WIDTH = MARKER_LABEL_FONT_SIZE * 0.64;
const MARKER_LABEL_GAP = 6; // minimum horizontal breathing room between adjacent labels
const MARKER_LANE_HEIGHT = MARKER_LABEL_FONT_SIZE + 4;
const MARKER_TRIANGLE_DROP = 9; // distance from axis to triangle base
const MARKER_FIRST_LANE_DROP = MARKER_TRIANGLE_DROP + MARKER_LABEL_FONT_SIZE + 3;

const MAX_MARKER_LANES = 5; // PUT WALL / CALL WALL / ACCEL / MAGNET / FLIP can all collide
const MARKER_LANE_BAND = MARKER_FIRST_LANE_DROP + MARKER_LANE_HEIGHT * (MAX_MARKER_LANES - 1) + 6;

const VIEWBOX_WIDTH = 800;
const PADDING_LEFT = 56;
const PADDING_RIGHT = 24;
const PADDING_TOP = 36;
const PADDING_BOTTOM = MARKER_LANE_BAND + 8;
const PLOT_HEIGHT_BASE = 184; // preserve the original curvature-field plot height
const VIEWBOX_HEIGHT = PLOT_HEIGHT_BASE + PADDING_TOP + PADDING_BOTTOM;
const PLOT_WIDTH = VIEWBOX_WIDTH - PADDING_LEFT - PADDING_RIGHT;
const PLOT_HEIGHT = VIEWBOX_HEIGHT - PADDING_TOP - PADDING_BOTTOM;

const POSITIVE_FILL = "color-mix(in srgb, var(--signal-core) 24%, transparent)";
const NEGATIVE_FILL = "color-mix(in srgb, var(--dislocation) 28%, transparent)";
const POSITIVE_EDGE = "color-mix(in srgb, var(--signal-core) 55%, transparent)";
const NEGATIVE_EDGE = "color-mix(in srgb, var(--dislocation) 55%, transparent)";

type GexLaplaceContourProps = {
  profile: GexBucket[];
  spotPrice: number;
  flipStrike: number | null;
  maxMagnet: number | null;
  maxAccelerator: number | null;
  putWall: number | null;
  callWall: number | null;
  ticker?: string;
};

type Point = { strike: number; netGex: number; x: number; y: number; curvature: number };

type StrikeDomain = { minStrike: number; maxStrike: number; maxAbsGex: number };

function ascendingByStrike(profile: GexBucket[]): GexBucket[] {
  return [...profile].sort((a, b) => a.strike - b.strike);
}

function computeDomain(profile: GexBucket[]): StrikeDomain {
  if (!profile.length) return { minStrike: 0, maxStrike: 1, maxAbsGex: 1 };
  const strikes = profile.map((b) => b.strike);
  const gexAbs = profile.map((b) => Math.abs(b.net_gex));
  return {
    minStrike: Math.min(...strikes),
    maxStrike: Math.max(...strikes),
    maxAbsGex: Math.max(...gexAbs, 1),
  };
}

function projectX(strike: number, domain: StrikeDomain): number {
  const span = domain.maxStrike - domain.minStrike || 1;
  return PADDING_LEFT + ((strike - domain.minStrike) / span) * PLOT_WIDTH;
}

function projectY(netGex: number, domain: StrikeDomain): number {
  const half = PLOT_HEIGHT / 2;
  const centerY = PADDING_TOP + half;
  return centerY - (netGex / domain.maxAbsGex) * half * 0.9;
}

function curvatureAt(buckets: GexBucket[], index: number): number {
  if (buckets.length < 3) return 0;
  const prev = buckets[Math.max(0, index - 1)];
  const curr = buckets[index];
  const next = buckets[Math.min(buckets.length - 1, index + 1)];
  const dStrike = (next.strike - prev.strike) || 1;
  const finiteSecondDeriv = (next.net_gex - 2 * curr.net_gex + prev.net_gex) / Math.pow(dStrike / 2, 2);
  const scale = Math.max(Math.abs(curr.net_gex), 1);
  return finiteSecondDeriv / scale;
}

function buildPoints(buckets: GexBucket[], domain: StrikeDomain): Point[] {
  return buckets.map((bucket, i) => ({
    strike: bucket.strike,
    netGex: bucket.net_gex,
    x: projectX(bucket.strike, domain),
    y: projectY(bucket.net_gex, domain),
    curvature: curvatureAt(buckets, i),
  }));
}

function tracePath(points: Point[]): string {
  if (!points.length) return "";
  const head = `M ${points[0].x.toFixed(2)} ${points[0].y.toFixed(2)}`;
  const segments = points.slice(1).map((p) => `L ${p.x.toFixed(2)} ${p.y.toFixed(2)}`);
  return [head, ...segments].join(" ");
}

function buildSidedAreaPath(points: Point[], domain: StrikeDomain, side: "positive" | "negative"): string {
  if (!points.length) return "";
  const centerY = projectY(0, domain);
  const clamp = (p: Point): number => {
    if (side === "positive") return Math.min(p.y, centerY);
    return Math.max(p.y, centerY);
  };
  const head = `M ${points[0].x.toFixed(2)} ${centerY.toFixed(2)}`;
  const ridge = points.map((p) => `L ${p.x.toFixed(2)} ${clamp(p).toFixed(2)}`);
  const tail = `L ${points[points.length - 1].x.toFixed(2)} ${centerY.toFixed(2)} Z`;
  return [head, ...ridge, tail].join(" ");
}

function zeroCrossingStrike(buckets: GexBucket[], fallback: number | null): number | null {
  for (let i = 1; i < buckets.length; i++) {
    const prev = buckets[i - 1];
    const curr = buckets[i];
    if (prev.net_gex === 0) return prev.strike;
    if (curr.net_gex === 0) return curr.strike;
    if (Math.sign(prev.net_gex) !== Math.sign(curr.net_gex)) {
      const slope = (curr.net_gex - prev.net_gex) || 1;
      const t = -prev.net_gex / slope;
      return prev.strike + t * (curr.strike - prev.strike);
    }
  }
  return fallback;
}

function fmtPrice(value: number): string {
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtStrike(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function fmtGex(value: number): string {
  const abs = Math.abs(value);
  const sign = value >= 0 ? "+" : "-";
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

function fmtCurvature(value: number): string {
  const sign = value >= 0 ? "+" : "-";
  return `${sign}${Math.abs(value).toFixed(2)}`;
}

function nearestPoint(points: Point[], strike: number): Point | null {
  if (!points.length) return null;
  return points.reduce<Point>((acc, p) => {
    return Math.abs(p.strike - strike) < Math.abs(acc.strike - strike) ? p : acc;
  }, points[0]);
}

type ProjectionGuide = { x1: number; y1: number; x2: number; y2: number };

function buildProjectionGuides(): ProjectionGuide[] {
  const guides: ProjectionGuide[] = [];
  const stride = PLOT_WIDTH / 5;
  for (let i = 1; i <= 4; i++) {
    const xStart = PADDING_LEFT + stride * i * 0.5;
    const xEnd = xStart + PLOT_HEIGHT * Math.tan((Math.PI / 180) * 55);
    guides.push({
      x1: xStart,
      y1: PADDING_TOP,
      x2: xEnd,
      y2: PADDING_TOP + PLOT_HEIGHT,
    });
  }
  return guides;
}

type LevelMarker = {
  testId: string;
  label: string;
  strike: number;
  color: string;
};

function buildLevelMarkers({
  maxMagnet,
  maxAccelerator,
  putWall,
  callWall,
  flipStrike,
  domain,
}: {
  maxMagnet: number | null;
  maxAccelerator: number | null;
  putWall: number | null;
  callWall: number | null;
  flipStrike: number | null;
  domain: StrikeDomain;
}): LevelMarker[] {
  const candidates: LevelMarker[] = [];
  if (maxMagnet != null) {
    candidates.push({ testId: "gex-level-marker-max-magnet", label: "MAGNET", strike: maxMagnet, color: "var(--signal-core-text)" });
  }
  if (maxAccelerator != null) {
    candidates.push({ testId: "gex-level-marker-max-accelerator", label: "ACCEL", strike: maxAccelerator, color: "var(--fault)" });
  }
  if (putWall != null) {
    candidates.push({ testId: "gex-level-marker-put-wall", label: "PUT WALL", strike: putWall, color: "var(--dislocation)" });
  }
  if (callWall != null && callWall !== putWall) {
    candidates.push({ testId: "gex-level-marker-call-wall", label: "CALL WALL", strike: callWall, color: "var(--signal-strong)" });
  }
  if (flipStrike != null) {
    candidates.push({ testId: "gex-level-marker-flip", label: "FLIP", strike: flipStrike, color: "var(--warn-text)" });
  }
  return candidates.filter((m) => m.strike >= domain.minStrike && m.strike <= domain.maxStrike);
}

export type PlacedMarker = LevelMarker & {
  x: number;
  lane: number;
  labelWidth: number;
};

export const MARKER_LABEL_LANE_GAP = MARKER_LABEL_GAP;

function estimateLabelWidth(label: string): number {
  return label.length * MARKER_LABEL_CHAR_WIDTH;
}

/**
 * Assign each marker to a horizontal lane so its centered text label never
 * overlaps a label already placed in the same lane. Markers are processed
 * left-to-right; a marker drops to the next lane whenever its label box would
 * collide with the previous occupant of every shallower lane. Co-located
 * strikes therefore stack vertically with leader lines instead of smearing
 * their text into one another.
 */
export function assignMarkerLanes(markers: LevelMarker[], domain: StrikeDomain): PlacedMarker[] {
  const ordered = [...markers].sort((a, b) => a.strike - b.strike);
  const laneRightEdges: number[] = [];
  return ordered.map((marker) => {
    const x = projectX(marker.strike, domain);
    const labelWidth = estimateLabelWidth(marker.label);
    const leftEdge = x - labelWidth / 2 - MARKER_LABEL_GAP;
    let lane = laneRightEdges.findIndex((rightEdge) => leftEdge >= rightEdge);
    if (lane === -1) lane = laneRightEdges.length;
    laneRightEdges[lane] = x + labelWidth / 2 + MARKER_LABEL_GAP;
    return { ...marker, x, lane, labelWidth };
  });
}

function readoutFor(point: Point | null): { strike: string; netGex: string; curvature: string } {
  if (!point) {
    return { strike: "---", netGex: "---", curvature: "---" };
  }
  return {
    strike: fmtStrike(point.strike),
    netGex: fmtGex(point.netGex),
    curvature: fmtCurvature(point.curvature),
  };
}

export default function GexLaplaceContour({
  profile,
  spotPrice,
  flipStrike,
  maxMagnet,
  maxAccelerator,
  putWall,
  callWall,
  ticker = "",
}: GexLaplaceContourProps) {
  const buckets = useMemo(() => ascendingByStrike(profile), [profile]);
  const domain = useMemo(() => computeDomain(buckets), [buckets]);
  const points = useMemo(() => buildPoints(buckets, domain), [buckets, domain]);
  const projectionGuides = useMemo(() => buildProjectionGuides(), []);
  const crossingStrike = useMemo(() => zeroCrossingStrike(buckets, flipStrike), [buckets, flipStrike]);
  const levelMarkers = useMemo(
    () => buildLevelMarkers({ maxMagnet, maxAccelerator, putWall, callWall, flipStrike, domain }),
    [maxMagnet, maxAccelerator, putWall, callWall, flipStrike, domain],
  );
  const placedMarkers = useMemo(() => assignMarkerLanes(levelMarkers, domain), [levelMarkers, domain]);

  const initialHoverStrike = useMemo(() => {
    const spotPoint = nearestPoint(points, spotPrice);
    return spotPoint?.strike ?? domain.minStrike;
  }, [points, spotPrice, domain.minStrike]);

  const [hoverStrike, setHoverStrike] = useState<number>(initialHoverStrike);
  const hoverPoint = useMemo(() => nearestPoint(points, hoverStrike), [points, hoverStrike]);
  const readout = readoutFor(hoverPoint);

  const onPointerMove = useCallback((event: React.PointerEvent<SVGSVGElement>) => {
    const svg = event.currentTarget;
    const rect = svg.getBoundingClientRect();
    const ratio = VIEWBOX_WIDTH / rect.width;
    const localX = (event.clientX - rect.left) * ratio;
    const span = domain.maxStrike - domain.minStrike || 1;
    const strikeApprox = domain.minStrike + ((localX - PADDING_LEFT) / PLOT_WIDTH) * span;
    setHoverStrike(strikeApprox);
  }, [domain.maxStrike, domain.minStrike]);

  const onPointerLeave = useCallback(() => {
    setHoverStrike(initialHoverStrike);
  }, [initialHoverStrike]);

  const centerY = projectY(0, domain);
  const positiveAreaPath = buildSidedAreaPath(points, domain, "positive");
  const negativeAreaPath = buildSidedAreaPath(points, domain, "negative");
  const contourPath = tracePath(points);

  const flipX = crossingStrike != null ? projectX(crossingStrike, domain) : null;
  const spotX = projectX(spotPrice, domain);
  const hoverX = hoverPoint ? hoverPoint.x : null;

  const yTopLabel = fmtGex(domain.maxAbsGex);
  const yBottomLabel = fmtGex(-domain.maxAbsGex);

  return (
    <div className="gex-laplace-wrap" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div
        data-testid="gex-readout"
        className="gex-laplace-readout"
        style={{
          display: "flex",
          gap: 18,
          fontFamily: "var(--font-mono)",
          fontSize: "var(--text-meta)",
          color: "var(--text-secondary)",
          letterSpacing: "0.04em",
        }}
      >
        <span style={{ color: "var(--text-muted)" }}>LAPLACE / CURVATURE FIELD</span>
        <span>
          <span style={{ color: "var(--text-muted)" }}>STRIKE </span>
          <span style={{ color: "var(--text-primary)" }}>{readout.strike}</span>
        </span>
        <span>
          <span style={{ color: "var(--text-muted)" }}>NET GEX </span>
          <span style={{ color: hoverPoint && hoverPoint.netGex >= 0 ? "var(--signal-core)" : "var(--dislocation)" }}>
            {readout.netGex}
          </span>
        </span>
        <span>
          <span style={{ color: "var(--text-muted)" }}>CURVATURE </span>
          <span style={{ color: "var(--text-primary)" }}>{readout.curvature}</span>
        </span>
      </div>

      <svg
        viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
        width="100%"
        height={VIEWBOX_HEIGHT}
        preserveAspectRatio="none"
        onPointerMove={onPointerMove}
        onPointerLeave={onPointerLeave}
        style={{ display: "block", background: "var(--bg-panel-raised)", borderRadius: 4 }}
        role="img"
        aria-label={`Gamma exposure curvature field for ${ticker || "ticker"}`}
      >
        <defs>
          <clipPath id="gex-laplace-plot-clip">
            <rect x={PADDING_LEFT} y={PADDING_TOP} width={PLOT_WIDTH} height={PLOT_HEIGHT} />
          </clipPath>
        </defs>

        {projectionGuides.map((guide, i) => (
          <line
            key={`guide-${i}`}
            data-testid="gex-projection-guide"
            x1={guide.x1}
            y1={guide.y1}
            x2={guide.x2}
            y2={guide.y2}
            stroke="var(--line-grid)"
            strokeWidth={1}
            opacity={0.4}
            clipPath="url(#gex-laplace-plot-clip)"
          />
        ))}

        <line
          x1={PADDING_LEFT}
          y1={centerY}
          x2={VIEWBOX_WIDTH - PADDING_RIGHT}
          y2={centerY}
          stroke="var(--line-grid)"
          strokeWidth={1}
        />
        <line
          x1={PADDING_LEFT}
          y1={PADDING_TOP}
          x2={PADDING_LEFT}
          y2={VIEWBOX_HEIGHT - PADDING_BOTTOM}
          stroke="var(--line-grid)"
          strokeWidth={1}
        />

        <text
          x={PADDING_LEFT - 6}
          y={PADDING_TOP + 4}
          textAnchor="end"
          fontFamily="var(--font-mono)"
          fontSize="var(--text-meta)"
          fill="var(--text-muted)"
        >
          {yTopLabel}
        </text>
        <text
          x={PADDING_LEFT - 6}
          y={centerY + 3}
          textAnchor="end"
          fontFamily="var(--font-mono)"
          fontSize="var(--text-meta)"
          fill="var(--text-muted)"
        >
          0
        </text>
        <text
          x={PADDING_LEFT - 6}
          y={VIEWBOX_HEIGHT - PADDING_BOTTOM + 2}
          textAnchor="end"
          fontFamily="var(--font-mono)"
          fontSize="var(--text-meta)"
          fill="var(--text-muted)"
        >
          {yBottomLabel}
        </text>

        <path
          data-testid="gex-curvature-positive"
          d={positiveAreaPath}
          fill={POSITIVE_FILL}
          stroke={POSITIVE_EDGE}
          strokeWidth={1}
        />
        <path
          data-testid="gex-curvature-negative"
          d={negativeAreaPath}
          fill={NEGATIVE_FILL}
          stroke={NEGATIVE_EDGE}
          strokeWidth={1}
        />

        <path
          data-testid="gex-curvature-trace"
          d={contourPath}
          fill="none"
          stroke="var(--signal-core)"
          strokeWidth={2}
        />

        {flipX != null && (
          <>
            <line
              data-testid="gex-flip-line"
              x1={flipX}
              y1={PADDING_TOP}
              x2={flipX}
              y2={VIEWBOX_HEIGHT - PADDING_BOTTOM}
              stroke="var(--warn)"
              strokeWidth={1}
              strokeDasharray="4 3"
            />
            <text
              x={flipX + 4}
              y={PADDING_TOP + 10}
              fontFamily="var(--font-mono)"
              fontSize="var(--text-meta)"
              fill="var(--warn)"
            >
              FLIP {fmtStrike(crossingStrike ?? 0)}
            </text>
          </>
        )}

        <line
          data-testid="gex-spot-line"
          x1={spotX}
          y1={PADDING_TOP - 6}
          x2={spotX}
          y2={VIEWBOX_HEIGHT - PADDING_BOTTOM}
          stroke="var(--text-muted)"
          strokeWidth={1}
        />
        <text
          x={spotX}
          y={PADDING_TOP - 10}
          textAnchor="middle"
          fontFamily="var(--font-mono)"
          fontSize="var(--text-meta)"
          fill="var(--text-primary)"
        >
          SPOT {fmtPrice(spotPrice)}
        </text>

        {placedMarkers.map((marker) => {
          const axisY = VIEWBOX_HEIGHT - PADDING_BOTTOM;
          const triangleBaseY = axisY + MARKER_TRIANGLE_DROP;
          const labelBaselineY = axisY + MARKER_FIRST_LANE_DROP + marker.lane * MARKER_LANE_HEIGHT;
          const showLeader = marker.lane > 0;
          return (
            <g key={marker.testId} data-testid={marker.testId}>
              {showLeader && (
                <line
                  data-testid={`${marker.testId}-leader`}
                  x1={marker.x}
                  y1={triangleBaseY}
                  x2={marker.x}
                  y2={labelBaselineY - MARKER_LABEL_FONT_SIZE}
                  stroke={marker.color}
                  strokeWidth={1}
                  opacity={0.4}
                />
              )}
              <polygon
                points={`${marker.x},${axisY + 2} ${marker.x - 5},${triangleBaseY} ${marker.x + 5},${triangleBaseY}`}
                fill={marker.color}
                opacity={0.85}
              />
              <text
                x={marker.x}
                y={labelBaselineY}
                textAnchor="middle"
                fontFamily="var(--font-mono)"
                fontSize={MARKER_LABEL_FONT_SIZE}
                fill={marker.color}
                letterSpacing="0.06em"
              >
                {marker.label}
              </text>
            </g>
          );
        })}

        {hoverX != null && hoverPoint && (
          <g>
            <line
              x1={hoverX}
              y1={PADDING_TOP}
              x2={hoverX}
              y2={VIEWBOX_HEIGHT - PADDING_BOTTOM}
              stroke="var(--signal-strong)"
              strokeWidth={1}
              opacity={0.45}
              strokeDasharray="2 2"
            />
            <circle
              cx={hoverPoint.x}
              cy={hoverPoint.y}
              r={3}
              fill="var(--signal-strong)"
              stroke="var(--bg-panel-raised)"
              strokeWidth={1}
            />
          </g>
        )}
      </svg>
    </div>
  );
}
