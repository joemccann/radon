const VOL_WINDOW = 20;

export type RegimeHistoryEntry = {
  date: string;
  vix: number;
  vvix: number;
  spy: number;
  cor1m?: number;
  realized_vol?: number | null;
  spx_vs_ma_pct: number;
  vix_5d_roc: number;
};

function asFiniteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function computeRealizedVolFromCloses(closes: number[], window = VOL_WINDOW): number | null {
  if (closes.length < window + 1) return null;

  const logReturns: number[] = [];
  for (let index = 1; index < closes.length; index += 1) {
    const prev = closes[index - 1];
    const next = closes[index];
    if (!(prev > 0) || !(next > 0)) return null;
    logReturns.push(Math.log(next / prev));
  }

  if (logReturns.length < 2) return null;

  const mean = logReturns.reduce((sum, value) => sum + value, 0) / logReturns.length;
  const variance =
    logReturns.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (logReturns.length - 1);

  return Math.sqrt(variance) * Math.sqrt(252) * 100;
}

export function backfillRealizedVolHistory(
  history: RegimeHistoryEntry[],
  spyCloses: unknown,
  window = VOL_WINDOW,
): RegimeHistoryEntry[] {
  if (!Array.isArray(history) || history.length === 0) return [];

  const normalizedHistory = history.map((entry) => ({ ...entry }));
  if (!Array.isArray(spyCloses)) return normalizedHistory;

  const closes = spyCloses
    .map((value) => asFiniteNumber(value))
    .filter((value): value is number => value !== null);

  const requiredCloses = normalizedHistory.length + window;
  if (closes.length < requiredCloses) return normalizedHistory;

  const relevantCloses = closes.slice(-requiredCloses);

  return normalizedHistory.map((entry, index) => {
    if (typeof entry.realized_vol === "number" && Number.isFinite(entry.realized_vol)) {
      return entry;
    }

    const windowCloses = relevantCloses.slice(index, index + window + 1);
    const realizedVol = computeRealizedVolFromCloses(windowCloses, window);
    return realizedVol == null ? entry : { ...entry, realized_vol: realizedVol };
  });
}
