/**
 * Bullish Percent Index domain lib — types, runtime guards, chart-entry
 * building, and the state lozenge mapping for the section 4 payload
 * contract (schema_version 1, tasks/bpi-indicator-plan.md).
 *
 * All BPI math is computed server-side by scripts/bpi_scan.py and persisted
 * per index in bpi_snapshots; this module never re-derives the index. The
 * only client-side derivation is the chart's cross-up-through-30 flag,
 * which is a rendering concern over the payload's history array.
 */

export type BpiIndexSymbol = "NDX" | "SPX" | "RUT";

export const BPI_INDEX_SYMBOLS: readonly BpiIndexSymbol[] = ["NDX", "SPX", "RUT"];

export type BpiState = "OVERSOLD" | "OVERBOUGHT" | "NEUTRAL";

export interface BpiHistoryPoint {
  date: string;
  bpi: number;
}

export interface BpiThresholds {
  oversold: number;
  overbought: number;
}

export interface BpiPayload {
  schema_version: number;
  index_symbol: string;
  index_name: string;
  taken_at: string;
  as_of_session: string;
  bpi: number;
  members: number;
  bullish: number;
  state: BpiState;
  cross_up_30: boolean;
  thresholds: BpiThresholds;
  /** Trailing sessions, ascending by date, at most ~504 points. */
  history: BpiHistoryPoint[];
  sources?: Record<string, unknown>;
}

export interface BpiMissingIndex {
  missing: true;
  index_symbol: string;
}

export type BpiIndexEntry = BpiPayload | BpiMissingIndex;

/** GET /api/bpi response shape: always all three indices, missing filled. */
export interface BpiResponse {
  generated_at: string | null;
  indices: Record<BpiIndexSymbol, BpiIndexEntry>;
}

const BPI_STATES = new Set<string>(["OVERSOLD", "OVERBOUGHT", "NEUTRAL"]);

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isBpiHistoryPoint(value: unknown): value is BpiHistoryPoint {
  if (!value || typeof value !== "object") return false;
  const point = value as Partial<BpiHistoryPoint>;
  return typeof point.date === "string" && finite(point.bpi);
}

function isBpiThresholds(value: unknown): value is BpiThresholds {
  if (!value || typeof value !== "object") return false;
  const thresholds = value as Partial<BpiThresholds>;
  return finite(thresholds.oversold) && finite(thresholds.overbought);
}

export function isBpiMissingIndex(value: unknown): value is BpiMissingIndex {
  if (!value || typeof value !== "object") return false;
  const entry = value as Partial<BpiMissingIndex>;
  return entry.missing === true && typeof entry.index_symbol === "string";
}

export function isBpiPayload(value: unknown): value is BpiPayload {
  if (!value || typeof value !== "object") return false;
  const payload = value as Partial<BpiPayload>;
  return (
    finite(payload.schema_version)
    && typeof payload.index_symbol === "string"
    && typeof payload.index_name === "string"
    && typeof payload.taken_at === "string"
    && typeof payload.as_of_session === "string"
    && finite(payload.bpi)
    && finite(payload.members)
    && finite(payload.bullish)
    && typeof payload.state === "string"
    && BPI_STATES.has(payload.state)
    && typeof payload.cross_up_30 === "boolean"
    && isBpiThresholds(payload.thresholds)
    && Array.isArray(payload.history)
    && payload.history.every(isBpiHistoryPoint)
  );
}

export interface BpiChartEntry {
  date: string;
  bpi: number;
  /** True where the series crosses UP through the oversold threshold. */
  crossUp: boolean;
}

/**
 * Zip payload history into chart entries with the cross-up flag computed
 * over the FULL history: bpi[i-1] < threshold && bpi[i] >= threshold.
 * Callers slice AFTER building so a dot at a slice boundary is never lost.
 */
export function buildBpiEntries(
  history: BpiHistoryPoint[],
  oversoldThreshold: number,
): BpiChartEntry[] {
  return history.map((point, index) => ({
    date: point.date,
    bpi: point.bpi,
    crossUp:
      index > 0
      && history[index - 1].bpi < oversoldThreshold
      && point.bpi >= oversoldThreshold,
  }));
}

export type BpiTone = "positive" | "warning" | "neutral";

export interface BpiStateBadge {
  label: string;
  tone: BpiTone;
}

/**
 * State lozenge mapping. Oversold-bull is the OPPORTUNITY read, so the
 * latest cross UP through 30 is the positive signal, OVERSOLD itself is
 * amber attention (not negative), and everything else stays neutral.
 */
export function classifyBpiState(
  payload: Pick<BpiPayload, "state" | "cross_up_30">,
): BpiStateBadge {
  if (payload.cross_up_30) return { label: "CROSS UP 30", tone: "positive" };
  if (payload.state === "OVERSOLD") return { label: "OVERSOLD", tone: "warning" };
  if (payload.state === "OVERBOUGHT") return { label: "OVERBOUGHT", tone: "neutral" };
  return { label: "NEUTRAL", tone: "neutral" };
}

export function bpiToneColor(tone: BpiTone): string {
  switch (tone) {
    case "positive": return "var(--positive)";
    case "warning": return "var(--warning)";
    case "neutral": return "var(--text-secondary)";
  }
}
