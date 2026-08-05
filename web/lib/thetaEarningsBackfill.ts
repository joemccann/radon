/**
 * Backfill earnings onto theta-harvester rows when the scan snapshot predates
 * the earnings annotation field (or annotation failed to attach the key).
 *
 * within_dte is computed per-row from structure.dte so a single batch call
 * (no shared DTE query param) stays correct across mixed expiries.
 */

export type ThetaEarningsSlim = {
  report_date: string | null;
  report_time: string | null;
  days_until: number | null;
  within_dte: boolean | null;
  source: string | null;
  expected_move_pct: number | null;
};

type ThetaRowLike = {
  ticker?: unknown;
  earnings?: unknown;
  structure?: { dte?: unknown } | null;
  [key: string]: unknown;
};

type EarningsApiRow = {
  ticker?: string;
  report_date?: string | null;
  report_time?: string | null;
  days_until?: number | null;
  source?: string | null;
  expected_move_pct?: number | null;
  missing?: boolean;
};

/** True when any result is missing the `earnings` key entirely (pre-feature snapshot). */
export function resultsNeedEarningsBackfill(results: unknown): boolean {
  if (!Array.isArray(results) || results.length === 0) return false;
  return results.some(
    (row) => row != null && typeof row === "object" && !("earnings" in (row as object)),
  );
}

export function withinDteForRow(
  daysUntil: number | null | undefined,
  dte: number | null | undefined,
): boolean | null {
  if (daysUntil == null || !Number.isFinite(daysUntil)) return null;
  if (dte == null || !Number.isFinite(dte)) return null;
  return daysUntil >= 0 && daysUntil <= dte;
}

export function slimEarningsFromApi(
  api: EarningsApiRow | null | undefined,
  dte: number | null | undefined,
): ThetaEarningsSlim | null {
  if (!api || api.missing || api.report_date == null) return null;
  const days =
    typeof api.days_until === "number" && Number.isFinite(api.days_until)
      ? api.days_until
      : null;
  return {
    report_date: api.report_date,
    report_time: api.report_time ?? null,
    days_until: days,
    within_dte: withinDteForRow(days, dte),
    source: api.source ?? null,
    expected_move_pct:
      typeof api.expected_move_pct === "number" && Number.isFinite(api.expected_move_pct)
        ? api.expected_move_pct
        : null,
  };
}

export function mergeEarningsIntoThetaResults(
  results: ThetaRowLike[],
  byTicker: Map<string, EarningsApiRow>,
): ThetaRowLike[] {
  return results.map((row) => {
    if (row != null && typeof row === "object" && "earnings" in row) return row;
    const ticker = typeof row.ticker === "string" ? row.ticker.toUpperCase() : "";
    const dte =
      row.structure && typeof row.structure.dte === "number" && Number.isFinite(row.structure.dte)
        ? row.structure.dte
        : null;
    const api = ticker ? byTicker.get(ticker) : undefined;
    return { ...row, earnings: slimEarningsFromApi(api, dte) };
  });
}

/**
 * Pure merge helper used by the GET route. `fetchBatch` returns FastAPI
 * `{ results: EarningsApiRow[] }` or throws / returns null to fail open.
 */
export async function backfillThetaEarningsPayload(
  data: Record<string, unknown>,
  fetchBatch: (tickers: string[]) => Promise<{ results?: EarningsApiRow[] } | null>,
): Promise<Record<string, unknown>> {
  const results = data.results;
  if (!resultsNeedEarningsBackfill(results)) return data;

  const rows = results as ThetaRowLike[];
  const tickers: string[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const t = typeof row.ticker === "string" ? row.ticker.toUpperCase().trim() : "";
    if (!t || seen.has(t)) continue;
    if (!/^[A-Z]{1,6}$/.test(t)) continue;
    seen.add(t);
    tickers.push(t);
  }
  if (tickers.length === 0) return data;

  // Cap matches FastAPI /earnings batch max.
  const capped = tickers.slice(0, 50);
  let body: { results?: EarningsApiRow[] } | null = null;
  try {
    body = await fetchBatch(capped);
  } catch {
    return data;
  }
  if (!body || !Array.isArray(body.results)) return data;

  const byTicker = new Map<string, EarningsApiRow>();
  for (const item of body.results) {
    if (item && typeof item.ticker === "string") {
      byTicker.set(item.ticker.toUpperCase(), item);
    }
  }
  return { ...data, results: mergeEarningsIntoThetaResults(rows, byTicker) };
}
