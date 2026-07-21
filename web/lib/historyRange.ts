/**
 * Shared range-preset helpers for the regime history charts.
 *
 * The CRI relationship view (`components/RegimeRelationshipView.tsx`)
 * keeps its own copy because it's tightly coupled to the brush
 * minimap logic in that file. New charts (VCG, future GEX/regime
 * variants) should import from here instead so the chip semantics
 * stay consistent across the regime tabs.
 */

export type RangePresetSlug = "1m" | "3m" | "6m" | "1y" | "3y" | "5y" | "all";

export interface RangePreset {
  slug: RangePresetSlug;
  label: string;
  /** Number of trading sessions the preset spans. */
  sessions: number;
}

export const RANGE_PRESETS: ReadonlyArray<RangePreset> = [
  { slug: "1m", label: "1M", sessions: 21 },
  { slug: "3m", label: "3M", sessions: 63 },
  { slug: "6m", label: "6M", sessions: 126 },
  { slug: "1y", label: "1Y", sessions: 252 },
  // Deep-history presets for multi-year series (RV ratio). Safe for the
  // ~251-session regime charts: HistoryRangeChips hides presets whose span
  // exceeds `maxSessions`, so they never appear there.
  { slug: "3y", label: "3Y", sessions: 756 },
  { slug: "5y", label: "5Y", sessions: 1260 },
  { slug: "all", label: "All", sessions: Number.POSITIVE_INFINITY },
];

export const DEFAULT_RANGE_PRESET: RangePresetSlug = "1y";

export function presetSessions(slug: RangePresetSlug): number {
  const preset = RANGE_PRESETS.find((entry) => entry.slug === slug);
  return preset?.sessions ?? Number.POSITIVE_INFINITY;
}

/**
 * Slice a history array's `[start, end]` indices for a given preset.
 * `end` is the index of the last element to include (inclusive).
 * If `total === 0`, returns `[0, 0]` and the caller should treat the
 * array as empty.
 */
export function presetRange(slug: RangePresetSlug, total: number): [number, number] {
  if (total === 0) return [0, 0];
  const sessions = Math.min(presetSessions(slug), total);
  const start = Math.max(0, total - sessions);
  return [start, total - 1];
}

/**
 * Auto-pick the most useful default preset given the available
 * history depth. Short datasets fall back to "all" rather than
 * defaulting to "1y" and showing the whole thing anyway.
 */
export function defaultPresetForLength(length: number): RangePresetSlug {
  if (length >= 252) return "1y";
  if (length >= 126) return "6m";
  if (length >= 63) return "3m";
  if (length >= 21) return "1m";
  return "all";
}
