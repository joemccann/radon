/**
 * Phase-1 initial-margin estimator — pure, module-private.
 *
 * Every order surface routes through `useOrderRisk`, so this estimator runs in
 * ONE place and every surface inherits an "impact to available margin"
 * read-out. Phase 1 is an INSTANT client-side estimate; Phase 2 (an IB
 * `whatIf` backend round-trip, source `'ib-whatif'`) is a separate follow-up
 * and is NOT built here.
 *
 * Why this exists separately from `maxLoss`
 * ----------------------------------------
 * `maxLoss` is the assignment-at-zero STRESS bound. For a DEFINED-RISK
 * structure that bound IS the Reg-T margin requirement, so we use it directly.
 * But for a NAKED / undefined-risk SHORT it massively overstates real margin —
 * e.g. a 15× MU 850P naked put has `maxLoss ≈ $1.275M` (strike×100×qty) while
 * the actual Reg-T initial requirement is ~$138k. Rendering `maxLoss` as the
 * margin requirement would alarm the operator with a number that is an order
 * of magnitude wrong. For those we compute the Reg-T broker estimate below.
 *
 * Like its siblings under `internal/`, ESLint blocks importing this module
 * from app code. Tests reach it via `lib/order/risk/__test_only__.ts`.
 */

// "ib-whatif" is set ONLY by the Phase-2 async merge (mergeWhatIfMargin), never
// by this synchronous estimator — what-if bypasses the estimator entirely.
export type MarginEstimateSource = "exact-maxloss" | "regt-estimate" | "ib-whatif";

export interface MarginEstimate {
  /** Dollar initial-margin requirement, or null when not estimable. */
  requirement: number | null;
  source: MarginEstimateSource;
  /** True when `requirement` is a Reg-T approximation rather than an exact bound. */
  approximate: boolean;
}

const MULTIPLIER = 100;

/**
 * Discriminated argument shape. The caller (`useOrderRisk`) already knows the
 * structural classification, so it picks the right `kind` rather than this
 * module re-deriving it from raw legs.
 */
export type MarginEstimateArgs =
  | {
      /**
       * Fully defined-risk structure (spread, long debit, cash buy). Reg-T
       * margin equals the position's max loss.
       */
      kind: "defined-risk";
      /** Bounded max-loss magnitude in dollars. */
      maxLoss: number | null;
    }
  | {
      /**
       * Long debit / cash buy: long single option (premium-only),
       * BUY stock (cash), cash-secured put. The dollar cash outlay IS the
       * requirement (margin is the cash, no extra requirement).
       */
      kind: "long-debit";
      /** Signed or unsigned total cost; magnitude is used. */
      totalCost: number | null;
    }
  | {
      /** Naked SHORT option (put or call). Reg-T broker estimate. */
      kind: "naked-option";
      right: "C" | "P";
      /** Per-contract strike. */
      strike: number;
      /** Underlying spot. When null/non-finite, requirement is null (no guess). */
      spot: number | null;
      /** Contract count (positive). */
      quantity: number;
      /** Per-share premium magnitude received. */
      premiumPerShare: number;
    }
  | {
      /** SHORT stock — Reg-T initial is 50% of notional. */
      kind: "short-stock";
      price: number;
      quantity: number;
    }
  | {
      /**
       * SHORT linear future (UNBOUNDED). No reliable Reg-T initial from the
       * client; requirement is null (renders "unavailable"). NEVER derive it
       * from the unbounded maxLoss.
       */
      kind: "short-future-unbounded";
    }
  | {
      /** Pure close-out — consumes no margin. */
      kind: "close-out";
    }
  | {
      /**
       * Short call(s) fully covered by held LONG stock (a covered call
       * against an existing position). Reg-T: the held shares ARE the
       * collateral, so the short call adds NO new margin requirement. The
       * synthetic buy-write's maxLoss (stock-to-zero) is pre-existing stock
       * risk, NOT margin consumed by this order — rendering it as the
       * requirement was the EWY 2026-07-21 bug ($415k req for a covered
       * call).
       */
      kind: "stock-covered-call";
    };

/**
 * Estimate the Phase-1 initial-margin requirement for an order. Pure.
 */
export function estimateInitialMargin(args: MarginEstimateArgs): MarginEstimate {
  switch (args.kind) {
    case "close-out":
      return { requirement: 0, source: "exact-maxloss", approximate: false };

    case "stock-covered-call":
      return { requirement: 0, source: "regt-estimate", approximate: false };

    case "defined-risk":
      return {
        requirement: args.maxLoss != null && Number.isFinite(args.maxLoss) ? args.maxLoss : null,
        source: "exact-maxloss",
        approximate: false,
      };

    case "long-debit":
      return {
        requirement:
          args.totalCost != null && Number.isFinite(args.totalCost)
            ? Math.abs(args.totalCost)
            : null,
        source: "exact-maxloss",
        approximate: false,
      };

    case "short-stock":
      return {
        requirement: 0.5 * Math.abs(args.price * args.quantity),
        source: "regt-estimate",
        approximate: true,
      };

    case "short-future-unbounded":
      return { requirement: null, source: "regt-estimate", approximate: true };

    case "naked-option":
      return estimateNakedOption(args);
  }
}

function estimateNakedOption(args: {
  right: "C" | "P";
  strike: number;
  spot: number | null;
  quantity: number;
  premiumPerShare: number;
}): MarginEstimate {
  const { right, strike, spot, quantity, premiumPerShare } = args;
  if (spot == null || !Number.isFinite(spot)) {
    return { requirement: null, source: "regt-estimate", approximate: true };
  }
  // Reg-T naked-option initial: 20% of underlying less out-of-the-money
  // amount, floored at 10% of strike, per share, plus premium received.
  const otmAmount = right === "P" ? Math.max(0, strike - spot) : Math.max(0, spot - strike);
  const perShare = Math.max(0.2 * spot - otmAmount, 0.1 * strike);
  const requirement =
    Math.max(0, perShare) * MULTIPLIER * quantity +
    Math.abs(premiumPerShare * quantity * MULTIPLIER);
  return { requirement, source: "regt-estimate", approximate: true };
}
