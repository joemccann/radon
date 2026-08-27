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
  | { kind: "undefined-risk" }
  /**
   * ONE bound resolved and the other did not — Gate 1 cannot be taken, but
   * this structure is not a close-out either. Rendering nothing here (the old
   * `null`) was indistinguishable from a structure that legitimately has no
   * ratio. BOTH bounds null stays `null`: with coverage resolved that IS the
   * close-out shape, and the still-resolving window never reaches this row
   * because `OrderConfirmSummary` returns its coverage skeleton first. R-251.
   */
  | { kind: "unmeasured" };

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
  const maxGain = summary.maxGain;
  // Both absent: a close-out, which has no ratio to take. One absent: Gate 1
  // is UNMEASURED, which is a different statement and used to render the same
  // (as nothing at all). R-251.
  if (maxLoss == null && maxGain == null && summary.maxGainUnbounded !== true) return null;
  if (maxLoss == null) return { kind: "unmeasured" };
  if (maxLoss <= 0) return null;

  // `maxGainUnbounded` alone is not a Gate 1 pass: it was rendered green with
  // data-meets-convexity="true" and no computation behind it, on e.g. a large
  // long-call debit whose realistic payoff is nowhere near 2:1. R-251.
  if (summary.maxGainUnbounded === true) return { kind: "uncapped" };

  if (maxGain == null) return { kind: "unmeasured" };
  if (maxGain <= 0) return null;

  const ratio = maxGain / maxLoss;
  return { kind: "ratio", ratio, meetsConvexity: ratio >= CONVEXITY_MIN_RATIO };
}

/** "7.2 : 1" — one decimal, trailing ".0" dropped so 2:1 reads clean. */
export function formatPayoffRatio(ratio: number): string {
  const rounded = Math.round(ratio * 10) / 10;
  return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)} : 1`;
}
