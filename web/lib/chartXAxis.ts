const DEFAULT_MINIMUM_TICK_SPACING = 110;
const DEFAULT_MAX_TICKS = 7;

export function chartXAxisTickAnchor(index: number, tickCount: number): "start" | "middle" | "end" {
  if (index === 0) return "start";
  if (index === tickCount - 1) return "end";
  return "middle";
}

export function resolveChartXAxisTickCount(
  length: number,
  innerWidth: number,
  minimumTickSpacing = DEFAULT_MINIMUM_TICK_SPACING,
  maximumTicks = DEFAULT_MAX_TICKS,
): number {
  if (length <= 0 || innerWidth <= 0) return 0;
  if (length === 1) return 1;
  return Math.max(
    2,
    Math.min(length, maximumTicks, Math.floor(innerWidth / minimumTickSpacing) + 1),
  );
}

export function buildChartXAxisTickIndices(
  length: number,
  innerWidth: number,
  minimumTickSpacing = DEFAULT_MINIMUM_TICK_SPACING,
  maximumTicks = DEFAULT_MAX_TICKS,
): number[] {
  const count = resolveChartXAxisTickCount(length, innerWidth, minimumTickSpacing, maximumTicks);
  if (count <= 0) return [];
  if (count === 1) return [0];

  const step = (length - 1) / (count - 1);
  const indices = new Set<number>([0, length - 1]);
  for (let index = 0; index < count; index += 1) indices.add(Math.round(index * step));
  return [...indices].sort((a, b) => a - b);
}

export function buildTimeXAxisTickValues(
  dates: Date[],
  innerWidth: number,
  minimumTickSpacing = DEFAULT_MINIMUM_TICK_SPACING,
): Date[] {
  const uniqueDates = [...new Map(
    dates
      .filter((date) => Number.isFinite(date.getTime()))
      .map((date) => [date.getTime(), date] as const),
  ).values()].sort((a, b) => a.getTime() - b.getTime());
  if (uniqueDates.length <= 1) return uniqueDates;

  const first = uniqueDates[0];
  const last = uniqueDates[uniqueDates.length - 1];
  const span = last.getTime() - first.getTime();
  if (span <= 0 || innerWidth <= 0) return [first];

  // Tick labels are positioned on elapsed time, not row index. Market-session
  // gaps make evenly sampled rows bunch together in pixels, so enforce the
  // rendered distance directly while keeping the two endpoints visible.
  const spacing = Math.max(minimumTickSpacing, innerWidth / (DEFAULT_MAX_TICKS - 1));
  const x = (date: Date) => ((date.getTime() - first.getTime()) / span) * innerWidth;
  const selected = [first];

  for (const date of uniqueDates.slice(1, -1)) {
    if (x(date) - x(selected[selected.length - 1]) >= spacing) selected.push(date);
  }

  if (x(last) - x(selected[selected.length - 1]) < spacing && selected.length > 1) {
    selected[selected.length - 1] = last;
  } else {
    selected.push(last);
  }

  return selected;
}
