import type { PerformanceData } from "./types";

export type ChartMargins = {
  top: number;
  right: number;
  bottom: number;
  left: number;
};

export type PerformanceAxisTick = {
  label: string;
};

export type PerformanceYAxisTick = PerformanceAxisTick & {
  value: number;
  y: number;
};

export type PerformanceXAxisTick = PerformanceAxisTick & {
  x: number;
  index: number;
};

export type PerformanceChartModel = {
  equityPath: string;
  benchmarkPath: string;
  areaPath: string;
  latestEquity: number;
  latestBenchmark: number;
  rebasedBenchmarkValues: number[];
  domainMin: number;
  domainMax: number;
  yAxisTicks: PerformanceYAxisTick[];
  xAxisTicks: PerformanceXAxisTick[];
  plotBottom: number;
  plotLeft: number;
  plotRight: number;
};

export const DEFAULT_PERFORMANCE_CHART_WIDTH = 820;
export const DEFAULT_PERFORMANCE_CHART_HEIGHT = 320;
export const DEFAULT_PERFORMANCE_CHART_MARGINS: ChartMargins = {
  top: 18,
  right: 24,
  bottom: 40,
  left: 72,
};

const axisDateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

function formatAxisDate(value: string): string {
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? value : axisDateFormatter.format(parsed);
}

function formatAxisUsd(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) {
    return `${value < 0 ? "-" : ""}$${(abs / 1_000_000).toFixed(2)}M`;
  }
  return `${value < 0 ? "-" : ""}$${abs.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })}`;
}

function buildLinePath(
  values: number[],
  width: number,
  height: number,
  margins: ChartMargins,
  domainMin: number,
  domainMax: number,
): string {
  if (values.length === 0) return "";
  const innerWidth = width - margins.left - margins.right;
  const top = margins.top;
  const bottom = height - margins.bottom;
  const span = domainMax - domainMin || 1;
  return values
    .map((value, index) => {
      const x = margins.left + (index / Math.max(values.length - 1, 1)) * innerWidth;
      const y = bottom - ((value - domainMin) / span) * (bottom - top);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function buildAreaPath(
  values: number[],
  width: number,
  height: number,
  margins: ChartMargins,
  domainMin: number,
  domainMax: number,
): string {
  if (values.length === 0) return "";
  const line = buildLinePath(values, width, height, margins, domainMin, domainMax);
  const baselineY = height - margins.bottom;
  const endX = width - margins.right;
  const startX = margins.left;
  return `${line} L ${endX} ${baselineY} L ${startX} ${baselineY} Z`;
}

function buildNiceTicks(values: number[], desiredTickCount: number) {
  if (values.length === 0) {
    return { domainMin: 0, domainMax: 100, ticks: [0, 25, 50, 75] };
  }

  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const rawSpan = rawMax - rawMin || Math.max(Math.abs(rawMax), 1);
  const roughStep = rawSpan / Math.max(desiredTickCount - 1, 1);
  const magnitude = 10 ** Math.floor(Math.log10(roughStep || 1));
  const normalized = roughStep / magnitude;
  const multiplier =
    normalized <= 1 ? 1
    : normalized <= 2 ? 2
    : normalized <= 2.5 ? 2.5
    : normalized <= 5 ? 5
    : 10;
  const step = multiplier * magnitude;
  const domainMin = Math.floor(rawMin / step) * step;
  const domainMax = Math.ceil(rawMax / step) * step;
  const ticks: number[] = [];

  for (let value = domainMin; value <= domainMax + step / 2; value += step) {
    ticks.push(Number(value.toFixed(6)));
  }

  return {
    domainMin,
    domainMax,
    ticks:
      ticks.length <= desiredTickCount
        ? ticks
        : Array.from({ length: desiredTickCount }, (_, index) =>
            ticks[Math.round((index * (ticks.length - 1)) / Math.max(desiredTickCount - 1, 1))],
          ),
  };
}

function buildIndexTicks(length: number, desiredTickCount: number): number[] {
  if (length <= 1) return [0];
  const tickCount = Math.min(length, desiredTickCount);
  const indices = new Set<number>();

  for (let index = 0; index < tickCount; index += 1) {
    const ratio = tickCount === 1 ? 0 : index / (tickCount - 1);
    indices.add(Math.round(ratio * (length - 1)));
  }

  return [...indices].sort((a, b) => a - b);
}

export function buildPerformanceChartModel(
  data: PerformanceData,
  width = DEFAULT_PERFORMANCE_CHART_WIDTH,
  height = DEFAULT_PERFORMANCE_CHART_HEIGHT,
  margins: ChartMargins = DEFAULT_PERFORMANCE_CHART_MARGINS,
): PerformanceChartModel {
  const startEquity = data.summary.starting_equity;
  const startBenchmark = data.series[0]?.benchmark_close ?? 1;
  const equityValues = data.series.map((point) => point.equity);
  const rebasedBenchmarkValues = data.series.map((point) => (point.benchmark_close / startBenchmark) * startEquity);
  const { domainMin, domainMax, ticks } = buildNiceTicks([...equityValues, ...rebasedBenchmarkValues], 4);
  const plotBottom = height - margins.bottom;
  const plotLeft = margins.left;
  const plotRight = width - margins.right;
  const span = domainMax - domainMin || 1;
  const yAxisTicks = ticks.map((tick) => ({
    value: tick,
    label: formatAxisUsd(tick),
    y: plotBottom - ((tick - domainMin) / span) * (plotBottom - margins.top),
  }));
  const xAxisTicks = buildIndexTicks(data.series.length, 4).map((index) => ({
    index,
    label: formatAxisDate(data.series[index]?.date ?? ""),
    x: plotLeft + (index / Math.max(data.series.length - 1, 1)) * (plotRight - plotLeft),
  }));

  return {
    equityPath: buildLinePath(equityValues, width, height, margins, domainMin, domainMax),
    benchmarkPath: buildLinePath(rebasedBenchmarkValues, width, height, margins, domainMin, domainMax),
    areaPath: buildAreaPath(equityValues, width, height, margins, domainMin, domainMax),
    latestEquity: equityValues[equityValues.length - 1] ?? startEquity,
    latestBenchmark: rebasedBenchmarkValues[rebasedBenchmarkValues.length - 1] ?? startEquity,
    rebasedBenchmarkValues,
    domainMin,
    domainMax,
    yAxisTicks,
    xAxisTicks,
    plotBottom,
    plotLeft,
    plotRight,
  };
}
