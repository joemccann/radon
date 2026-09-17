import type { AiHistoryPoint } from "./aiInfrastructure";
import { RANGE_PRESETS, type RangePresetSlug } from "./historyRange";

const MONTHS: Record<Exclude<RangePresetSlug, "all">, number> = { "1m": 1, "3m": 3, "6m": 6, "1y": 12, "3y": 36, "5y": 60 };

/** A chart represents one publisher, methodology/cohort series and unit only. */
export function comparableAiHistory(points: AiHistoryPoint[]): AiHistoryPoint[] {
  const first = points[0];
  if (!first || points.some(p => p.source_id !== first.source_id || p.series_id !== first.series_id || p.unit !== first.unit)) return [];
  const byDate = new Map<string, AiHistoryPoint>();
  for (const point of points) if (Number.isFinite(point.value) && Number.isFinite(Date.parse(point.date))) byDate.set(point.date, point);
  return [...byDate.values()].sort((a, b) => Date.parse(a.date) - Date.parse(b.date));
}

/** Calendar windows, never the trading-session counts used for exchange series. */
export function aiCalendarRange(points: AiHistoryPoint[], preset: RangePresetSlug): [number, number] {
  const end = Math.max(0, points.length - 1);
  if (!points.length || preset === "all") return [0, end];
  const last = new Date(points[end].date);
  const boundary = new Date(last);
  const day = last.getUTCDate();
  boundary.setUTCDate(1);
  boundary.setUTCMonth(boundary.getUTCMonth() - MONTHS[preset]);
  const finalDay = new Date(Date.UTC(boundary.getUTCFullYear(), boundary.getUTCMonth() + 1, 0)).getUTCDate();
  boundary.setUTCDate(Math.min(day, finalDay));
  const first = points.findIndex(p => Date.parse(p.date) >= boundary.getTime());
  return [first < 0 ? end : first, end];
}

export function aiCalendarPresets(points: AiHistoryPoint[]) {
  const seen = new Set<string>();
  return RANGE_PRESETS.filter(preset => {
    if (preset.slug === "all") return true;
    const range = aiCalendarRange(points, preset.slug);
    if (range[0] === 0 || range[1] - range[0] < 1) return false;
    const key = range.join(":");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function observationGaps(points: AiHistoryPoint[]): number[] {
  return points.slice(1).map((p, i) => Date.parse(p.date) - Date.parse(points[i].date)).filter(g => g > 0).sort((a, b) => a - b);
}

export function aiHistoryGapMs(points: AiHistoryPoint[], cadence?: string): number | undefined {
  const days: Record<string, number> = { daily: 1.8, weekly: 10, monthly: 45, quarterly: 120 };
  if (cadence && days[cadence]) return days[cadence] * 86_400_000;
  const gaps = observationGaps(points);
  return gaps.length > 1 ? gaps[Math.floor((gaps.length - 1) / 2)] * 1.8 : undefined;
}

export function aiPeriodicBars(points: AiHistoryPoint[], cadence?: string): boolean {
  const gaps = observationGaps(points);
  return cadence === "quarterly" || (cadence === "filing" && gaps.length > 0 && gaps[Math.floor((gaps.length - 1) / 2)] > 45 * 86_400_000);
}
