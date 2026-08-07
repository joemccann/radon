export type RegimeRelationshipSource = {
  date: string;
  realized_vol?: number | null;
  cor1m?: number | null;
};

export type RegimeRelationshipLiveValues = Partial<Pick<RegimeRelationshipSource, "realized_vol" | "cor1m">>;

export type RegimeQuadrant =
  | "Systemic Panic"
  | "Fragile Calm"
  | "Stock Picker's Market"
  | "Goldilocks";

export const REGIME_QUADRANT_DETAILS: Record<RegimeQuadrant, string> = {
  "Systemic Panic":
    "RVOL is at or above its 20-session mean and COR1M is at or above its 20-session mean. Realized stress is elevated and the options market is still pricing broad lockstep behavior.",
  "Fragile Calm":
    "RVOL is below its 20-session mean and COR1M is at or above its 20-session mean. The tape looks calm, but implied correlation is still elevated.",
  "Stock Picker's Market":
    "RVOL is at or above its 20-session mean and COR1M is below its 20-session mean. Realized movement is elevated, but implied correlation is still contained.",
  Goldilocks:
    "RVOL is below its 20-session mean and COR1M is below its 20-session mean. Both realized volatility and implied co-movement are below recent norms.",
};

export type RelationshipBias = "Fear Premium" | "Realized Lead" | "Balanced";

export type RegimeRelationshipEntry = {
  date: string;
  realizedVol: number;
  cor1m: number;
  spread: number;
  realizedVolZ: number;
  cor1mZ: number;
  zDivergence: number;
  quadrant: RegimeQuadrant;
};

export type RegimeRelationshipSummary = {
  latestSpread: number;
  priorSpread: number | null;
  meanSpread: number;
  spreadState: RelationshipBias;
  zScoreBias: RelationshipBias;
  latestQuadrant: RegimeQuadrant;
  latestDivergence: number;
};

function isFiniteNumber(value: number | null | undefined): value is number {
  return value != null && Number.isFinite(value);
}

function mergeLatestSession(
  history: RegimeRelationshipSource[],
  liveValues?: RegimeRelationshipLiveValues,
): RegimeRelationshipSource[] {
  if (!history.length || !liveValues || Object.keys(liveValues).length === 0) {
    return history;
  }

  const merged = [...history];
  const last = {
    ...merged[merged.length - 1],
  };

  if (isFiniteNumber(liveValues.realized_vol)) {
    last.realized_vol = liveValues.realized_vol;
  }
  if (isFiniteNumber(liveValues.cor1m)) {
    last.cor1m = liveValues.cor1m;
  }

  merged[merged.length - 1] = last;
  return merged;
}

function mean(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function sampleStdDev(values: number[], avg: number): number {
  if (values.length < 2) return 0;
  const variance =
    values.reduce((sum, value) => sum + (value - avg) ** 2, 0) / (values.length - 1);
  return Math.sqrt(variance);
}

function zScore(value: number, avg: number, deviation: number): number {
  if (!Number.isFinite(deviation) || deviation <= 0) return 0;
  return (value - avg) / deviation;
}

export function classifyRegimeQuadrant(
  realizedVol: number,
  cor1m: number,
  realizedVolMean: number,
  cor1mMean: number,
): RegimeQuadrant {
  const highRvol = realizedVol >= realizedVolMean;
  const highCor1m = cor1m >= cor1mMean;

  if (highRvol && highCor1m) return "Systemic Panic";
  if (!highRvol && highCor1m) return "Fragile Calm";
  if (highRvol && !highCor1m) return "Stock Picker's Market";
  return "Goldilocks";
}

export function relationshipBias(value: number, epsilon = 0.15): RelationshipBias {
  if (value > epsilon) return "Fear Premium";
  if (value < -epsilon) return "Realized Lead";
  return "Balanced";
}

// Z-score statistical window in sessions. Earlier in the project the
// server only ever sent 20 entries, so computing stats over the full
// `history` array gave a 20-session z-score by accident. The history
// payload is now full (~13 months); scope the statistical window
// explicitly so the "20-session z-score overlay" copy stays accurate.
const Z_SCORE_WINDOW = 20;

export function buildRegimeRelationshipEntries(
  history: RegimeRelationshipSource[],
  liveValues?: RegimeRelationshipLiveValues,
): RegimeRelationshipEntry[] {
  const comparable = mergeLatestSession(history, liveValues).filter(
    (entry): entry is { date: string; realized_vol: number; cor1m: number } =>
      isFiniteNumber(entry.realized_vol) && isFiniteNumber(entry.cor1m),
  );

  if (comparable.length === 0) return [];

  // Stats over the rolling tail. When history is shorter than the window
  // we fall back to using everything we have, matching the prior behavior.
  const statsTail =
    comparable.length > Z_SCORE_WINDOW
      ? comparable.slice(-Z_SCORE_WINDOW)
      : comparable;
  const realizedVolSeries = statsTail.map((entry) => entry.realized_vol);
  const cor1mSeries = statsTail.map((entry) => entry.cor1m);

  const realizedVolMean = mean(realizedVolSeries);
  const cor1mMean = mean(cor1mSeries);
  const realizedVolStdDev = sampleStdDev(realizedVolSeries, realizedVolMean);
  const cor1mStdDev = sampleStdDev(cor1mSeries, cor1mMean);

  // Quadrants are STRUCTURAL labels classified against the full-sample
  // means — the same baseline the scatter crosshair draws. Classifying
  // against the 20-session z-window means made COR1M 7.38 read as "high
  // correlation" after weeks of collapsed correlation and headlined
  // SYSTEMIC PANIC with VIX under 15 (2026-08-07). Only the z-score
  // overlay is 20-session scoped.
  const quadrantRvolMean = mean(comparable.map((entry) => entry.realized_vol));
  const quadrantCor1mMean = mean(comparable.map((entry) => entry.cor1m));

  return comparable.map((entry) => {
    const realizedVolZ = zScore(entry.realized_vol, realizedVolMean, realizedVolStdDev);
    const cor1mZ = zScore(entry.cor1m, cor1mMean, cor1mStdDev);

    return {
      date: entry.date,
      realizedVol: entry.realized_vol,
      cor1m: entry.cor1m,
      spread: entry.cor1m - entry.realized_vol,
      realizedVolZ,
      cor1mZ,
      zDivergence: cor1mZ - realizedVolZ,
      quadrant: classifyRegimeQuadrant(
        entry.realized_vol,
        entry.cor1m,
        quadrantRvolMean,
        quadrantCor1mMean,
      ),
    };
  });
}

export function summarizeRegimeRelationship(
  entries: RegimeRelationshipEntry[],
): RegimeRelationshipSummary | null {
  if (entries.length === 0) return null;

  const latest = entries[entries.length - 1];
  const prior = entries.length > 1 ? entries[entries.length - 2] : null;
  const meanSpread = mean(entries.map((entry) => entry.spread));

  return {
    latestSpread: latest.spread,
    priorSpread: prior?.spread ?? null,
    meanSpread,
    spreadState: relationshipBias(latest.spread),
    zScoreBias: relationshipBias(latest.zDivergence),
    latestQuadrant: latest.quadrant,
    latestDivergence: latest.zDivergence,
  };
}
