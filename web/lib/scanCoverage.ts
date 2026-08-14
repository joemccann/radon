/** Coverage + snapshot picker for UW universe scans.

A completed NDX pass that 429'd every name still lands as
`candidates_found: 0`. Readers must not treat that as a real empty tape.
*/

export type ScanCoverage = {
  tickers?: number;
  ok?: number;
  no_setup?: number;
  rate_limited?: number;
  errors?: number;
  completed?: number;
};

const MIN_COMPLETED_RATIO = 0.5;

export function payloadHasCandidates(
  data: Record<string, unknown> | null | undefined,
): boolean {
  if (data == null) return false;
  const found = Number(data.candidates_found ?? 0);
  if (Number.isFinite(found) && found > 0) return true;
  return Array.isArray(data.results) && data.results.length > 0;
}

export function isCoverageFailedScan(
  data: Record<string, unknown> | null | undefined,
): boolean {
  if (payloadHasCandidates(data)) return false;
  if (data == null) return true;
  const coverage = data.coverage;
  let tickers = Number(data.tickers_scanned ?? 0);
  let completed = 0;
  if (coverage != null && typeof coverage === "object") {
    const block = coverage as ScanCoverage;
    tickers = Number(block.tickers ?? tickers);
    completed = Number(block.completed ?? 0);
  }
  if (!Number.isFinite(tickers) || tickers <= 0) return true;
  if (!Number.isFinite(completed) || completed < 0) return true;
  return completed / tickers < MIN_COMPLETED_RATIO;
}

export function pickUsableScanSnapshot(
  rows: Array<{ scan_time: string; payload: string }>,
): { scanTime: string; data: Record<string, unknown> } | null {
  const parsed: Array<{ scanTime: string; data: Record<string, unknown> }> = [];
  for (const row of rows) {
    try {
      const data = JSON.parse(row.payload) as Record<string, unknown>;
      if (data != null && typeof data === "object") {
        parsed.push({ scanTime: row.scan_time, data });
      }
    } catch {
      continue;
    }
  }
  return parsed.find((row) => payloadHasCandidates(row.data)) ?? parsed[0] ?? null;
}
