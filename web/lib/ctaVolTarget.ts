/**
 * CTA vol-targeting model helpers (mirror scripts/cri_scan.py cta_exposure_model).
 *
 * Est. selling is a stock of implied underweight vs 100% AUM at the vol target,
 * not a time-decaying sell pipeline. Day change is the flow companion:
 * delta of that stock vs the prior session's 20d rvol.
 */

export const CTA_VOL_TARGET = 10.0;
export const CTA_MAX_EXPOSURE = 200.0;
export const CTA_AUM_BN = 400.0;

export type CtaVolTargetInputs = {
  realizedVol: number | null | undefined;
  volTarget?: number;
  aumBn?: number;
};

export type CtaVolTargetModel = {
  realizedVol: number;
  exposurePct: number;
  forcedReductionPct: number;
  estSellingBn: number;
};

export type HistoryRvolEntry = {
  date: string;
  realized_vol?: number | null;
};

/** Exposure / reduction / est. selling from 20d realized vol (percent points). */
export function ctaVolTargetModel({
  realizedVol,
  volTarget = CTA_VOL_TARGET,
  aumBn = CTA_AUM_BN,
}: CtaVolTargetInputs): CtaVolTargetModel | null {
  if (realizedVol == null || !Number.isFinite(realizedVol) || realizedVol <= 0) {
    return null;
  }

  const exposurePct = Math.min((volTarget / realizedVol) * 100.0, CTA_MAX_EXPOSURE);
  const reduction = Math.max(0.0, 1.0 - exposurePct / 100.0);
  const estSellingBn = reduction * aumBn;

  return {
    realizedVol: round2(realizedVol),
    exposurePct: round1(exposurePct),
    forcedReductionPct: round1(reduction * 100.0),
    estSellingBn: round1(estSellingBn),
  };
}

/** Est. selling notional ($B) from rvol alone. */
export function estSellingBnFromRvol(
  realizedVol: number | null | undefined,
  aumBn: number = CTA_AUM_BN,
  volTarget: number = CTA_VOL_TARGET,
): number | null {
  return ctaVolTargetModel({ realizedVol, aumBn, volTarget })?.estSellingBn ?? null;
}

/**
 * Prior session est. selling from CRI history rvol.
 * Uses the latest history row with date strictly before currentDate when given;
 * otherwise the second-to-last row with a usable rvol.
 */
export function priorSessionEstSellingBn(
  history: HistoryRvolEntry[] | null | undefined,
  currentDate?: string | null,
  aumBn: number = CTA_AUM_BN,
  volTarget: number = CTA_VOL_TARGET,
): number | null {
  if (!history?.length) return null;

  const withVol = history.filter(
    (h) => h.realized_vol != null && Number.isFinite(h.realized_vol) && (h.realized_vol as number) > 0,
  );
  if (withVol.length === 0) return null;

  let prior: HistoryRvolEntry | null = null;
  if (currentDate) {
    for (let i = withVol.length - 1; i >= 0; i--) {
      if (withVol[i].date < currentDate) {
        prior = withVol[i];
        break;
      }
    }
  } else if (withVol.length >= 2) {
    prior = withVol[withVol.length - 2];
  }

  if (!prior) return null;
  return estSellingBnFromRvol(prior.realized_vol, aumBn, volTarget);
}

/** Day-over-day change in est. selling stock ($B). Positive = more implied cut. */
export function dayOverDayEstSellingDelta(
  currentBn: number | null | undefined,
  priorBn: number | null | undefined,
): number | null {
  if (currentBn == null || priorBn == null) return null;
  if (!Number.isFinite(currentBn) || !Number.isFinite(priorBn)) return null;
  return round1(currentBn - priorBn);
}

/** Compact model-input stamp for the VOL-TARGETING panel. */
export function formatCtaModelInputsStamp(opts: {
  realizedVol: number | null | undefined;
  volTarget?: number;
  aumBn?: number;
  asOfLabel?: string | null;
}): string {
  const volTarget = opts.volTarget ?? CTA_VOL_TARGET;
  const aumBn = opts.aumBn ?? CTA_AUM_BN;
  const rvol =
    opts.realizedVol != null && Number.isFinite(opts.realizedVol)
      ? `${opts.realizedVol.toFixed(2)}%`
      : "---";
  const parts = [
    `RVOL ${rvol}`,
    `TARGET ${volTarget.toFixed(0)}%`,
    `AUM $${round1(aumBn)}B`,
  ];
  if (opts.asOfLabel) {
    parts.push(`AS OF ${opts.asOfLabel}`);
  }
  return parts.join(" · ");
}

function round1(n: number): number {
  return Math.round(n * 10) / 10;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
