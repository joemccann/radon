export type OptionsExposureFrequency = "eod" | "intraday";

export type OptionsExposureMetric =
  | "net_gex"
  | "abs_gex"
  | "net_dex"
  | "abs_dex"
  | "open_interest";

export type OptionsExposureStrikeWindow = 5 | 10 | 20 | 50 | "all";

export type OptionsExposureLevelKey =
  | "hvl"
  | "call_resistance"
  | "put_support"
  | "call_resistance_0dte"
  | "put_support_0dte"
  | "max_1d"
  | "min_1d";

export interface OptionsExposureExpiration {
  expiration_date: string;
  dte: number;
}

export interface OptionsExposureLevel {
  key: OptionsExposureLevelKey;
  label: string;
  value: number | null;
}

export interface OptionsExposureCells {
  net_gex: number[];
  abs_gex: number[];
  net_dex: number[];
  abs_dex: number[];
  oi_call: number[];
  oi_put: number[];
  strike_idx: number[];
  expiration_idx: number[];
}

export interface OptionsExposurePayload {
  schema_version: number;
  symbol: string;
  source: string;
  source_time: string;
  fetched_at: string;
  frequency: OptionsExposureFrequency;
  spot: number;
  strikes: number[];
  expirations: OptionsExposureExpiration[];
  cells: OptionsExposureCells;
  levels: OptionsExposureLevel[];
  units: Record<string, string>;
  complete: boolean;
}

export interface OptionsExposureRow {
  strike: number;
  value: number;
  putOi: number;
  callOi: number;
}

export const EXPOSURE_METRIC_OPTIONS = [
  { value: "net_gex", label: "Net GEX" },
  { value: "abs_gex", label: "Abs GEX" },
  { value: "net_dex", label: "Net DEX" },
  { value: "abs_dex", label: "Abs DEX" },
  { value: "open_interest", label: "Open Interest" },
] as const satisfies ReadonlyArray<{ value: OptionsExposureMetric; label: string }>;

export const EXPOSURE_STRIKE_OPTIONS = [
  { value: 5, label: "5 Strikes +/-" },
  { value: 10, label: "10 Strikes +/-" },
  { value: 20, label: "20 Strikes +/-" },
  { value: 50, label: "50 Strikes +/-" },
  { value: "all", label: "All Strikes" },
] as const satisfies ReadonlyArray<{ value: OptionsExposureStrikeWindow; label: string }>;

export const EXPOSURE_LEVEL_OPTIONS = [
  { key: "hvl", label: "HVL" },
  { key: "call_resistance", label: "CR" },
  { key: "put_support", label: "PS" },
  { key: "call_resistance_0dte", label: "CR 0DTE" },
  { key: "put_support_0dte", label: "PS 0DTE" },
  { key: "max_1d", label: "1D Max" },
  { key: "min_1d", label: "1D Min" },
] as const satisfies ReadonlyArray<{ key: OptionsExposureLevelKey; label: string }>;

export const DEFAULT_EXPOSURE_CONTROLS = {
  metric: "net_gex" as OptionsExposureMetric,
  strikeWindow: 10 as OptionsExposureStrikeWindow,
  frequency: "eod" as OptionsExposureFrequency,
  expirationDates: null as Set<string> | null,
  visibleLevels: new Set<OptionsExposureLevelKey>(EXPOSURE_LEVEL_OPTIONS.map((option) => option.key)),
};

const METRIC_CELL_KEY: Exclude<OptionsExposureMetric, "open_interest">[] = [
  "net_gex",
  "abs_gex",
  "net_dex",
  "abs_dex",
];

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * Collapse the provider's flattened strike-by-expiration matrix into a
 * strike profile. The backend normalises GEX/DEX values to USD; OI remains
 * contracts. Invalid matrix indexes are ignored so one malformed cell does
 * not erase the rest of an otherwise usable measurement.
 */
export function aggregateOptionsExposure(
  payload: OptionsExposurePayload,
  metric: OptionsExposureMetric,
  expirationDates: ReadonlySet<string> | null,
): OptionsExposureRow[] {
  const rows = payload.strikes.map((strike) => ({ strike, value: 0, putOi: 0, callOi: 0 }));
  const { cells } = payload;
  const cellCount = Math.min(cells.strike_idx.length, cells.expiration_idx.length);

  for (let index = 0; index < cellCount; index += 1) {
    const strikeIndex = cells.strike_idx[index];
    const expirationIndex = cells.expiration_idx[index];
    const row = rows[strikeIndex];
    const expiration = payload.expirations[expirationIndex];
    if (!row || !expiration) continue;
    if (expirationDates && !expirationDates.has(expiration.expiration_date)) continue;

    const callOi = cells.oi_call[index];
    const putOi = cells.oi_put[index];
    if (finite(callOi)) row.callOi += Math.abs(callOi);
    if (finite(putOi)) row.putOi += Math.abs(putOi);

    if (metric === "open_interest") {
      row.value += (finite(callOi) ? callOi : 0) - (finite(putOi) ? putOi : 0);
      continue;
    }

    const values = cells[metric];
    const value = values[index];
    if (!finite(value)) continue;
    row.value += METRIC_CELL_KEY.includes(metric) && metric.startsWith("abs_")
      ? Math.abs(value)
      : value;
  }

  return rows;
}

/** Return the nearest strike plus N strikes on either side, highest first. */
export function selectStrikeWindow(
  rows: OptionsExposureRow[],
  spot: number,
  strikeWindow: OptionsExposureStrikeWindow,
): OptionsExposureRow[] {
  const ascending = [...rows].sort((a, b) => a.strike - b.strike);
  if (strikeWindow === "all") return ascending.reverse();
  if (ascending.length === 0) return [];

  let nearestIndex = 0;
  for (let index = 1; index < ascending.length; index += 1) {
    if (Math.abs(ascending[index].strike - spot) < Math.abs(ascending[nearestIndex].strike - spot)) {
      nearestIndex = index;
    }
  }

  return ascending
    .slice(Math.max(0, nearestIndex - strikeWindow), nearestIndex + strikeWindow + 1)
    .reverse();
}

export function nearestStrike(strikes: number[], spot: number): number | null {
  if (strikes.length === 0) return null;
  return strikes.reduce((nearest, strike) => (
    Math.abs(strike - spot) < Math.abs(nearest - spot) ? strike : nearest
  ));
}

export function exposureMetricLabel(metric: OptionsExposureMetric): string {
  return EXPOSURE_METRIC_OPTIONS.find((option) => option.value === metric)?.label ?? "Exposure";
}

export function isAbsoluteExposureMetric(metric: OptionsExposureMetric): boolean {
  return metric === "abs_gex" || metric === "abs_dex";
}

export function isOptionsExposurePayload(value: unknown): value is OptionsExposurePayload {
  if (!value || typeof value !== "object") return false;
  const payload = value as Partial<OptionsExposurePayload>;
  const cells = payload.cells;
  return (
    finite(payload.schema_version)
    && typeof payload.symbol === "string"
    && typeof payload.source === "string"
    && typeof payload.source_time === "string"
    && typeof payload.fetched_at === "string"
    && (payload.frequency === "eod" || payload.frequency === "intraday")
    && finite(payload.spot)
    && Array.isArray(payload.strikes)
    && payload.strikes.every(finite)
    && Array.isArray(payload.expirations)
    && Array.isArray(payload.levels)
    && typeof payload.complete === "boolean"
    && Boolean(cells)
    && Array.isArray(cells?.net_gex)
    && Array.isArray(cells?.abs_gex)
    && Array.isArray(cells?.net_dex)
    && Array.isArray(cells?.abs_dex)
    && Array.isArray(cells?.oi_call)
    && Array.isArray(cells?.oi_put)
    && Array.isArray(cells?.strike_idx)
    && Array.isArray(cells?.expiration_idx)
  );
}
