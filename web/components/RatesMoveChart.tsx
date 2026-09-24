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

export function RatesMoveChart() {
  const moveRight = WIDTH - MOVE_MARGIN.right;
  const moveBottom = MOVE_HEIGHT - MOVE_MARGIN.bottom;
  const moveX = scaleLinear<number>({
    domain: [0, MOVE_AXIS_MAX],
    range: [MOVE_MARGIN.left, moveRight],
    zero: true,
  });
  const moveY = scaleBand<string>({
    domain: TEN_YEAR_MOVE.map((row) => row.id),
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
      <p style={eyebrowStyle}>Rates · 24 Sep 2026</p>
      <figcaption style={titleStyle}>10-year move and the October hike</figcaption>
      <svg
        role="img"
        aria-label="10-year change in basis points"
        viewBox={`0 0 ${WIDTH} ${MOVE_HEIGHT}`}
        width="100%"
        data-axis-min={moveX.domain()[0]}
        data-axis-max={moveX.domain()[1]}
      >
        <title>10-year change in basis points</title>
        {MOVE_TICKS.map((tick) => (
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
        {TEN_YEAR_MOVE.map((row) => {
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
      <p style={noteStyle}>2-week window sits inside the month. Not additive.</p>
      <svg
        role="img"
        aria-label="October hike probability"
        viewBox={`0 0 ${WIDTH} ${ODDS_HEIGHT}`}
        width="100%"
        data-scale="probability"
        data-axis-min={oddsMin}
        data-axis-max={oddsMax}
      >
        <title>October hike probability</title>
        <text
          x={ODDS_MARGIN.left}
          y={18}
          fill="var(--text-primary)"
          fontSize={13}
          fontFamily="var(--font-sans)"
          fontWeight={600}
        >
          October hike
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
          width={oddsX(OCTOBER_HIKE_ODDS) - oddsX(0)}
          height={trackH}
          fill="var(--signal-core)"
        />
        <text
          x={oddsX(OCTOBER_HIKE_ODDS) + 8}
          y={trackY + 9}
          fill="var(--signal-core)"
          fontSize={12}
          fontFamily="var(--font-mono)"
          fontWeight={500}
        >
          {`${OCTOBER_HIKE_ODDS}%`}
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
      <p style={tighteningStyle} data-unit="bp">
        Year-end tightening
        <span style={tighteningValueStyle}>{`+${YEAR_END_TIGHTENING_BP} bp`}</span>
      </p>
      <p style={sourceStyle}>{RATES_MOVE_SOURCE}</p>
    </figure>
  );
}

const frameStyle: React.CSSProperties = {
  boxSizing: "border-box",
  width: 760,
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
