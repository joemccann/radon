import { Group } from "@visx/group";
import { scaleBand, scaleLinear } from "@visx/scale";
import { Bar } from "@visx/shape";

import {
  MOVE_AXIS_MAX,
  MOVE_TICKS,
  OCTOBER_HIKE_ODDS,
  PROBABILITY_AXIS,
  RATES_MOVE_SOURCE,
  TEN_YEAR_MOVE,
  YEAR_END_TIGHTENING_BP,
} from "@/lib/ratesMove";

const WIDTH = 720;
const MOVE_HEIGHT = 156;
const ODDS_HEIGHT = 88;
const MOVE_MARGIN = { top: 8, right: 88, bottom: 36, left: 96 };
const ODDS_MARGIN = { top: 28, right: 72, bottom: 28, left: 96 };

type MoveBar = { id: string; label: string; bp: number };

type Props = {
  bars?: readonly MoveBar[];
  axisMax?: number;
  ticks?: readonly number[];
  probability?: { label: string; pct: number } | null;
  readout?: { label: string; bp: number } | null;
  title?: string;
  barAria?: string;
  note?: string | null;
  source?: string | null;
  eyebrow?: string;
};

export function RatesMoveChart({
  bars = TEN_YEAR_MOVE,
  axisMax = MOVE_AXIS_MAX,
  ticks = MOVE_TICKS,
  probability = { label: "October hike", pct: OCTOBER_HIKE_ODDS },
  readout = { label: "Year-end tightening", bp: YEAR_END_TIGHTENING_BP },
  title = "10-year move and the October hike",
  barAria = "10-year change in basis points",
  note = "2-week window sits inside the month. Not additive.",
  source = RATES_MOVE_SOURCE,
  eyebrow = "Rates · 24 Sep 2026",
}: Props) {
  const moveRight = WIDTH - MOVE_MARGIN.right;
  const moveBottom = MOVE_HEIGHT - MOVE_MARGIN.bottom;
  const moveX = scaleLinear<number>({
    domain: [0, axisMax],
    range: [MOVE_MARGIN.left, moveRight],
    zero: true,
  });
  const moveY = scaleBand<string>({
    domain: bars.map((row) => row.id),
    range: [MOVE_MARGIN.top, moveBottom],
    padding: 0.4,
  });

  const oddsRight = WIDTH - ODDS_MARGIN.right;
  const oddsX = scaleLinear<number>({
    domain: [...PROBABILITY_AXIS],
    range: [ODDS_MARGIN.left, oddsRight],
    zero: true,
  });
  const [oddsMin, oddsMax] = oddsX.domain();
  const trackY = 30;
  const trackH = 10;

  return (
    <figure style={frameStyle}>
      <p style={eyebrowStyle}>{eyebrow}</p>
      <figcaption style={titleStyle}>{title}</figcaption>
      {bars.length > 0 ? (
      <svg
        role="img"
        aria-label={barAria}
        viewBox={`0 0 ${WIDTH} ${MOVE_HEIGHT}`}
        width="100%"
        data-axis-min={moveX.domain()[0]}
        data-axis-max={moveX.domain()[1]}
      >
        <title>{barAria}</title>
        {ticks.map((tick) => (
          <Group key={`tick-${tick}`}>
            <line
              x1={moveX(tick)}
              y1={MOVE_MARGIN.top}
              x2={moveX(tick)}
              y2={moveBottom}
              stroke={tick === 0 ? "var(--line-grid)" : "var(--chart-grid)"}
              strokeWidth={1}
            />
            <text
              x={moveX(tick)}
              y={moveBottom + 18}
              fill="var(--text-muted)"
              fontSize={12}
              fontFamily="var(--font-mono)"
              textAnchor="middle"
            >
              {tick}
            </text>
          </Group>
        ))}
        <text
          x={(MOVE_MARGIN.left + moveRight) / 2}
          y={MOVE_HEIGHT - 6}
          fill="var(--text-muted)"
          fontSize={12}
          fontFamily="var(--font-sans)"
          textAnchor="middle"
        >
          Basis points
        </text>
        {bars.map((row) => {
          const y = moveY(row.id) ?? 0;
          return (
            <Group key={row.id} data-mark={row.id}>
              <text
                x={MOVE_MARGIN.left - 12}
                y={y + moveY.bandwidth() / 2 + 4}
                fill="var(--text-primary)"
                fontSize={13}
                fontFamily="var(--font-sans)"
                fontWeight={600}
                textAnchor="end"
              >
                {row.label}
              </text>
              <Bar
                x={moveX(0)}
                y={y}
                width={Math.max(moveX(row.bp) - moveX(0), 1)}
                height={moveY.bandwidth()}
                fill="var(--signal-core)"
              />
              <text
                x={moveRight + 12}
                y={y + moveY.bandwidth() / 2 + 4}
                fill="var(--signal-core)"
                fontSize={12}
                fontFamily="var(--font-mono)"
                fontWeight={500}
              >
                {`+${row.bp} bp`}
              </text>
            </Group>
          );
        })}
      </svg>
      ) : null}
      {note ? <p style={noteStyle}>{note}</p> : null}
      {probability ? (
      <svg
        role="img"
        aria-label={`${probability.label} probability`}
        viewBox={`0 0 ${WIDTH} ${ODDS_HEIGHT}`}
        width="100%"
        data-scale="probability"
        data-axis-min={oddsMin}
        data-axis-max={oddsMax}
      >
        <title>{`${probability.label} probability`}</title>
        <text
          x={ODDS_MARGIN.left}
          y={18}
          fill="var(--text-primary)"
          fontSize={13}
          fontFamily="var(--font-sans)"
          fontWeight={600}
        >
          {probability.label}
        </text>
        <Bar
          x={oddsX(0)}
          y={trackY}
          width={oddsX(100) - oddsX(0)}
          height={trackH}
          fill="var(--line-grid)"
        />
        <Bar
          x={oddsX(0)}
          y={trackY}
          width={oddsX(probability.pct) - oddsX(0)}
          height={trackH}
          fill="var(--signal-core)"
        />
        <text
          x={oddsX(probability.pct) + 8}
          y={trackY + 9}
          fill="var(--signal-core)"
          fontSize={12}
          fontFamily="var(--font-mono)"
          fontWeight={500}
        >
          {`${probability.pct}%`}
        </text>
        <text
          x={oddsX(0)}
          y={ODDS_HEIGHT - 8}
          fill="var(--text-muted)"
          fontSize={12}
          fontFamily="var(--font-mono)"
          textAnchor="middle"
        >
          0
        </text>
        <text
          x={oddsX(100)}
          y={ODDS_HEIGHT - 8}
          fill="var(--text-muted)"
          fontSize={12}
          fontFamily="var(--font-mono)"
          textAnchor="middle"
        >
          100
        </text>
      </svg>
      ) : null}
      {readout ? (
        <p style={tighteningStyle} data-unit="bp">
          {readout.label}
          <span style={tighteningValueStyle}>{`+${readout.bp} bp`}</span>
        </p>
      ) : null}
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
  margin: "6px 0 8px",
  color: "var(--text-primary)",
  fontFamily: "var(--font-sans)",
  fontSize: 18,
  fontWeight: 600,
  letterSpacing: "0.01em",
  lineHeight: 1.2,
};

const noteStyle: React.CSSProperties = {
  margin: "4px 0 8px",
  color: "var(--text-secondary)",
  fontFamily: "var(--font-sans)",
  fontSize: 12,
  lineHeight: 1.4,
};

const tighteningStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  margin: "4px 0 0",
  color: "var(--text-primary)",
  fontFamily: "var(--font-sans)",
  fontSize: 13,
  fontWeight: 600,
};

const tighteningValueStyle: React.CSSProperties = {
  color: "var(--text-primary)",
  fontFamily: "var(--font-mono)",
  fontWeight: 500,
};

const sourceStyle: React.CSSProperties = {
  margin: "8px 0 0",
  color: "var(--text-muted)",
  fontFamily: "var(--font-sans)",
  fontSize: 12,
  lineHeight: 1.4,
};
