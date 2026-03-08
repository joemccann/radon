/**
 * Client-side CRI (Crash Risk Index) calculator.
 *
 * Ports the 4 scoring functions from cri_scan.py so the UI can recompute
 * the CRI score in real-time using live VIX/VVIX/SPY prices + cached daily values.
 */

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/* ─── Component Scoring (each 0–25) ─────────────────── */

export function scoreVix(vix: number, vix5dRoc: number): number {
  if (!Number.isFinite(vix) || !Number.isFinite(vix5dRoc)) return 0;
  // VIX level score (0–15): linear from 15 → 40
  const levelScore = clamp(((vix - 15) / (40 - 15)) * 15, 0, 15);
  // VIX rate-of-change score (0–10): linear from 0% → 60%
  const rocScore = clamp((Math.max(vix5dRoc, 0) / 60) * 10, 0, 10);
  return clamp(levelScore + rocScore, 0, 25);
}

export function scoreVvix(vvix: number, vvixVixRatio: number): number {
  if (!Number.isFinite(vvix) || !Number.isFinite(vvixVixRatio)) return 0;
  // VVIX level score (0–17): linear from 90 → 140
  const levelScore = clamp(((vvix - 90) / (140 - 90)) * 17, 0, 17);
  // VVIX/VIX ratio score (0–8): high ratio (> 8) means convexity demand
  const ratioScore = clamp(((vvixVixRatio - 5) / (8 - 5)) * 8, 0, 8);
  return clamp(levelScore + ratioScore, 0, 25);
}

export function scoreCorrelation(corr: number, corr5dChange: number): number {
  if (!Number.isFinite(corr)) return 0;
  const safeChange = Number.isFinite(corr5dChange) ? corr5dChange : 0;
  // Correlation level score (0–17): linear from 0.25 → 0.70
  const levelScore = clamp(((corr - 0.25) / (0.70 - 0.25)) * 17, 0, 17);
  // Correlation spike score (0–8): linear from 0 → 0.20 change
  const spikeScore = clamp((Math.max(safeChange, 0) / 0.20) * 8, 0, 8);
  return clamp(levelScore + spikeScore, 0, 25);
}

export function scoreMomentum(spxDistancePct: number): number {
  if (!Number.isFinite(spxDistancePct)) return 0;
  // Score rises as SPX goes below MA. Linear from 0% to -10%.
  if (spxDistancePct >= 0) return 0;
  return clamp((Math.abs(spxDistancePct) / 10) * 25, 0, 25);
}

/* ─── Level classification ───────────────────────────── */

export type CriLevel = "LOW" | "ELEVATED" | "HIGH" | "CRITICAL";

export function criLevel(score: number): CriLevel {
  if (score < 25) return "LOW";
  if (score < 50) return "ELEVATED";
  if (score < 75) return "HIGH";
  return "CRITICAL";
}

/* ─── Composite CRI ──────────────────────────────────── */

export type CriComponents = {
  vix: number;
  vvix: number;
  correlation: number;
  momentum: number;
};

export type CriResult = {
  score: number;
  level: CriLevel;
  components: CriComponents;
};

export type CriInputs = {
  vix: number;
  vix5dRoc: number;
  vvix: number;
  vvixVixRatio: number;
  corr: number;
  corr5dChange: number;
  spxDistancePct: number;
};

export function computeCri(inputs: CriInputs): CriResult {
  const vixScore = scoreVix(inputs.vix, inputs.vix5dRoc);
  const vvixScore = scoreVvix(inputs.vvix, inputs.vvixVixRatio);
  const corrScore = scoreCorrelation(inputs.corr, inputs.corr5dChange);
  const momentumScore = scoreMomentum(inputs.spxDistancePct);

  const total = clamp(vixScore + vvixScore + corrScore + momentumScore, 0, 100);

  return {
    score: Math.round(total * 10) / 10,
    level: criLevel(total),
    components: {
      vix: Math.round(vixScore * 10) / 10,
      vvix: Math.round(vvixScore * 10) / 10,
      correlation: Math.round(corrScore * 10) / 10,
      momentum: Math.round(momentumScore * 10) / 10,
    },
  };
}
