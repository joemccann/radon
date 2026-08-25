/**
 * Payoff ratio — the embedded leverage of a structure, expressed as the
 * gain-per-dollar-risked multiple the operator reads before execution.
 *
 * A spread's leverage is not visible from its debit. A $40,500 bull call
 * spread risking $45,600 to make $329,400 is a 7.2:1 trade; the same debit
 * on a tighter width can be 1.2:1. Both render identical "Total / Max Gain /
 * Max Loss" rows, so the multiple has to be shown, not inferred.
 *
 * This is a pure derivation of `maxGain` / `maxLoss` as they already sit on
 * the augmented summary — it does NOT re-derive risk. Keeping it downstream
 * of the risk chokepoint means it cannot disagree with the numbers rendered
 * beside it (see web/CLAUDE.md → Order-Risk Chokepoint).
 *
 * Gate 1 (convexity) requires gain >= 2x loss, so the multiple doubles as
 * the gate readout.
 */

/** Gate 1: gain must be at least this multiple of loss. */
export const CONVEXITY_MIN_RATIO = 2;

export type PayoffRatio =
  /** Both bounds known — a real multiple. */
  | { kind: "ratio"; ratio: number; meetsConvexity: boolean }
  /** Capped loss, unbounded upside (long call / long straddle tail). */
  | { kind: "uncapped" }
  /** Loss is unbounded — no multiple is meaningful. */
  | { kind: "undefined-risk" };

interface PayoffInput {
  maxGain?: number | null;
  maxLoss?: number | null;
  maxGainUnbounded?: boolean;
  maxLossUnbounded?: boolean;
}

/**
 * Derive the payoff multiple. Returns null when the structure carries no
 * risk bounds to divide (close-outs), or when max loss is zero.
 */
export function computePayoffRatio(summary: PayoffInput): PayoffRatio | null {
  if (summary.maxLossUnbounded === true) return { kind: "undefined-risk" };

  const maxLoss = summary.maxLoss;
  if (maxLoss == null || maxLoss <= 0) return null;

  if (summary.maxGainUnbounded === true) return { kind: "uncapped" };

  const maxGain = summary.maxGain;
  if (maxGain == null || maxGain <= 0) return null;

  const ratio = maxGain / maxLoss;
  return { kind: "ratio", ratio, meetsConvexity: ratio >= CONVEXITY_MIN_RATIO };
}

/** "7.2 : 1" — one decimal, trailing ".0" dropped so 2:1 reads clean. */
export function formatPayoffRatio(ratio: number): string {
  const rounded = Math.round(ratio * 10) / 10;
  return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)} : 1`;
}
