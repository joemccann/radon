/** SVG chart primitives for Satori inline SVG rendering.
 *  Returns React elements (JSX) that Satori renders natively. */

import { OG, ogHeatmapColor, ogSeriesFill } from "./og-theme";

function axisFontSize(): number {
  return OG.chart.axisFontSize;
}

function axisFontFamily(): string {
  return OG.chart.axisFontFamily;
}

export function linearScale(
  domain: [number, number],
  range: [number, number]
): (v: number) => number {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0 || 1;
  return (v: number) => r0 + ((v - d0) / span) * (r1 - r0);
}

export function bandScale(
  domain: string[],
  range: [number, number],
  padding = 0.2
): { scale: (label: string) => number; bandwidth: number } {
  const [r0, r1] = range;
  const n = domain.length;
  const total = r1 - r0;
  const step = total / n;
  const bandwidth = step * (1 - padding);
  const offset = (step - bandwidth) / 2;
  const map = new Map(domain.map((d, i) => [d, r0 + i * step + offset]));
  return {
    scale: (label: string) => map.get(label) ?? r0,
    bandwidth,
  };
}

type Point = { x: number; y: number };

export type LineChartProps = {
  data: { label: string; value: number }[];
  width: number;
  height: number;
  color?: string;
  marginTop?: number;
  marginRight?: number;
  marginBottom?: number;
  marginLeft?: number;
  showDots?: boolean;
  showGrid?: boolean;
};

export function lineChartSvg({
  data,
  width,
  height,
  color = OG.positive,
  marginTop = 20,
  marginRight = 10,
  marginBottom = 30,
  marginLeft = 50,
  showDots = true,
  showGrid = true,
}: LineChartProps) {
  if (data.length === 0) return null;

  const innerW = width - marginLeft - marginRight;
  const innerH = height - marginTop - marginBottom;
  const values = data.map((d) => d.value);
  const yMin = Math.min(...values);
  const yMax = Math.max(...values);
  const yPad = (yMax - yMin) * 0.1 || 1;
  const yScale = linearScale(
    [yMin - yPad, yMax + yPad],
    [innerH, 0]
  );
  const xStep = innerW / Math.max(data.length - 1, 1);

  const points: Point[] = data.map((d, i) => ({
    x: marginLeft + i * xStep,
    y: marginTop + yScale(d.value),
  }));

  const pathD = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`)
    .join(" ");

  const yTicks: number[] = [];
  for (let i = 0; i < 5; i++) {
    yTicks.push(yMin - yPad + ((yMax + yPad - (yMin - yPad)) / 4) * i);
  }

  return (
    <svg width={width} height={height}>
      {showGrid &&
        yTicks.map((tick, i) => {
          const y = marginTop + yScale(tick);
          return (
            <g key={`grid-${i}`}>
              <line
                x1={marginLeft}
                y1={y}
                x2={width - marginRight}
                y2={y}
                stroke={OG.chart.grid}
                strokeWidth={1}
              />
              <text
                x={marginLeft - 6}
                y={y + 3}
                fill={OG.chart.axisLabel}
                fontSize={axisFontSize()}
                textAnchor="end"
                fontFamily={axisFontFamily()}
              >
                {tick.toFixed(2)}
              </text>
            </g>
          );
        })}

      {data.map((d, i) => {
        const skip = Math.max(1, Math.floor(data.length / 8));
        if (i % skip !== 0 && i !== data.length - 1) return null;
        return (
          <text
            key={`x-${i}`}
            x={points[i].x}
            y={height - 6}
            fill={OG.chart.axisLabel}
            fontSize={axisFontSize()}
            textAnchor="middle"
            fontFamily={axisFontFamily()}
          >
            {d.label}
          </text>
        );
      })}

      <path d={pathD} fill="none" stroke={color} strokeWidth={2} />

      {showDots &&
        points.map((p, i) => (
          <circle key={`dot-${i}`} cx={p.x} cy={p.y} r={3} fill={color} />
        ))}
    </svg>
  );
}

export type BarChartProps = {
  data: { label: string; value: number }[];
  width: number;
  height: number;
  posColor?: string;
  negColor?: string;
  marginTop?: number;
  marginRight?: number;
  marginBottom?: number;
  marginLeft?: number;
};

export function barChartSvg({
  data,
  width,
  height,
  posColor = OG.positive,
  negColor = OG.negative,
  marginTop = 20,
  marginRight = 10,
  marginBottom = 40,
  marginLeft = 50,
}: BarChartProps) {
  if (data.length === 0) return null;

  const innerH = height - marginTop - marginBottom;
  const values = data.map((d) => d.value);
  const yMin = Math.min(0, ...values);
  const yMax = Math.max(0, ...values);
  const yPad = (yMax - yMin) * 0.05 || 1;
  const yScale = linearScale(
    [yMin - yPad, yMax + yPad],
    [innerH, 0]
  );
  const { scale: xScale, bandwidth } = bandScale(
    data.map((d) => d.label),
    [marginLeft, width - marginRight]
  );
  const zeroY = marginTop + yScale(0);

  return (
    <svg width={width} height={height}>
      <line
        x1={marginLeft}
        y1={zeroY}
        x2={width - marginRight}
        y2={zeroY}
        stroke={OG.chart.axis}
        strokeWidth={1}
        strokeDasharray="4,2"
      />

      {data.map((d) => {
        const x = xScale(d.label);
        const barY = d.value >= 0 ? marginTop + yScale(d.value) : zeroY;
        const barH = Math.abs(
          yScale(d.value) - yScale(0)
        );
        return (
          <rect
            key={d.label}
            x={x}
            y={barY}
            width={bandwidth}
            height={Math.max(barH, 1)}
            fill={d.value >= 0 ? posColor : negColor}
          />
        );
      })}

      {data.map((d) => {
        const skip = Math.max(1, Math.floor(data.length / 12));
        const idx = data.indexOf(d);
        if (idx % skip !== 0 && idx !== data.length - 1) return null;
        return (
          <text
            key={`xl-${d.label}`}
            x={xScale(d.label) + bandwidth / 2}
            y={height - 6}
            fill={OG.chart.axisLabel}
            fontSize={axisFontSize()}
            textAnchor="middle"
            fontFamily={axisFontFamily()}
            transform={`rotate(-45, ${xScale(d.label) + bandwidth / 2}, ${height - 6})`}
          >
            {d.label}
          </text>
        );
      })}

      {[yMin, (yMin + yMax) / 2, yMax].map((tick, i) => {
        const y = marginTop + yScale(tick);
        return (
          <text
            key={`yl-${i}`}
            x={marginLeft - 6}
            y={y + 3}
            fill={OG.chart.axisLabel}
            fontSize={axisFontSize()}
            textAnchor="end"
            fontFamily={axisFontFamily()}
          >
            {tick >= 1e9
              ? `${(tick / 1e9).toFixed(1)}B`
              : tick >= 1e6
                ? `${(tick / 1e6).toFixed(1)}M`
                : tick.toFixed(2)}
          </text>
        );
      })}
    </svg>
  );
}

export type AreaChartProps = LineChartProps & {
  fillOpacity?: number;
};

export function areaChartSvg({
  data,
  width,
  height,
  color = OG.info,
  fillOpacity = 0.15,
  marginTop = 20,
  marginRight = 10,
  marginBottom = 30,
  marginLeft = 50,
  showGrid = true,
}: AreaChartProps) {
  if (data.length === 0) return null;

  const innerW = width - marginLeft - marginRight;
  const innerH = height - marginTop - marginBottom;
  const values = data.map((d) => d.value);
  const yMin = Math.min(...values);
  const yMax = Math.max(...values);
  const yPad = (yMax - yMin) * 0.1 || 1;
  const yScale = linearScale(
    [yMin - yPad, yMax + yPad],
    [innerH, 0]
  );
  const xStep = innerW / Math.max(data.length - 1, 1);
  const points: Point[] = data.map((d, i) => ({
    x: marginLeft + i * xStep,
    y: marginTop + yScale(d.value),
  }));

  const lineD = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`)
    .join(" ");

  const areaD =
    lineD +
    ` L ${points[points.length - 1].x} ${marginTop + innerH}` +
    ` L ${points[0].x} ${marginTop + innerH} Z`;

  const yTicks: number[] = [];
  for (let i = 0; i < 5; i++) {
    yTicks.push(yMin - yPad + ((yMax + yPad - (yMin - yPad)) / 4) * i);
  }

  return (
    <svg width={width} height={height}>
      {showGrid &&
        yTicks.map((tick, i) => {
          const y = marginTop + yScale(tick);
          return (
            <g key={`grid-${i}`}>
              <line
                x1={marginLeft}
                y1={y}
                x2={width - marginRight}
                y2={y}
                stroke={OG.chart.grid}
                strokeWidth={1}
              />
              <text
                x={marginLeft - 6}
                y={y + 3}
                fill={OG.chart.axisLabel}
                fontSize={axisFontSize()}
                textAnchor="end"
                fontFamily={axisFontFamily()}
              >
                {tick.toFixed(2)}
              </text>
            </g>
          );
        })}

      {data.map((d, i) => {
        const skip = Math.max(1, Math.floor(data.length / 8));
        if (i % skip !== 0 && i !== data.length - 1) return null;
        return (
          <text
            key={`x-${i}`}
            x={points[i].x}
            y={height - 6}
            fill={OG.chart.axisLabel}
            fontSize={axisFontSize()}
            textAnchor="middle"
            fontFamily={axisFontFamily()}
          >
            {d.label}
          </text>
        );
      })}

      <path d={areaD} fill={ogSeriesFill("primary", fillOpacity)} />
      <path d={lineD} fill="none" stroke={color} strokeWidth={2} />
    </svg>
  );
}

export type HeatmapProps = {
  data: { row: string; col: string; value: number }[];
  rows: string[];
  cols: string[];
  width: number;
  height: number;
  colorScale?: (v: number) => string;
  marginTop?: number;
  marginRight?: number;
  marginBottom?: number;
  marginLeft?: number;
};

export function heatmapSvg({
  data,
  rows,
  cols,
  width,
  height,
  colorScale,
  marginTop = 30,
  marginRight = 10,
  marginBottom = 10,
  marginLeft = 80,
}: HeatmapProps) {
  if (data.length === 0) return null;

  const innerW = width - marginLeft - marginRight;
  const innerH = height - marginTop - marginBottom;
  const cellW = innerW / cols.length;
  const cellH = innerH / rows.length;

  const values = data.map((d) => d.value);
  const vMin = Math.min(...values);
  const vMax = Math.max(...values);

  const defaultColor = (v: number): string => ogHeatmapColor(v, vMin, vMax);
  const color = colorScale ?? defaultColor;
  const lookup = new Map(data.map((d) => [`${d.row}|${d.col}`, d.value]));

  return (
    <svg width={width} height={height}>
      {cols.map((col, ci) => (
        <text
          key={`ch-${ci}`}
          x={marginLeft + ci * cellW + cellW / 2}
          y={marginTop - 8}
          fill={OG.chart.axisLabel}
          fontSize={axisFontSize()}
          textAnchor="middle"
          fontFamily={axisFontFamily()}
        >
          {col}
        </text>
      ))}

      {rows.map((row, ri) => (
        <g key={`row-${ri}`}>
          <text
            x={marginLeft - 6}
            y={marginTop + ri * cellH + cellH / 2 + 3}
            fill={OG.chart.axisLabel}
            fontSize={axisFontSize()}
            textAnchor="end"
            fontFamily={axisFontFamily()}
          >
            {row}
          </text>
          {cols.map((col, ci) => {
            const val = lookup.get(`${row}|${col}`);
            return (
              <rect
                key={`cell-${ri}-${ci}`}
                x={marginLeft + ci * cellW + 1}
                y={marginTop + ri * cellH + 1}
                width={cellW - 2}
                height={cellH - 2}
                fill={val != null ? color(val as number) : OG.panel}
              />
            );
          })}
        </g>
      ))}
    </svg>
  );
}
