/**
 * Order Risk — single source of truth for max-loss / max-gain across the
 * order builder, the per-position order tab, and the instrument modal.
 *
 * Why this exists
 * ---------------
 * The legacy paths assumed every combo was a vertical debit spread and
 * displayed "max loss = debit paid" for every two-leg structure. That formula
 * is correct ONLY when both legs share a strike width that caps the downside
 * (bull call, bear put, iron condor). Risk reversals (short put + long call)
 * carry full assignment exposure on the short put leg: a $150 strike short
 * put × 50 contracts is $750,000 of risk at the assignment-at-zero bound,
 * not the $5,000 net debit.
 *
 * The functions here treat each leg independently, classify it as bounded
 * or unbounded, and roll the legs up into a combo verdict. The combo verdict
 * is consumed by `OrderSummary` so the confirmation UI can:
 *   - show a numeric max loss when the structure is fully bounded
 *   - show "UNBOUNDED" when a short call leg is uncovered
 *   - show a numeric max loss + an "undefined risk" badge when a short put
 *     leg drives the bound (the user should understand the number reflects
 *     a stock-to-zero stress, not a defined-risk cap)
 */

// Relative path: web/lib/order/risk/internal/ → web/lib/types.ts (three levels up).
// Direct imports of this module from outside the `internal/` folder are
// banned by ESLint (`no-restricted-imports`) — every caller must go through
// the public `useOrderRisk` / `<OrderRiskGate>` surface.
import type { PortfolioData } from "../../../types";

export type LegRight = "C" | "P";
export type LegAction = "BUY" | "SELL";

/**
 * Shape consumed by `augmentOrderLegsWithPortfolioCoverage` — matches the
 * chain-order-builder's per-leg state. Quantity here is the raw user-entered
 * contract count for the leg (NOT a per-combo ratio); augmentation normalises
 * it to a ratio against `comboQuantity` so callers don't need to pre-divide
 * before calling `computeOrderRisk`.
 */
export interface ChainOrderLeg {
  action: LegAction;
  right: LegRight;
  strike: number;
  expiry: string;
  quantity: number;
}

/**
 * Discriminated union — option coverage and stock coverage carry different
 * meaningful fields. Stock has shares + per-share avg_cost (no strike, no
 * expiry); option has strike, expiry, contracts, right. Splitting them keeps
 * the chip-rendering code from formatting stock as "0× $0 Call" or similar.
 */
export type CoveringPortfolioLeg =
  | {
      type: "Option";
      right: LegRight;
      strike: number;
      expiry: string;
      contracts: number;
      /** Cost basis in dollars of the `contracts` consumed; null when unknown. */
      entryCostDollars?: number | null;
    }
  | { type: "Stock"; shares: number; avgCost: number };

export interface AugmentedOrderLegs {
  /** Risk legs ready for `computeOrderRisk`, with per-combo ratios. */
  riskLegs: OrderRiskLeg[];
  /** Number of combo units — pass to `computeOrderRisk` as `comboQuantity`. */
  comboQuantity: number;
  /** Held positions from portfolio injected as virtual coverage. */
  coveringLegs: CoveringPortfolioLeg[];
  /**
   * Dollar amount (per share, per combo) to ADD to the chain's `netPremium`
   * before calling `computeOrderRisk`. Non-zero only when stock coverage
   * folds its sunk cost basis into the synthetic combo so the risk model
   * sees the structure as "long stock at $X.YZ + short call at $K" instead
   * of "short call at $K alone". Callers MUST add this — otherwise the
   * stock-to-zero floor reads as $0 max loss.
   *
   * The chain UI continues to display the unmodified user-entered limit
   * (the order's actual cash flow), only the max-loss/max-gain math uses
   * the adjusted premium.
   */
  netPremiumAdjustment: number;
  /**
   * Per-share, per-combo cost basis of the held OPTION legs injected as
   * coverage. Not folded into `netPremiumAdjustment`: the order-only figures
   * treat a held long as already paid for. Adding this to the net premium
   * prices the whole spread (held leg at its basis + this order). 0 when no
   * option covers the order; null when any consumed lot has no known basis.
   */
  heldOptionBasisAdjustment: number | null;
}

export interface OrderRiskLeg {
  action: LegAction;
  right: LegRight;
  strike: number;
  expiry: string;
  /** Per-combo ratio. Combined with `quantity` to derive contract count. */
  quantity: number;
  /**
   * Total contracts already held LONG of this exact option (same symbol /
   * expiry / strike / right). Used to recognise SELL-to-close as a bounded
   * trade — selling against an existing long is not a naked short.
   *
   * - SELL with `coveringLongContracts >= effectiveContracts` → pure close,
   *   no new exposure. Loss/gain attribution = 0 (the order itself adds no
   *   structural risk; the cash flow is surfaced via `totalCost` upstream).
   * - SELL with `coveringLongContracts < effectiveContracts` → only the
   *   uncovered excess (`effectiveContracts - coveringLongContracts`) is
   *   modelled as naked. The covered portion drops out.
   *
   * Omit (or set to 0) for opening trades and naked-short scenarios — the
   * model then behaves exactly as before.
   */
  coveringLongContracts?: number;
  /** False only for virtual stock coverage whose acquisition basis is unknown. */
  costBasisKnown?: boolean;
}

export interface OrderRisk {
  /** Worst-case dollar loss across the combo, or null if unbounded. */
  maxLoss: number | null;
  /** Best-case dollar gain across the combo, or null if unbounded. */
  maxGain: number | null;
  /** True when at least one leg has no theoretical upper-bound loss
   *  (uncovered short call). `maxLoss` is null in that case. */
  maxLossUnbounded: boolean;
  /** True when at least one leg has no theoretical upper-bound gain
   *  (long call). `maxGain` is null in that case. */
  maxGainUnbounded: boolean;
  /** Either leg is unbounded OR a naked short put bounds the trade only at
   *  stock-to-zero. Used by the UI to surface a Gate 1 warning. */
  hasUndefinedRisk: boolean;
  /** Human readable list of legs driving the undefined-risk classification. */
  undefinedRiskReason: string | null;
}

const MULTIPLIER = 100;

/**
 * Compute max loss / max gain for a single options leg in isolation,
 * expressed as positive dollar magnitudes plus an unbounded flag.
 *
 * Premium convention
 * ------------------
 * `legPremium` is the per-share dollar price paid (for BUY legs) or
 * received (for SELL legs). It is always a positive magnitude here —
 * sign is encoded by the leg's action.
 *
 * Return shape
 * ------------
 *   maxLoss / maxGain are positive dollar magnitudes when bounded, null when
 *   unbounded (long calls have unbounded upside; naked short calls have
 *   unbounded downside).
 */
export function legRisk(
  leg: OrderRiskLeg,
  legPremium: number,
  contracts: number,
): {
  maxLoss: number | null;
  maxGain: number | null;
  unboundedLoss: boolean;
  unboundedGain: boolean;
} {
  const premiumDollars = legPremium * contracts * MULTIPLIER;
  const intrinsicDollars = leg.strike * contracts * MULTIPLIER;

  if (leg.action === "BUY") {
    // Long option: paid the premium; loss capped at the debit; gain capped
    // by the strike for puts (stock → 0) and unbounded for calls.
    if (leg.right === "C") {
      return {
        maxLoss: premiumDollars,
        maxGain: null,
        unboundedLoss: false,
        unboundedGain: true,
      };
    }
    return {
      maxLoss: premiumDollars,
      // Clamp at 0: when `premiumDollars > intrinsicDollars` (paid more than
      // the strike-to-zero floor — out-of-money put with extreme premium),
      // the position can never profit at expiry. maxGain = 0, matching the
      // multi-leg path's `Math.max(0, …)` convention.
      maxGain: Math.max(0, intrinsicDollars - premiumDollars),
      unboundedLoss: false,
      unboundedGain: false,
    };
  }

  // SELL leg
  if (leg.right === "C") {
    // Naked short call: gain capped at premium, loss UNBOUNDED.
    return {
      maxLoss: null,
      maxGain: premiumDollars,
      unboundedLoss: true,
      unboundedGain: false,
    };
  }
  // Naked / cash-secured short put: gain capped at premium, loss at the
  // assignment-at-zero bound (strike × contracts × 100) minus premium.
  // Clamp at 0 to match the multi-leg path's `Math.max(0, ...)` convention.
  // When `premiumDollars > intrinsicDollars` (premium received exceeds the
  // strike-to-zero floor — an arb-only scenario in real markets, but the
  // function shouldn't return nonsensical negative maxLoss for fuzz inputs)
  // the position is structurally guaranteed-profit and `maxLoss = 0` is the
  // right reading. The 2026-05-26 fuzz suite caught this asymmetry between
  // the single-leg and multi-leg paths.
  return {
    maxLoss: Math.max(0, intrinsicDollars - premiumDollars),
    maxGain: premiumDollars,
    unboundedLoss: false,
    unboundedGain: false,
  };
}

/**
 * Optional transaction-cost adjustment. `roundTripCost` is the total dollar
 * cost of getting in AND out (commission + slippage + combo penalty for both
 * the entry and the exit). Compute it with `estimateRoundTripCost` from
 * `web/lib/order/costs.ts` — the shared source of truth mirrored in
 * `scripts/costs.py`.
 *
 * When supplied, bounded max-loss grows by the cost (you pay it on top of the
 * structural loss) and bounded max-gain shrinks by the cost (clamped at 0).
 * Unbounded legs stay unbounded — cost never bounds an infinite tail.
 */
export interface OrderCostAdjustment {
  roundTripCost: number;
}

/**
 * Classify a combo into "fully defined" vs "undefined risk", and pin down
 * the dollar max-loss when expressible.
 *
 * Defined structures the formula handles correctly:
 *   - Bull / Bear vertical spreads (call or put)
 *   - Long straddle / long strangle
 *   - Long butterfly
 *   - Iron condor (long wings cap each side)
 *
 * Undefined structures the formula surfaces correctly:
 *   - Risk reversal (short put + long call) → bounded only at stock-to-zero
 *   - Short straddle / short strangle → unbounded via the short call leg
 *   - Jade lizard (short put + short call spread) → unbounded via short put
 *     unless the short call wing is fully covered; bounded if the call
 *     side is a spread but flagged because of the naked put
 *
 * Net-premium semantics
 * ---------------------
 * `netPremium` is the per-combo net price as displayed in the order builder:
 * positive for a net debit, negative for a net credit. Multi-leg dollars are
 * the intrinsic payoff extrema at the strike kinks, net of `netPremium`.
 */
export function computeOrderRisk(
  legs: OrderRiskLeg[],
  netPremium: number,
  comboQuantity: number,
  costs?: OrderCostAdjustment,
): OrderRisk {
  return applyCostAdjustment(
    computeGrossOrderRisk(legs, netPremium, comboQuantity),
    costs,
  );
}

/**
 * Adjust a gross (cost-free) risk verdict to be NET of transaction cost.
 * Pure; a no-op when `costs` is absent so existing callers are unchanged.
 */
function applyCostAdjustment(
  gross: OrderRisk,
  costs: OrderCostAdjustment | undefined,
): OrderRisk {
  if (!costs || !Number.isFinite(costs.roundTripCost) || costs.roundTripCost <= 0) {
    return gross;
  }
  const cost = costs.roundTripCost;
  return {
    ...gross,
    maxLoss: gross.maxLoss == null ? null : gross.maxLoss + cost,
    maxGain: gross.maxGain == null ? null : Math.max(0, gross.maxGain - cost),
  };
}

function computeGrossOrderRisk(
  legs: OrderRiskLeg[],
  netPremium: number,
  comboQuantity: number,
): OrderRisk {
  if (legs.length === 0 || comboQuantity <= 0) {
    return {
      maxLoss: null,
      maxGain: null,
      maxLossUnbounded: false,
      maxGainUnbounded: false,
      hasUndefinedRisk: false,
      undefinedRiskReason: null,
    };
  }

  // Single leg path: defer to legRisk with per-share premium = abs(netPremium).
  // For single legs `netPremium` represents the per-share price of that leg.
  if (legs.length === 1) {
    const leg = legs[0];
    const contracts = comboQuantity * leg.quantity;
    const perContractPremium = Math.abs(netPremium);

    // Closing-trade coverage: SELL of N contracts against M held longs is a
    // close on the min(N, M) side. Only the (N - M) excess is naked.
    const nakedContracts = effectiveNakedContracts(leg, contracts);
    if (leg.action === "SELL" && nakedContracts === 0) {
      return {
        maxLoss: 0,
        maxGain: 0,
        maxLossUnbounded: false,
        maxGainUnbounded: false,
        hasUndefinedRisk: false,
        undefinedRiskReason: null,
      };
    }
    const exposureContracts = leg.action === "SELL" ? nakedContracts : contracts;
    const r = legRisk(leg, perContractPremium, exposureContracts);
    const undefinedReason = describeUndefinedLeg(leg, r.unboundedLoss);
    return {
      maxLoss: r.maxLoss,
      maxGain: r.maxGain,
      maxLossUnbounded: r.unboundedLoss,
      maxGainUnbounded: r.unboundedGain,
      hasUndefinedRisk: r.unboundedLoss || undefinedReason != null,
      undefinedRiskReason: undefinedReason,
    };
  }

  // Multi-leg: net out call coverage and put coverage so a long-call leg
  // can offset a short-call leg in the same combo (vertical / butterfly).
  // `legsAreBounded` matches shorts to longs of the same right by ratio and
  // expiry; the dollars come from the payoff at every strike kink below.

  const callLegs = legs.filter((l) => l.right === "C");
  const putLegs = legs.filter((l) => l.right === "P");

  const callBounded = legsAreBounded(callLegs);
  const putBounded = legsAreBounded(putLegs);

  // The unbounded-loss case: an uncovered short call. Even one is fatal.
  if (!callBounded.bounded) {
    return {
      maxLoss: null,
      maxGain: null,
      maxLossUnbounded: true,
      maxGainUnbounded: false,
      hasUndefinedRisk: true,
      undefinedRiskReason: callBounded.reason,
    };
  }

  // Long-side unbounded gain: any net-long position on the call side has
  // unbounded upside at S → ∞. Compute the net call ratio; > 0 means net
  // long and the gain is unbounded.
  const netCallRatio = callLegs.reduce(
    (sum, l) => sum + (l.action === "BUY" ? l.quantity : -l.quantity),
    0,
  );
  const maxGainUnbounded = netCallRatio > 0;

  // A calendar cannot be reduced to one expiration payoff. An earlier long
  // never caps a later short; a surviving later long can cap the tail, but
  // exact dollars still require a time-aware model or the broker what-if.
  const expiries = new Set(legs.map((leg) => normaliseExpiry(leg.expiry)));
  if (expiries.size > 1) {
    return {
      maxLoss: null,
      maxGain: null,
      maxLossUnbounded: false,
      maxGainUnbounded,
      hasUndefinedRisk: true,
      undefinedRiskReason: "Mixed-expiry risk requires broker margin",
    };
  }

  if (legs.some((leg) => leg.costBasisKnown === false)) {
    return {
      maxLoss: null,
      maxGain: null,
      maxLossUnbounded: false,
      maxGainUnbounded,
      hasUndefinedRisk: true,
      undefinedRiskReason: "Stock cost basis unavailable",
    };
  }

  // From here the call side is bounded for loss. Evaluate the aggregate
  // intrinsic payoff at every strike kink, where bounded extrema occur.
  const extrema = intrinsicPayoffExtrema(legs);
  const maxLossDollars = Math.max(
    0,
    (-extrema.min + netPremium) * comboQuantity * MULTIPLIER,
  );

  // Max gain (when bounded): same model symmetrically. If unbounded on
  // the call side we surface null.
  const maxGainDollars = maxGainUnbounded
    ? null
    : Math.max(
        0,
        (extrema.max - netPremium) * comboQuantity * MULTIPLIER,
      );

  // Undefined risk flag: naked short put bounds the trade only at
  // stock-to-zero. The number is real but it's a stress bound, not a
  // structural cap. Surface it to the user.
  const undefinedReason = putBounded.bounded ? null : putBounded.reason;

  return {
    maxLoss: maxLossDollars,
    maxGain: maxGainDollars,
    maxLossUnbounded: false,
    maxGainUnbounded,
    hasUndefinedRisk: undefinedReason != null,
    undefinedRiskReason: undefinedReason,
  };
}

/**
 * For a same-right leg group, return whether every short leg is fully
 * covered by a long leg of the same right.
 *
 * Call side: long calls (any strike) cap the upside. Short calls without
 * a long call cap are UNBOUNDED. We use total ratio, not per-strike — any
 * long call expiring on or after the short protects against an arbitrarily
 * large move, the question is how big the dollar loss is, not whether it's
 * unbounded.
 *
 * Put side: long puts cap the assignment risk. Short puts without a long
 * put cap drive max loss to strike-at-zero. That's a finite number, but
 * the structure is not "defined risk" — flag it.
 */
function legsAreBounded(sameRightLegs: OrderRiskLeg[]): {
  bounded: boolean;
  reason: string | null;
} {
  if (sameRightLegs.length === 0) return { bounded: true, reason: null };
  const right = sameRightLegs[0].right;
  const longs = sameRightLegs
    .filter((leg) => leg.action === "BUY")
    .map((leg) => ({ expiry: normaliseExpiry(leg.expiry), remaining: leg.quantity }))
    .sort((a, b) => a.expiry.localeCompare(b.expiry));
  const shorts = sameRightLegs
    .filter((leg) => leg.action === "SELL")
    .map((leg) => ({
      expiry: normaliseExpiry(leg.expiry),
      remaining: Math.max(0, leg.quantity - Math.max(0, leg.coveringLongContracts ?? 0)),
    }))
    .sort((a, b) => b.expiry.localeCompare(a.expiry));

  for (const short of shorts) {
    for (const long of longs) {
      if (short.remaining <= 0) break;
      if (long.remaining <= 0 || long.expiry < short.expiry) continue;
      const consumed = Math.min(short.remaining, long.remaining);
      short.remaining -= consumed;
      long.remaining -= consumed;
    }
  }

  if (shorts.every((short) => short.remaining <= 0)) {
    return { bounded: true, reason: null };
  }

  const rightLabel = right === "C" ? "call" : "put";
  return {
    bounded: false,
    reason: `Uncovered short ${rightLabel}`,
  };
}

/**
 * Signed expiry P&L range in dollars: best and worst outcome across every
 * settlement, NOT clamped at zero. A combo whose credit exceeds its width
 * has a positive worst case; one bought above its payoff has a negative
 * best case. Only meaningful for a bounded, single-expiry combo (callers
 * check `computeOrderRisk` first); the net-long-call tail is not modelled.
 */
export function signedPayoffRange(
  legs: OrderRiskLeg[],
  netPremium: number,
  comboQuantity: number,
): { best: number; worst: number } {
  const extrema = intrinsicPayoffExtrema(legs);
  return {
    best: (extrema.max - netPremium) * comboQuantity * MULTIPLIER,
    worst: (extrema.min - netPremium) * comboQuantity * MULTIPLIER,
  };
}

/** Exact same-expiry intrinsic extrema at every payoff kink. */
function intrinsicPayoffExtrema(legs: OrderRiskLeg[]): { min: number; max: number } {
  const points = [0, ...legs.map((leg) => leg.strike)].filter(
    (value, index, all) => Number.isFinite(value) && value >= 0 && all.indexOf(value) === index,
  );
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;

  for (const spot of points) {
    let payoff = 0;
    for (const leg of legs) {
      const intrinsic = leg.right === "C"
        ? Math.max(0, spot - leg.strike)
        : Math.max(0, leg.strike - spot);
      payoff += (leg.action === "BUY" ? 1 : -1) * intrinsic * leg.quantity;
    }
    min = Math.min(min, payoff);
    max = Math.max(max, payoff);
  }

  return {
    min: Number.isFinite(min) ? min : 0,
    max: Number.isFinite(max) ? max : 0,
  };
}

function describeUndefinedLeg(
  leg: OrderRiskLeg,
  unboundedLoss: boolean,
): string | null {
  if (unboundedLoss) return "Uncovered short call";
  if (leg.action === "SELL" && leg.right === "P") return "Naked short put";
  return null;
}

/**
 * For a SELL leg, return the number of contracts that are GENUINELY naked
 * after subtracting any covering LONG holdings of the same option. For BUY
 * legs and SELL legs without a `coveringLongContracts` hint this returns
 * the original contract count so behavior is unchanged for opening trades.
 *
 * Example: held 65 long $45 calls, selling 66 → naked excess = 1 contract.
 */
function effectiveNakedContracts(leg: OrderRiskLeg, contracts: number): number {
  if (leg.action !== "SELL") return contracts;
  const covering = Math.max(0, leg.coveringLongContracts ?? 0);
  return Math.max(0, contracts - covering);
}

/* ---------------------------------------------------------------------------
 * Portfolio-coverage augmentation
 *
 * The chain order builder lets the operator click any strike on the chain and
 * SELL it. Without portfolio awareness, the resulting leg is treated as a
 * standalone instrument by `computeOrderRisk`. For a SHORT CALL with no held
 * LONG call this is correct — UNBOUNDED is the right answer. But when the
 * operator already holds LONG calls of the SAME UNDERLYING and EXPIRY at a
 * different strike, the resulting position is a vertical spread (or partial
 * spread), and the risk model should see both legs.
 *
 * This helper transforms `ChainOrderLeg[]` into the convention expected by
 * `computeOrderRisk`:
 *
 *   - Chain leg quantity (raw user-entered contracts) is normalised to a
 *     per-combo ratio. `comboQuantity` carries the contract count.
 *   - For each SELL chain leg, the held LONG legs on the same ticker /
 *     expiry / right are injected as virtual BUY legs with ratio
 *     `held_contracts / comboQuantity`. Coverage is capped at the chain
 *     leg's contract count — additional held longs above that beyond don't
 *     belong inside the resulting-position model (they remain a separately
 *     held instrument and would inflate Max Gain via long-call unbounded
 *     upside).
 *   - Fractional ratios are deliberate. legsAreBounded compares the
 *     numeric ratios; partial coverage surfaces as longRatio less than
 *     shortRatio, which resolves to UNBOUNDED — the correct answer when
 *     N held longs only cover N of M shorts.
 *
 * Why not generalise `OrderRiskLeg.coveringLongContracts`? That field models
 * SAME-OPTION coverage (close-out semantics: SELL of contracts you already
 * hold short-circuits to maxLoss = 0). Spread coverage at a different strike
 * isn't a close — it's a structural transformation, and is most cleanly
 * expressed as a multi-leg combo. The single-leg branch keeps its existing
 * semantics; this helper just hands a different shape to the multi-leg path
 * when coverage exists.
 *
 * Expiry normalisation: portfolio positions store expiry as "YYYY-MM-DD"
 * while chain legs use "YYYYMMDD". `normaliseExpiry` strips dashes and is
 * tolerant of either input.
 * -------------------------------------------------------------------------*/

function normaliseExpiry(value: string): string {
  return value.replace(/-/g, "");
}

function gcdInt(a: number, b: number): number {
  let x = Math.abs(Math.trunc(a));
  let y = Math.abs(Math.trunc(b));
  while (y !== 0) {
    [x, y] = [y, x % y];
  }
  return x || 1;
}

function comboUnitFromQuantities(quantities: number[]): number {
  if (quantities.length === 0) return 1;
  return quantities.reduce((acc, q) => gcdInt(acc, q));
}

/**
 * Build the risk-leg representation for a chain-built order, augmented with
 * any portfolio coverage. See block comment above for the full semantics.
 *
 * Coverage precedence per SELL chain leg:
 *   1. Held LONG option (same ticker / expiry / right, any strike).
 *      Pairing N short ↔ N long composes a vertical spread (bull/bear call
 *      or put). Bounded loss via the multi-leg risk model.
 *   2. Held LONG stock — only for SELL CALL legs. Each 100 shares cover
 *      one short call as a covered call. Modelled by injecting a virtual
 *      `BUY C strike=0` leg (stock = "long call at strike 0") AND folding
 *      the stock's avg_cost into `netPremiumAdjustment` so the structure's
 *      max-loss bottoms at stock-to-zero net of premium received, not at $0.
 *
 * SELL PUT coverage by stock is intentionally NOT modelled — long stock
 * does not cap a short put obligation (the short put owes BUYING more
 * stock at strike, which long stock does not satisfy).
 *
 * @param chainLegs  Raw chain order legs (per-leg quantity is the user-entered contract count).
 * @param ticker     Underlying symbol — used to filter portfolio positions.
 * @param portfolio  Live portfolio snapshot or null. When null, the helper still
 *                   normalises chain leg quantities to per-combo ratios so the
 *                   single-leg quantity² bug stays fixed even without coverage.
 * @returns          `{ riskLegs, comboQuantity, coveringLegs, netPremiumAdjustment,
 *                   heldOptionBasisAdjustment }`.
 */
export function augmentOrderLegsWithPortfolioCoverage(
  chainLegs: ChainOrderLeg[],
  ticker: string,
  portfolio: PortfolioData | null,
): AugmentedOrderLegs {
  if (chainLegs.length === 0) {
    return { riskLegs: [], comboQuantity: 1, coveringLegs: [], netPremiumAdjustment: 0, heldOptionBasisAdjustment: 0 };
  }

  const quantities = chainLegs.map((l) => Math.max(1, Math.trunc(l.quantity)));
  const comboQuantity = comboUnitFromQuantities(quantities);

  const riskLegs: OrderRiskLeg[] = chainLegs.map((leg, i) => ({
    action: leg.action,
    right: leg.right,
    strike: leg.strike,
    expiry: leg.expiry,
    quantity: quantities[i] / comboQuantity,
  }));

  const coveringLegs: CoveringPortfolioLeg[] = [];
  if (!portfolio) {
    return { riskLegs, comboQuantity, coveringLegs, netPremiumAdjustment: 0, heldOptionBasisAdjustment: 0 };
  }

  // Per-target index of held LONG OPTION contracts: key = expiry|right|strike.
  type HeldKey = string;
  const heldLongIndex = new Map<
    HeldKey,
    { contracts: number; strike: number; right: LegRight; expiry: string; basisDollars: number | null }
  >();
  // Aggregate LONG stock contracts on this ticker, weighted by avg_cost.
  // Multiple lots / positions are pooled because stock is fungible.
  let longStockShares = 0;
  let longStockBasisDollars = 0;
  let longStockBasisKnown = true;
  const heldShorts: Array<{ contracts: number; right: LegRight; expiry: string }> = [];
  for (const pos of portfolio.positions ?? []) {
    if (pos.ticker.toUpperCase() !== ticker.toUpperCase()) continue;
    for (const leg of pos.legs ?? []) {
      if (leg.contracts <= 0) continue;
      if (leg.direction === "SHORT" && (leg.type === "Call" || leg.type === "Put")) {
        const effectiveExpiry = leg.expiry ?? pos.expiry;
        const expiry = effectiveExpiry ? normaliseExpiry(effectiveExpiry) : "";
        if (expiry) {
          heldShorts.push({
            contracts: leg.contracts,
            right: leg.type === "Call" ? "C" : "P",
            expiry,
          });
        }
        continue;
      }
      if (leg.direction !== "LONG") continue;
      if (leg.type === "Stock") {
        // Stocks don't have expiry / strike. avg_cost is per-share.
        const avgCost = Number.isFinite(leg.avg_cost) ? leg.avg_cost : 0;
        if (!Number.isFinite(leg.avg_cost)) longStockBasisKnown = false;
        if (avgCost < 0) continue;
        longStockShares += leg.contracts;
        longStockBasisDollars += leg.contracts * avgCost;
        continue;
      }
      if (leg.type !== "Call" && leg.type !== "Put") continue;
      const effectiveExpiry = leg.expiry ?? pos.expiry;
      const expiry = effectiveExpiry ? normaliseExpiry(effectiveExpiry) : "";
      if (!expiry) continue;
      if (leg.strike == null || !Number.isFinite(leg.strike) || leg.strike <= 0) continue;
      const right: LegRight = leg.type === "Call" ? "C" : "P";
      const key: HeldKey = `${expiry}|${right}|${leg.strike}`;
      const prior = heldLongIndex.get(key);
      const legBasis = Number.isFinite(leg.entry_cost) && leg.entry_cost >= 0 ? leg.entry_cost : null;
      heldLongIndex.set(key, {
        contracts: (prior?.contracts ?? 0) + leg.contracts,
        strike: leg.strike,
        right,
        expiry,
        basisDollars:
          prior !== undefined && prior.basisDollars == null
            ? null
            : legBasis == null
              ? null
              : (prior?.basisDollars ?? 0) + legBasis,
      });
    }
  }

  // --- Pass 0: the order's own BUY legs cover its SELL legs ----------------
  // A self-contained spread (BUY 10x 135C + SELL 10x 155C) needs NO external
  // coverage — the multi-leg risk model already prices the pair as bounded.
  // Injecting a held long on top of an order-internal cover double-counts
  // and flips the structure to net-long → phantom "UNBOUNDED" max gain
  // (SPCX repro 2026-07-22). Held positions may only cover the residue.
  // Strikes are irrelevant for tail-boundedness: any 1:1 long-vs-short of
  // the same right and expiry cancels the tail slope.
  const orderBuyPool = new Map<string, number>();
  for (let i = 0; i < chainLegs.length; i++) {
    const leg = chainLegs[i];
    if (leg.action !== "BUY") continue;
    const key = `${leg.right}|${normaliseExpiry(leg.expiry)}`;
    orderBuyPool.set(key, (orderBuyPool.get(key) ?? 0) + quantities[i]);
  }

  // --- Pass 1: option coverage ---------------------------------------------
  // For each SELL chain leg, draw down covering held LONG option contracts of
  // the same expiry and right. Coverage is capped at the chain leg's contract
  // count so we don't inject more longs than the short can pair with.
  //
  // Multiple SELL chain legs at the same right share a single coverage pool
  // — a held long can only cover one short at a time. We consume in order
  // of the chain legs list.
  const remainingByKey = new Map<HeldKey, number>();
  for (const [k, v] of heldLongIndex) remainingByKey.set(k, v.contracts);

  // Existing short obligations consume collateral before a new order can use
  // it. Options cover the same right through their expiry; remaining short
  // calls consume 100 shares each. This prevents one holding from backing two
  // simultaneous shorts.
  let encumberedStockShares = 0;
  for (const short of heldShorts) {
    let remaining = short.contracts;
    for (const [key, held] of heldLongIndex.entries()) {
      if (remaining <= 0) break;
      if (held.right !== short.right || held.expiry < short.expiry) continue;
      const available = remainingByKey.get(key) ?? 0;
      const consumed = Math.min(available, remaining);
      remainingByKey.set(key, available - consumed);
      remaining -= consumed;
    }
    if (short.right === "C" && remaining > 0) {
      encumberedStockShares += remaining * MULTIPLIER;
    }
  }

  // Track per-leg remaining naked short contracts after option coverage. Used
  // by pass 2 to apply stock coverage to the residue.
  const remainingShortByLeg: number[] = quantities.slice();
  let heldOptionBasisDollars: number | null = 0;

  for (let i = 0; i < chainLegs.length; i++) {
    const chain = chainLegs[i];
    if (chain.action !== "SELL") continue;
    const chainExpiry = normaliseExpiry(chain.expiry);
    let remainingShort = quantities[i];

    const poolKey = `${chain.right}|${chainExpiry}`;
    const pooled = orderBuyPool.get(poolKey) ?? 0;
    const selfCovered = Math.min(pooled, remainingShort);
    if (selfCovered > 0) {
      orderBuyPool.set(poolKey, pooled - selfCovered);
      remainingShort -= selfCovered;
    }

    for (const [key, leg] of heldLongIndex.entries()) {
      if (remainingShort <= 0) break;
      if (leg.right !== chain.right) continue;
      if (leg.expiry !== chainExpiry) continue;
      const available = remainingByKey.get(key) ?? 0;
      if (available <= 0) continue;
      const consumed = Math.min(available, remainingShort);
      remainingByKey.set(key, available - consumed);
      remainingShort -= consumed;
      // Basis scales linearly with the share of the held lot consumed.
      const consumedBasis =
        leg.basisDollars == null ? null : (leg.basisDollars * consumed) / leg.contracts;
      heldOptionBasisDollars =
        heldOptionBasisDollars == null || consumedBasis == null
          ? null
          : heldOptionBasisDollars + consumedBasis;
      coveringLegs.push({
        type: "Option",
        right: leg.right,
        strike: leg.strike,
        expiry: leg.expiry,
        contracts: consumed,
        entryCostDollars: consumedBasis,
      });
      riskLegs.push({
        action: "BUY",
        right: leg.right,
        strike: leg.strike,
        expiry: leg.expiry,
        quantity: consumed / comboQuantity,
      });
    }
    remainingShortByLeg[i] = remainingShort;
  }

  // --- Pass 2: stock coverage for SELL CALL legs ---------------------------
  // Each 100 shares of held LONG stock cover one short call (covered-call
  // semantics). We:
  //   - cap consumption at exactly (remaining_short_calls × 100) per leg so
  //     extra shares stay outside the model (otherwise they'd inflate the
  //     long-call ratio and flip the structure to "unbounded gain", which
  //     is true of the held shares but not of THIS order's structure).
  //   - inject a virtual `BUY C strike=0` leg whose ratio = shares / (100 ×
  //     comboQuantity). Strike=0 is mathematically equivalent to long stock
  //     (intrinsic = max(0, S - 0) = S), and `intrinsicPayoffExtrema`
  //     evaluates it like any other leg.
  //   - fold the stock's per-share cost basis into `netPremiumAdjustment`
  //     so the synthetic combo's net debit reflects sunk basis. Without
  //     this, the model would say "long stock at $0 + short call" → max
  //     loss = 0 (free stock can't lose), which is wrong.
  //
  // Weighted avg cost: longStockBasisDollars / longStockShares. We consume
  // a fraction of shares; cost basis scales linearly with shares consumed.
  let netPremiumAdjustment = 0;
  let remainingShares = Math.max(0, longStockShares - encumberedStockShares);
  const stockAvgCost = longStockShares > 0 ? longStockBasisDollars / longStockShares : 0;

  for (let i = 0; i < chainLegs.length; i++) {
    const chain = chainLegs[i];
    if (chain.action !== "SELL") continue;
    if (chain.right !== "C") continue;
    const remainingShort = remainingShortByLeg[i];
    if (remainingShort <= 0) continue;
    const sharesNeeded = remainingShort * 100;
    const sharesConsumed = Math.min(remainingShares, sharesNeeded);
    if (sharesConsumed <= 0) continue;
    remainingShares -= sharesConsumed;

    // Inject virtual long call at strike=0 (stock equivalent).
    // ratio = sharesConsumed / 100 / comboQuantity gives the per-combo unit:
    // for 100 shares, 1 combo (1 short call), ratio = 1 (one call's worth).
    riskLegs.push({
      action: "BUY",
      right: "C",
      strike: 0,
      expiry: chain.expiry,
      quantity: sharesConsumed / 100 / comboQuantity,
      costBasisKnown: longStockBasisKnown,
    });

    // Cost basis per-share-per-combo: sharesConsumed × avgCost gives total
    // dollars; divide by (comboQuantity × MULTIPLIER) to express in the
    // per-share-per-combo unit that `netPremium` uses.
    netPremiumAdjustment += (sharesConsumed * stockAvgCost) / (comboQuantity * MULTIPLIER);

    coveringLegs.push({
      type: "Stock",
      shares: sharesConsumed,
      avgCost: stockAvgCost,
    });
  }

  const heldOptionBasisAdjustment =
    heldOptionBasisDollars == null ? null : heldOptionBasisDollars / (comboQuantity * MULTIPLIER);

  return { riskLegs, comboQuantity, coveringLegs, netPremiumAdjustment, heldOptionBasisAdjustment };
}
