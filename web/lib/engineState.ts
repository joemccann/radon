import type { CriData } from "./useRegime";
import type { MarkovStateOutput } from "./useMarkovState";
import type { VcgData } from "./useVcg";
import type { GexData } from "./useGex";

export type EngineTone = "core" | "warn" | "fault" | "neutral";

export type EngineRow = {
  name: string;
  /** null = engine has no data (off-hours, cold cache) — honest empty, not a fault. */
  reading: string | null;
  /** Confidence-like 0–1 value for the meter; null hides it. */
  p: number | null;
  tone: EngineTone;
};

function emptyRow(name: string): EngineRow {
  return { name, reading: null, p: null, tone: "neutral" };
}

export function criRow(data: CriData | null | undefined): EngineRow {
  const cri = data?.cri;
  if (!cri || !Number.isFinite(cri.score)) return emptyRow("CRI");
  const level = cri.level?.toUpperCase() ?? "";
  const tone: EngineTone =
    level === "LOW" ? "core" : level === "ELEVATED" ? "warn" : level ? "fault" : "neutral";
  return {
    name: "CRI",
    reading: `${level} ${Math.round(cri.score)}`,
    p: Math.max(0, Math.min(1, cri.score / 100)),
    tone,
  };
}

export function markovRow(state: MarkovStateOutput | null | undefined): EngineRow {
  if (!state || state.currentBand == null) return emptyRow("MARKOV");
  // A zero-probability "next" band was never observed in the lookback —
  // that is a hold, not a transition forecast.
  const hasTransition = state.nextLikelyBand != null && (state.pNext ?? 0) > 0;
  const transition = hasTransition
    ? `${state.currentBand} → ${state.nextLikelyBand}`
    : `HOLD ${state.currentBand}`;
  const p = hasTransition ? state.pNext : state.pCurrent;
  return {
    name: "MARKOV",
    reading: transition,
    p,
    tone: p != null && p >= 0.5 ? "core" : "warn",
  };
}

export function vcgRow(data: VcgData | null | undefined): EngineRow {
  const signal = data?.signal;
  if (!signal?.interpretation) return emptyRow("VCG");
  const tone: EngineTone =
    signal.interpretation === "RISK_OFF" || signal.interpretation === "PANIC"
      ? "fault"
      : signal.interpretation === "EDR" || signal.interpretation === "WATCH"
        ? "warn"
        : "core";
  return {
    name: "VCG",
    reading: `${signal.interpretation} · ${signal.regime}`,
    p: Number.isFinite(signal.pi_panic) ? Math.max(0, Math.min(1, signal.pi_panic)) : null,
    tone,
  };
}

export function gexRow(data: GexData | null | undefined): EngineRow {
  if (!data?.bias?.direction) return emptyRow("GEX");
  const direction = data.bias.direction;
  const tone: EngineTone = direction.endsWith("BULL")
    ? "core"
    : direction === "NEUTRAL"
      ? "warn"
      : "fault";

  // Conviction proxy: distance from the gamma flip in expected-1d-range units.
  const flip = data.levels?.gex_flip?.strike ?? null;
  const low = data.expected_range?.low ?? null;
  const high = data.expected_range?.high ?? null;
  const range = low != null && high != null ? high - low : null;
  const p =
    flip != null && range != null && range > 0 && Number.isFinite(data.spot)
      ? Math.max(0, Math.min(1, Math.abs(data.spot - flip) / range))
      : null;

  return { name: "GEX", reading: `${data.ticker} ${direction}`, p, tone };
}

export function engineCount(rows: EngineRow[]): number {
  return rows.filter((row) => row.reading != null).length;
}
