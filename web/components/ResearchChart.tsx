import type { CSSProperties } from "react";
import { Group } from "@visx/group";
import { scaleBand, scaleLinear } from "@visx/scale";
import { Bar, Circle, Line } from "@visx/shape";

import type { ChartPlan } from "@/lib/planCharts";

const WIDTH = 720;

function formatValue(value: number, unit: string): string {
  if (unit === "bp") return `${value > 0 ? "+" : ""}${value} bp`;
  if (unit === "z") return `${value}z`;
  if (unit === "%") return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(2);
  const text = String(round(value));
  return text;
}

function round(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function RangeFigure({ plan }: { plan: Extract<ChartPlan, { kind: "range" }> }) {
  const height = 36 + plan.marks.length * 56 + 28;
  const margin = { top: 16, right: 108, bottom: 36, left: 168 };
  const plotRight = WIDTH - margin.right;
  const plotBottom = height - margin.bottom;
  const xScale = scaleLinear<number>({ domain: [...plan.axis], range: [margin.left, plotRight] });
  const yScale = scaleBand<string>({
    domain: plan.marks.map((mark) => mark.id),
    range: [margin.top, plotBottom],
    padding: 0.42,
  });
  return (
    <svg role="img" aria-label={plan.title} viewBox={`0 0 ${WIDTH} ${height}`} width="100%" data-axis-min={plan.axis[0]} data-axis-max={plan.axis[1]} data-unit={plan.unit}>
      <title>{plan.title}</title>
      {plan.ticks.map((tick) => (
        <Group key={`tick-${tick}`}>
          <Line
            from={{ x: xScale(tick), y: margin.top }}
            to={{ x: xScale(tick), y: plotBottom }}
            stroke={tick === 0 ? "var(--line-grid)" : "var(--chart-grid)"}
            strokeWidth={1}
          />
          <text x={xScale(tick)} y={plotBottom + 18} fill="var(--text-muted)" fontSize={12} fontFamily="var(--font-mono)" textAnchor="middle">{tick}</text>
        </Group>
      ))}
      {plan.reference ? (
        <Group>
          <Line
            from={{ x: xScale(plan.reference.value), y: margin.top }}
            to={{ x: xScale(plan.reference.value), y: plotBottom }}
            stroke="var(--text-muted)"
            strokeWidth={1}
            strokeDasharray="3 3"
          />
          <text x={xScale(plan.reference.value)} y={12} fill="var(--text-secondary)" fontSize={12} fontFamily="var(--font-mono)" textAnchor="middle">{plan.reference.label}</text>
        </Group>
      ) : null}
      <text x={(margin.left + plotRight) / 2} y={height - 8} fill="var(--text-muted)" fontSize={12} fontFamily="var(--font-sans)" textAnchor="middle">{plan.unit === "%" ? "Yield %" : plan.unit}</text>
      {plan.marks.map((mark) => {
        const cy = (yScale(mark.id) ?? 0) + yScale.bandwidth() / 2;
        const color = mark.estimated ? "var(--warn)" : mark.low === mark.high ? "var(--neutral)" : "var(--signal-core)";
        const valueText = mark.estimated ? `low-mid ${mark.low}%` : mark.low === mark.high ? formatValue(mark.low, "%") : `${formatValue(mark.low, "%")}-${formatValue(mark.high, "%")}`;
        return (
          <Group key={mark.id} data-mark={mark.id} data-estimated={mark.estimated ? "true" : "false"}>
            <text x={margin.left - 12} y={cy - 6} fill="var(--text-primary)" fontSize={13} fontFamily="var(--font-sans)" fontWeight={600} textAnchor="end">{mark.label}</text>
            {mark.detail ? <text x={margin.left - 12} y={cy + 12} fill="var(--text-muted)" fontSize={12} fontFamily="var(--font-mono)" textAnchor="end">{mark.detail}</text> : null}
            {mark.low !== mark.high ? (
              <Line
                from={{ x: xScale(mark.low), y: cy }}
                to={{ x: xScale(mark.high), y: cy }}
                stroke={color}
                strokeWidth={mark.estimated ? 3 : 2}
                strokeDasharray={mark.estimated ? "0 6" : undefined}
                strokeLinecap="round"
              />
            ) : null}
            <Circle cx={xScale(mark.low)} cy={cy} r={5} fill={mark.estimated ? "var(--bg-panel)" : color} stroke={mark.estimated ? color : "none"} strokeWidth={2} />
            {mark.low !== mark.high ? <Circle cx={xScale(mark.high)} cy={cy} r={5} fill={mark.estimated ? "var(--bg-panel)" : color} stroke={mark.estimated ? color : "none"} strokeWidth={2} /> : null}
            <text x={plotRight + 12} y={cy + 4} fill={color} fontSize={12} fontFamily="var(--font-mono)" fontWeight={500}>{valueText}</text>
          </Group>
        );
      })}
    </svg>
  );
}

function BarFigure({ plan }: { plan: Extract<ChartPlan, { kind: "bar" }> }) {
  const height = 20 + Math.max(plan.bars.length, 1) * 36 + 40;
  const margin = { top: 8, right: 88, bottom: 44, left: 148 };
  const plotRight = WIDTH - margin.right;
  const plotBottom = height - margin.bottom;
  const xScale = scaleLinear<number>({ domain: [...plan.axis], range: [margin.left, plotRight] });
  const yScale = scaleBand<string>({
    domain: plan.bars.map((row) => row.id),
    range: [margin.top, plotBottom],
    padding: 0.38,
  });
  return (
    <svg role="img" aria-label={plan.title} viewBox={`0 0 ${WIDTH} ${height}`} width="100%" data-axis-min={plan.axis[0]} data-axis-max={plan.axis[1]} data-unit={plan.unit}>
      <title>{plan.title}</title>
      {plan.ticks.map((tick) => (
        <Group key={`tick-${tick}`}>
          <line x1={xScale(tick)} y1={margin.top} x2={xScale(tick)} y2={plotBottom} stroke={tick === 0 ? "var(--line-grid)" : "var(--chart-grid)"} strokeWidth={1} />
          <text x={xScale(tick)} y={plotBottom + 18} fill="var(--text-muted)" fontSize={12} fontFamily="var(--font-mono)" textAnchor="middle">{tick}</text>
        </Group>
      ))}
      <text x={plotRight} y={height - 8} fill="var(--text-muted)" fontSize={12} fontFamily="var(--font-sans)" textAnchor="end">{plan.unit}</text>
      {plan.bars.map((row) => {
        const y = yScale(row.id) ?? 0;
        const x0 = xScale(0);
        const x1 = xScale(row.value);
        return (
          <Group key={row.id} data-mark={row.id}>
            <text x={margin.left - 12} y={y + yScale.bandwidth() / 2 + 4} fill="var(--text-primary)" fontSize={13} fontFamily="var(--font-sans)" fontWeight={600} textAnchor="end">{row.label}</text>
            <Bar x={Math.min(x0, x1)} y={y} width={Math.max(Math.abs(x1 - x0), 1)} height={yScale.bandwidth()} fill={row.value < 0 ? "var(--fault)" : "var(--signal-core)"} />
            <text x={row.value < 0 ? x1 - 8 : x1 + 8} y={y + yScale.bandwidth() / 2 + 4} fill={row.value < 0 ? "var(--fault)" : "var(--signal-core)"} fontSize={12} fontFamily="var(--font-mono)" fontWeight={500} textAnchor={row.value < 0 ? "end" : "start"}>{formatValue(row.value, plan.unit)}</text>
          </Group>
        );
      })}
    </svg>
  );
}

function LineFigure({ plan }: { plan: Extract<ChartPlan, { kind: "line" }> }) {
  const height = 240;
  const margin = { top: 16, right: 24, bottom: 36, left: 56 };
  const plotRight = WIDTH - margin.right;
  const plotBottom = height - margin.bottom;
  const yScale = scaleLinear<number>({ domain: [...plan.axis], range: [plotBottom, margin.top] });
  const xScale = scaleBand<string>({
    domain: plan.points.map((point) => point.id),
    range: [margin.left, plotRight],
    padding: 0.4,
  });
  const coords = plan.points.map((point) => ({
    point,
    x: (xScale(point.id) ?? 0) + xScale.bandwidth() / 2,
    y: yScale(point.value),
  }));
  const path = coords.map((coord, index) => `${index === 0 ? "M" : "L"}${coord.x} ${coord.y}`).join(" ");
  return (
    <svg role="img" aria-label={plan.title} viewBox={`0 0 ${WIDTH} ${height}`} width="100%" data-axis-min={plan.axis[0]} data-axis-max={plan.axis[1]} data-unit={plan.unit}>
      <title>{plan.title}</title>
      {plan.ticks.map((tick) => (
        <Group key={`tick-${tick}`}>
          <Line from={{ x: margin.left, y: yScale(tick) }} to={{ x: plotRight, y: yScale(tick) }} stroke={tick === 0 ? "var(--line-grid)" : "var(--chart-grid)"} strokeWidth={1} />
          <text x={margin.left - 8} y={yScale(tick) + 4} fill="var(--text-muted)" fontSize={12} fontFamily="var(--font-mono)" textAnchor="end">{tick}</text>
        </Group>
      ))}
      <path d={path} fill="none" stroke="var(--signal-core)" strokeWidth={2} />
      {coords.map(({ point, x, y }) => (
        <Group key={point.id} data-mark={point.id}>
          <Circle cx={x} cy={y} r={5} fill="var(--signal-core)" />
          <text x={x} y={y - 12} fill="var(--signal-core)" fontSize={12} fontFamily="var(--font-mono)" textAnchor="middle">{formatValue(point.value, plan.unit)}</text>
          <text x={x} y={plotBottom + 18} fill="var(--text-primary)" fontSize={12} fontFamily="var(--font-sans)" textAnchor="middle">{point.label}</text>
        </Group>
      ))}
    </svg>
  );
}

function ScatterFigure({ plan }: { plan: Extract<ChartPlan, { kind: "scatter" }> }) {
  const height = 280;
  const margin = { top: 16, right: 24, bottom: 40, left: 56 };
  const plotRight = WIDTH - margin.right;
  const plotBottom = height - margin.bottom;
  const xScale = scaleLinear<number>({ domain: [...plan.xAxis], range: [margin.left, plotRight] });
  const yScale = scaleLinear<number>({ domain: [...plan.yAxis], range: [plotBottom, margin.top] });
  return (
    <svg role="img" aria-label={plan.title} viewBox={`0 0 ${WIDTH} ${height}`} width="100%" data-axis-min={plan.xAxis[0]} data-axis-max={plan.xAxis[1]} data-unit={plan.xUnit}>
      <title>{plan.title}</title>
      {plan.xTicks.map((tick) => (
        <Line key={`x-${tick}`} from={{ x: xScale(tick), y: margin.top }} to={{ x: xScale(tick), y: plotBottom }} stroke={tick === 0 ? "var(--line-grid)" : "var(--chart-grid)"} strokeWidth={1} />
      ))}
      {plan.yTicks.map((tick) => (
        <Group key={`y-${tick}`}>
          <Line from={{ x: margin.left, y: yScale(tick) }} to={{ x: plotRight, y: yScale(tick) }} stroke={tick === 0 ? "var(--line-grid)" : "var(--chart-grid)"} strokeWidth={1} />
          <text x={margin.left - 8} y={yScale(tick) + 4} fill="var(--text-muted)" fontSize={12} fontFamily="var(--font-mono)" textAnchor="end">{tick}</text>
        </Group>
      ))}
      {plan.xTicks.map((tick) => (
        <text key={`xl-${tick}`} x={xScale(tick)} y={plotBottom + 18} fill="var(--text-muted)" fontSize={12} fontFamily="var(--font-mono)" textAnchor="middle">{tick}</text>
      ))}
      <text x={(margin.left + plotRight) / 2} y={height - 6} fill="var(--text-muted)" fontSize={12} fontFamily="var(--font-sans)" textAnchor="middle">{plan.xUnit}</text>
      {plan.points.map((point) => (
        <Group key={point.id} data-mark={point.id}>
          <Circle cx={xScale(point.x)} cy={yScale(point.y)} r={5} fill="var(--signal-core)" />
          <text x={xScale(point.x) + 8} y={yScale(point.y) - 8} fill="var(--text-primary)" fontSize={12} fontFamily="var(--font-sans)">{point.label}</text>
        </Group>
      ))}
    </svg>
  );
}

export function ResearchChart({ plan }: { plan: ChartPlan }) {
  return (
    <figure data-kind={plan.kind} style={frameStyle}>
      <figcaption style={titleStyle}>{plan.title}</figcaption>
      {plan.kind === "range" ? <RangeFigure plan={plan} /> : null}
      {plan.kind === "bar" ? <BarFigure plan={plan} /> : null}
      {plan.kind === "line" ? <LineFigure plan={plan} /> : null}
      {plan.kind === "scatter" ? <ScatterFigure plan={plan} /> : null}
      {plan.kind === "bar" && plan.note ? <p style={noteStyle}>{plan.note}</p> : null}
      {plan.kind === "range" && plan.sourceNote ? <p style={noteStyle}>{plan.sourceNote}</p> : null}
    </figure>
  );
}

const frameStyle: CSSProperties = {
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

const titleStyle: CSSProperties = {
  margin: "0 0 8px",
  color: "var(--text-primary)",
  fontFamily: "var(--font-sans)",
  fontSize: 18,
  fontWeight: 600,
  letterSpacing: "0.01em",
  lineHeight: 1.2,
};

const noteStyle: CSSProperties = {
  margin: "8px 0 0",
  color: "var(--text-muted)",
  fontFamily: "var(--font-sans)",
  fontSize: 12,
  lineHeight: 1.4,
};
