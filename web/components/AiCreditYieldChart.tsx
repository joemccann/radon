import { Group } from "@visx/group";
import { scaleBand, scaleLinear } from "@visx/scale";
import { Circle, Line } from "@visx/shape";

import {
  AI_CREDIT_DEK,
  AI_CREDIT_SOURCE,
  AI_CREDIT_YIELDS,
  NEAR_TEN_BID,
  YIELD_AXIS_DOMAIN,
  YIELD_TICKS,
  valueLabel,
  type YieldMark,
} from "@/lib/aiCreditYields";

const WIDTH = 720;
const HEIGHT = 380;
const MARGIN = { top: 28, right: 124, bottom: 46, left: 168 };

const SERIES_COLOR: Record<YieldMark["kind"], string> = {
  point: "var(--neutral)",
  "printed-range": "var(--signal-core)",
  "desk-band": "var(--warn)",
};

type Props = {
  marks?: readonly YieldMark[];
  title?: string;
  ariaLabel?: string;
  dek?: string | null;
  axis?: readonly [number, number];
  ticks?: readonly number[];
  reference?: { value: number; label: string } | null;
  source?: string | null;
  eyebrow?: string;
};

export function AiCreditYieldChart({
  marks = AI_CREDIT_YIELDS,
  title = "AI credit dollar yields",
  ariaLabel = "AI credit dollar yields versus Treasuries",
  dek = AI_CREDIT_DEK,
  axis = YIELD_AXIS_DOMAIN,
  ticks = YIELD_TICKS,
  reference = { value: NEAR_TEN_BID, label: "near 10%" },
  source = AI_CREDIT_SOURCE,
  eyebrow = "Credit · Rates · 24 Sep 2026",
}: Props) {
  const plotRight = WIDTH - MARGIN.right;
  const plotBottom = HEIGHT - MARGIN.bottom;
  const xScale = scaleLinear<number>({
    domain: [...axis],
    range: [MARGIN.left, plotRight],
    zero: true,
  });
  const yScale = scaleBand<string>({
    domain: marks.map((mark) => mark.id),
    range: [MARGIN.top, plotBottom],
    padding: 0.42,
  });
  const [axisMin, axisMax] = xScale.domain();

  return (
    <figure style={frameStyle}>
      <p style={eyebrowStyle}>{eyebrow}</p>
      <figcaption style={titleStyle}>{title}</figcaption>
      {dek ? <p style={dekStyle}>{dek}</p> : null}
      <svg
        role="img"
        aria-label={ariaLabel}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        width="100%"
        data-axis-min={axisMin}
        data-axis-max={axisMax}
      >
        <title>{ariaLabel}</title>
        {ticks.map((tick) => (
          <Group key={`tick-${tick}`}>
            {tick !== reference?.value && (
              <Line
                from={{ x: xScale(tick), y: MARGIN.top }}
                to={{ x: xScale(tick), y: plotBottom }}
                stroke={tick === 0 ? "var(--line-grid)" : "var(--chart-grid)"}
                strokeWidth={1}
              />
            )}
            <text
              x={xScale(tick)}
              y={plotBottom + 18}
              fill="var(--text-muted)"
              fontSize={12}
              fontFamily="var(--font-mono)"
              textAnchor="middle"
            >
              {tick}
            </text>
          </Group>
        ))}
        {reference ? (
          <Group>
            <Line
              from={{ x: xScale(reference.value), y: MARGIN.top }}
              to={{ x: xScale(reference.value), y: plotBottom }}
              stroke="var(--text-muted)"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            <text
              x={xScale(reference.value)}
              y={16}
              fill="var(--text-secondary)"
              fontSize={12}
              fontFamily="var(--font-mono)"
              textAnchor="middle"
            >
              {reference.label}
            </text>
          </Group>
        ) : null}
        <text
          x={(MARGIN.left + plotRight) / 2}
          y={HEIGHT - 8}
          fill="var(--text-muted)"
          fontSize={12}
          fontFamily="var(--font-sans)"
          textAnchor="middle"
        >
          Yield %
        </text>
        {marks.map((mark) => {
          const cy = (yScale(mark.id) ?? 0) + yScale.bandwidth() / 2;
          const color = SERIES_COLOR[mark.kind];
          const estimated = mark.kind === "desk-band";
          return (
            <Group key={mark.id} data-mark={mark.id} data-estimated={estimated ? "true" : "false"}>
              <text
                x={MARGIN.left - 12}
                y={cy - 6}
                fill="var(--text-primary)"
                fontSize={13}
                fontFamily="var(--font-sans)"
                fontWeight={600}
                textAnchor="end"
              >
                {mark.label}
              </text>
              <text
                x={MARGIN.left - 12}
                y={cy + 12}
                fill="var(--text-muted)"
                fontSize={12}
                fontFamily="var(--font-mono)"
                textAnchor="end"
              >
                {mark.detail}
              </text>
              {mark.kind !== "point" && (
                <Line
                  from={{ x: xScale(mark.low), y: cy }}
                  to={{ x: xScale(mark.high), y: cy }}
                  stroke={color}
                  strokeWidth={estimated ? 3 : 2}
                  strokeDasharray={estimated ? "0 6" : undefined}
                  strokeLinecap="round"
                />
              )}
              <Circle
                cx={xScale(mark.low)}
                cy={cy}
                r={5}
                fill={estimated ? "var(--bg-panel)" : color}
                stroke={estimated ? color : "none"}
                strokeWidth={2}
              />
              {mark.kind !== "point" && (
                <Circle
                  cx={xScale(mark.high)}
                  cy={cy}
                  r={5}
                  fill={estimated ? "var(--bg-panel)" : color}
                  stroke={estimated ? color : "none"}
                  strokeWidth={2}
                />
              )}
              <text
                x={plotRight + 12}
                y={cy + 4}
                fill={color}
                fontSize={12}
                fontFamily="var(--font-mono)"
                fontWeight={500}
                textAnchor="start"
              >
                {valueLabel(mark)}
              </text>
            </Group>
          );
        })}
      </svg>
      {source ? <p style={sourceStyle}>{source}</p> : null}
    </figure>
  );
}

const frameStyle: React.CSSProperties = {
  boxSizing: "border-box",
  width: "100%",
  maxWidth: 760,
  margin: 0,
  padding: "16px 20px 14px",
  background: "var(--bg-panel)",
  color: "var(--text-primary)",
  border: "1px solid var(--line-grid)",
  borderLeft: "2px solid var(--signal-core)",
  borderRadius: 4,
  fontFamily: "var(--font-sans)",
};

const eyebrowStyle: React.CSSProperties = {
  margin: 0,
  color: "var(--text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: 12,
  fontWeight: 500,
  letterSpacing: "0.04em",
  textTransform: "uppercase",
};

const titleStyle: React.CSSProperties = {
  margin: "6px 0 0",
  color: "var(--text-primary)",
  fontFamily: "var(--font-sans)",
  fontSize: 18,
  fontWeight: 600,
  letterSpacing: "0.01em",
  lineHeight: 1.2,
};

const dekStyle: React.CSSProperties = {
  margin: "6px 0 8px",
  color: "var(--text-secondary)",
  fontFamily: "var(--font-sans)",
  fontSize: 13,
  lineHeight: 1.4,
};

const sourceStyle: React.CSSProperties = {
  margin: "4px 0 0",
  color: "var(--text-muted)",
  fontFamily: "var(--font-sans)",
  fontSize: 12,
  lineHeight: 1.4,
};
