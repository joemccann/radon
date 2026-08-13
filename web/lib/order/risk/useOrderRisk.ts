"use client";

/**
 * useOrderRisk — the ONLY public way to produce an `AugmentedOrderSummary`.
 *
 * Every order-entry surface in the app routes through this hook (typically
 * via `<OrderRiskGate>`, which pairs the hook with `<OrderConfirmSummary>`).
 * Direct use of `computeOrderRisk` / `augmentOrderLegsWithPortfolioCoverage`
 * is ESLint-banned outside `lib/order/risk/internal/` precisely so that
 * portfolio-aware augmentation is not bypassable by a new surface.
 *
 * Inputs in: ticker, chain legs, net premium, descriptive labels, and the
 * portfolio snapshot. Output: `OrderRiskState` carrying a branded
 * `AugmentedOrderSummary` ready to hand to `<OrderConfirmSummary>`.
 *
 * Coverage status semantics:
 *   - `resolved`     — portfolio was provided and augmentation ran. Risk
 *                      numbers reflect the resulting portfolio + order
 *                      structure.
 *   - `pending`      — `portfolio === undefined`. Likely the parent has not
 *                      finished fetching. Render a skeleton + disable submit.
 *   - `no-portfolio` — `portfolio === null` was passed explicitly. The
 *                      surface intentionally has no portfolio context (or
 *                      forgot to thread it). Same UI treatment as pending.
 *
 * The hook does NOT call `usePortfolio()` directly because there is no
 * single PortfolioContext yet (a follow-up step). Until that lands, every
 * caller passes `portfolio` explicitly — but the brand + lint rule still
 * prevent the bug class because a surface that forgets to pass portfolio
 * gets a "Coverage indeterminate" skeleton instead of a wrong dollar number.
 */

import { useMemo } from "react";
import type { PortfolioData } from "@/lib/types";
import {
  type AugmentedOrderSummary,
  type CoverageStatus,
  type OrderPresentationSummary,
  ORDER_RISK_BRAND,
} from "../types";
import {
  augmentOrderLegsWithPortfolioCoverage,
  computeOrderRisk,
  type ChainOrderLeg,
  type CoveringPortfolioLeg,
  type OrderRisk,
} from "./internal/computeOrderRisk";
import { computeLinearRisk } from "./internal/computeLinearRisk";
import {
  estimateInitialMargin,
  type MarginEstimate,
} from "./internal/marginEstimate";
import { estimateRoundTripCost } from "../costs";
import type { RiskBudgetReport } from "@/lib/correlationRiskBanner";

export type { ChainOrderLeg, CoveringPortfolioLeg };

/**
 * Option-flavoured input — the original (and most common) shape. The chain
 * builder, OrderTab forms, InstrumentDetailModal, MobileOrderTicket,
 * IndexOptionOrderForm, and ModifyOrderModal all hand this shape in. The
 * augmenter walks `chainLegs` for portfolio coverage; `netPremium` flows
 * into `computeOrderRisk`.
 *
 * `type` is optional for backwards compatibility — surfaces that don't set
 * it are treated as option orders. New code should set `type: "options"`
 * explicitly so the discriminator is readable at the call site.
 */
export interface OptionOrderRiskInput {
  type?: "options";
  /** Underlying symbol. Drives portfolio filtering. */
  ticker: string;
  /**
   * Chain order legs in their raw user-entered form. Single-leg orders pass
   * `[{...}]`. Multi-leg combos pass each leg. Quantities are total
   * contracts; the augmenter normalises to per-combo ratios internally.
   */
  chainLegs: ChainOrderLeg[];
  /**
   * Per-share, signed net premium of the order: positive for net debit,
   * negative for net credit. Augmentation may add `netPremiumAdjustment`
   * (e.g. for stock-backed covered calls); the caller does NOT do this
   * — `useOrderRisk` folds it in.
   */
  netPremium: number;
  /** Human-readable order description, surfaced verbatim in the summary. */
  description: string;
  /**
   * Order's notional cash flow as displayed to the operator. Sign matches
   * the chain's user input (positive = debit; negative = credit). This is
   * the operator-visible "Total" field — it stays unmodified even when
   * `netPremiumAdjustment` non-zero (the adjustment affects risk math
   * only, not the displayed cash flow).
   */
  totalCost: number | null;
  /** Override "Total:" label (e.g. "Proceeds:", "Close Credit:"). */
  totalLabel?: string;
  /**
   * Close-out economics. `chainLegs` must still identify every exact closing
   * contract so the hook can project retained portfolio risk before treating
   * the order as a pure close. `estimatedPnl` is computed from cash flow minus
   * sunk basis. Used by close paths in OrderTab and InstrumentDetailModal.
   */
  closeOut?: {
    entryCostDollars: number;
    estimatedPnlLabel?: string;
  } | null;
  /**
   * Optional breakeven price to surface in the summary (e.g. for long
   * options). Not computed by the risk model.
   */
  breakeven?: number | null;
  /**
   * Optional live entry quote for the order as a whole (the signed NET combo
   * quote for multi-leg, or the positive single option quote for single-leg).
   * When supplied, `useOrderRisk` runs the F1 cost
   * model (`estimateRoundTripCost`) and folds the round-trip cost into the
   * risk verdict: bounded max-loss grows by the cost, bounded max-gain shrinks
   * by it (clamped at 0). Unbounded legs stay unbounded.
   *
   * Absent (the historical default) → no cost adjustment; behavior is
   * byte-for-byte identical to before FU7. A null bid/ask falls back to the
   * estimated half-spread inside the cost model rather than disabling cost.
   */
  quote?: { bid: number | null; ask: number | null } | null;
  /**
   * Already-resolved underlying spot for the order's symbol. Used ONLY by the
   * Phase-1 margin estimator for naked single-leg shorts (Reg-T initial =
   * 20% spot less OTM, floored at 10% strike). The surface threads in the
   * spot it already has (`prices[ticker].last`, or the forward-priced
   * underlying for VIX); `null` when the surface has no spot. When null on a
   * naked short, the margin requirement renders "unavailable" rather than a
   * guess. Has no effect on max-loss/max-gain math.
   */
  underlyingSpot?: number | null;
}

/**
 * Linear-instrument input — futures and stock. Linear instruments have NO
 * strike, NO expiry-as-option-input, and a linear payoff shape:
 *
 *   - LONG: max-loss = price × qty × multiplier (price-to-zero stress);
 *           max-gain = UNBOUNDED.
 *   - SHORT: max-loss = UNBOUNDED (no price ceiling); max-gain = price ×
 *           qty × multiplier (counterparty buys back at 0).
 *
 * Held-quantity close-out: SELL of N units with held LONG ≥ N is a pure
 * close. Reports proceeds + realised P&L from `closeOut.entryCostDollars`,
 * same contract as the option close-out branch. SELL with held < N is
 * partial close + naked-excess UNBOUNDED. BUY of N units with held SHORT ≥ N
 * is a buy-to-close (mirror logic).
 *
 * `multiplier` semantics:
 *   - Stock: 1 (the unit IS the share).
 *   - Futures: contract multiplier (VIX=1000, ES=50, MNQ=2). Stored on the
 *     IB futures contract metadata; the caller looks it up.
 */
export interface LinearOrderRiskInput {
  type: "linear";
  /** Underlying symbol. Drives portfolio filtering (held stock / held futures). */
  ticker: string;
  /** Direction. SELL is the UNBOUNDED branch for opening linear positions. */
  action: "BUY" | "SELL";
  /** Per-unit count: shares for stock, contracts for futures. */
  quantity: number;
  /** Per-unit signed price. Positive number; sign is encoded by `action`. */
  limitPrice: number;
  /** Contract multiplier: 1 for stock, instrument-specific for futures. */
  multiplier: number;
  /** Tag for telemetry + chip-rendering branch ("stock" vs "future"). */
  instrument: "stock" | "future";
  /** Human-readable order description, surfaced in the summary. */
  description: string;
  /**
   * Held LONG quantity of this instrument on the same ticker. For stock, the
   * total shares held LONG. For futures, the total contracts held LONG on
   * the same contract (rare). Drives close-out detection: SELL with
   * `heldQuantity >= quantity` reports as a close instead of opening a
   * naked short.
   */
  heldQuantity?: number;
  /**
   * Held SHORT quantity. Mirror of `heldQuantity` for the BUY-to-close case
   * (covering an existing short stock or short futures position).
   */
  heldShortQuantity?: number;
  /**
   * Close-out economics. Required when the action is closing a held
   * position (SELL against LONG OR BUY against SHORT). Provides cost basis
   * so the summary can report realised P&L.
   */
  closeOut?: {
    entryCostDollars: number;
    estimatedPnlLabel?: string;
  } | null;
}

/**
 * Top-level discriminated input. `useOrderRisk` and `<OrderRiskGate>` accept
 * either shape; the discriminator routes internally.
 *
 * Backwards compat: pre-existing call sites pass the OptionOrderRiskInput
 * shape without `type`. They continue to work; the hook treats absent
 * `type` as `"options"`.
 */
export type OrderRiskInput = OptionOrderRiskInput | LinearOrderRiskInput;

export interface OrderRiskState {
  /** Ready to pass to `<OrderConfirmSummary>`. Always branded. */
  summary: AugmentedOrderSummary;
  /** Convenience accessor — same as `summary.coverageStatus`. */
  coverageStatus: CoverageStatus;
  /**
   * True iff coverage is fully resolved. Surfaces use this to gate the
   * submit button: pending / no-portfolio coverage hard-blocks (the risk
   * numbers are indeterminate). An UNBOUNDED / undefined-risk verdict does
   * NOT block — Gate 4 (no naked shorts) was disabled 2026-04-30 and Gate 1
   * is advisory, rendered as a warning by `<OrderConfirmSummary>`.
   */
  okToSubmit: boolean;
  /** Coverage entries injected, exposed for chip rendering. */
  coveringLegs: CoveringPortfolioLeg[];
  /** Canonical Gate-3 correlation report carried by the portfolio snapshot. */
  correlationReport: RiskBudgetReport | null;
  /** Distinguishes a pending snapshot from an unavailable correlation measurement. */
  correlationStatus: "pending" | "unavailable" | "resolved";
  /** Stable identity of the portfolio inputs material to broker margin. */
  portfolioRevision: string;
}

function buildPortfolioRevision(
  portfolio: PortfolioData | null | undefined,
): string {
  if (portfolio === undefined) return "pending";
  if (portfolio === null) return "no-portfolio";

  const account = portfolio.account_summary;
  const positions = portfolio.positions
    .map((position) => {
      const legs = position.legs
        .map((leg) => [
          leg.con_id ?? "",
          leg.expiry ?? position.expiry,
          leg.type,
          leg.strike ?? "",
          leg.direction,
          leg.contracts,
        ].join(":"))
        .sort()
        .join(",");
      return [
        position.account_id ?? "",
        position.position_instance_id ?? position.id,
        position.ticker,
        position.direction,
        position.contracts,
        legs,
      ].join("|");
    })
    .sort()
    .join(";");

  return [
    portfolio.last_sync,
    account?.initial_margin ?? "",
    account?.maintenance_margin ?? "",
    account?.available_funds ?? "",
    account?.buying_power ?? "",
    positions,
  ].join("::");
}

function makeTraceId(): string {
  // crypto.randomUUID() is widely supported (Node 19+, all evergreen
  // browsers). Fallback for very old SSR contexts.
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID().slice(0, 8);
  }
  return Math.random().toString(36).slice(2, 10);
}

function brand(
  summary: OrderPresentationSummary,
  coverageStatus: CoverageStatus,
  traceId: string,
): AugmentedOrderSummary {
  return {
    ...summary,
    [ORDER_RISK_BRAND]: "augmented",
    coverageStatus,
    traceId,
  } as AugmentedOrderSummary;
}

type MarginImpact = NonNullable<OrderPresentationSummary["marginImpact"]>;

/**
 * Resolve the operator-visible margin baseline from the account summary.
 * Prefers AvailableFunds; falls back to BuyingPower. Returns null + a
 * coherent label when neither is present.
 */
function resolveMarginBaseline(
  portfolio: PortfolioData | null,
): { availableBefore: number | null; baselineLabel: string } {
  const account = portfolio?.account_summary;
  if (account && Number.isFinite(account.available_funds)) {
    return { availableBefore: account.available_funds as number, baselineLabel: "Available Funds" };
  }
  if (account && Number.isFinite(account.buying_power)) {
    return { availableBefore: account.buying_power, baselineLabel: "Buying Power" };
  }
  return { availableBefore: null, baselineLabel: "Available Funds" };
}

/**
 * Compose the Phase-1 `marginImpact` field from a margin estimate + the
 * portfolio baseline. Returns null (NEVER a $0 baseline) when coverage is not
 * resolved or no account summary is present, so the UI omits the rows rather
 * than rendering a misleading zero.
 */
function buildMarginImpact(
  estimate: MarginEstimate,
  portfolio: PortfolioData | null,
  coverageStatus: CoverageStatus,
): MarginImpact | null {
  if (coverageStatus !== "resolved") return null;
  if (portfolio?.account_summary == null) return null;
  const { availableBefore, baselineLabel } = resolveMarginBaseline(portfolio);
  if (availableBefore == null) return null;
  const requirement = estimate.requirement;
  const availableAfter = requirement != null ? availableBefore - requirement : null;
  return {
    requirement,
    availableBefore,
    availableAfter,
    baselineLabel,
    source: estimate.source,
    approximate: estimate.approximate,
  };
}

/**
 * Margin estimate for a freshly-opened linear order (no close-out).
 *   - BUY stock → cash buy (`Math.abs(totalCost)`).
 *   - SELL stock → Reg-T 50% of notional.
 *   - SHORT future → UNBOUNDED → null requirement (never the unbounded maxLoss).
 *   - LONG future → bounded maxLoss (price-to-zero × mult × qty) is the
 *     conservative defined-risk requirement.
 */
function estimateLinearMargin(
  input: LinearOrderRiskInput,
  maxLoss: number | null,
  maxLossUnbounded: boolean,
): MarginEstimate {
  const notional = Math.abs(input.limitPrice * input.quantity * input.multiplier);
  if (input.instrument === "stock") {
    return input.action === "SELL"
      ? estimateInitialMargin({ kind: "short-stock", price: input.limitPrice, quantity: input.quantity })
      : estimateInitialMargin({ kind: "long-debit", totalCost: notional });
  }
  // Future
  if (maxLossUnbounded) {
    return estimateInitialMargin({ kind: "short-future-unbounded" });
  }
  return estimateInitialMargin({ kind: "defined-risk", maxLoss });
}

/**
 * A stock sale can be a pure linear close while still increasing portfolio
 * risk by removing collateral from retained short calls. Long calls of the
 * same expiry absorb short-call tail risk first; remaining shorts consume
 * 100 long shares each. The order stays stock-only, but its confirmation must
 * surface any newly uncovered calls instead of reporting a $0 margin impact.
 */
function retainedShortCallRiskAfterStockClose(
  input: LinearOrderRiskInput,
  portfolio: PortfolioData | null,
): string | null {
  if (
    portfolio == null ||
    input.instrument !== "stock" ||
    input.action !== "SELL" ||
    input.quantity <= 0
  ) {
    return null;
  }

  let heldLongShares = 0;
  const callsByExpiry = new Map<string, { long: number; short: number }>();

  for (const position of portfolio.positions) {
    if (position.ticker.toUpperCase() !== input.ticker.toUpperCase()) continue;
    for (const leg of position.legs) {
      if (leg.type === "Stock" && leg.direction === "LONG") {
        heldLongShares += leg.contracts;
        continue;
      }
      if (leg.type !== "Call") continue;
      const bucket = callsByExpiry.get(position.expiry) ?? { long: 0, short: 0 };
      bucket[leg.direction === "LONG" ? "long" : "short"] += leg.contracts;
      callsByExpiry.set(position.expiry, bucket);
    }
  }

  const shortCallsNeedingStock = [...callsByExpiry.values()].reduce(
    (total, calls) => total + Math.max(0, calls.short - calls.long),
    0,
  );
  if (shortCallsNeedingStock === 0) return null;

  const coveredBefore = Math.min(shortCallsNeedingStock, Math.floor(heldLongShares / 100));
  const sharesAfter = Math.max(0, heldLongShares - input.quantity);
  const coveredAfter = Math.min(shortCallsNeedingStock, Math.floor(sharesAfter / 100));
  const uncoveredBefore = shortCallsNeedingStock - coveredBefore;
  const uncoveredAfter = shortCallsNeedingStock - coveredAfter;
  if (uncoveredAfter <= uncoveredBefore) return null;

  return `${uncoveredAfter} retained short call${uncoveredAfter === 1 ? "" : "s"} uncovered after selling stock collateral`;
}

type ResidualOptionRisk = { reason: string; unbounded: boolean };

/**
 * Project an option close through the held portfolio before declaring it
 * risk-free. Closing a long wing can leave a retained short naked even though
 * the closing order itself is bounded. Exact closing legs are mandatory when
 * the ticker has option inventory so the projection cannot guess by symbol.
 */
function retainedOptionRiskAfterClose(
  opt: OptionOrderRiskInput,
  portfolio: PortfolioData | null,
): ResidualOptionRisk | null {
  if (portfolio == null) return null;

  type Holding = {
    key: string;
    right: "C" | "P";
    expiry: string;
    long: number;
    short: number;
  };
  const holdings = new Map<string, Holding>();
  let longStockShares = 0;
  for (const position of portfolio.positions) {
    if (position.ticker.toUpperCase() !== opt.ticker.toUpperCase()) continue;
    for (const leg of position.legs) {
      if (leg.type === "Stock") {
        if (leg.direction === "LONG") longStockShares += Math.max(0, leg.contracts);
        continue;
      }
      if (leg.strike == null || !Number.isFinite(leg.strike)) continue;
      const right = leg.type === "Call" ? "C" : "P";
      const expiry = (leg.expiry ?? position.expiry).replace(/-/g, "");
      const key = `${expiry}|${right}|${leg.strike}`;
      const holding = holdings.get(key) ?? { key, right, expiry, long: 0, short: 0 };
      holding[leg.direction === "LONG" ? "long" : "short"] += Math.max(0, leg.contracts);
      holdings.set(key, holding);
    }
  }

  if (holdings.size === 0) return null;
  if (opt.chainLegs.length === 0) {
    return { reason: "Closing option identity unavailable", unbounded: false };
  }

  const uncovered = (inventory: Map<string, Holding>) => {
    const remainingLongs = [...inventory.values()].map((holding) => ({
      right: holding.right,
      expiry: holding.expiry,
      remaining: holding.long,
    }));
    let stockContracts = Math.floor(longStockShares / 100);
    let calls = 0;
    let puts = 0;
    const shorts = [...inventory.values()]
      .flatMap((holding) =>
        holding.short > 0
          ? [{ right: holding.right, expiry: holding.expiry, remaining: holding.short }]
          : [],
      )
      .sort((a, b) => b.expiry.localeCompare(a.expiry));
    for (const short of shorts) {
      for (const long of remainingLongs) {
        if (
          short.remaining <= 0 ||
          long.remaining <= 0 ||
          long.right !== short.right ||
          long.expiry < short.expiry
        ) continue;
        const consumed = Math.min(short.remaining, long.remaining);
        short.remaining -= consumed;
        long.remaining -= consumed;
      }
      if (short.right === "C" && short.remaining > 0 && stockContracts > 0) {
        const consumed = Math.min(short.remaining, stockContracts);
        short.remaining -= consumed;
        stockContracts -= consumed;
      }
      if (short.right === "C") calls += short.remaining;
      else puts += short.remaining;
    }
    return { calls, puts };
  };

  const before = uncovered(holdings);
  const projected = new Map(
    [...holdings].map(([key, holding]) => [key, { ...holding }]),
  );
  for (const closingLeg of opt.chainLegs) {
    const key = `${closingLeg.expiry.replace(/-/g, "")}|${closingLeg.right}|${closingLeg.strike}`;
    const holding = projected.get(key);
    const side = closingLeg.action === "SELL" ? "long" : "short";
    const quantity = Math.max(0, Math.trunc(closingLeg.quantity));
    if (holding == null || quantity <= 0 || holding[side] < quantity) {
      return { reason: "Closing option identity does not match held quantity", unbounded: false };
    }
    holding[side] -= quantity;
  }

  const after = uncovered(projected);
  if (after.calls > before.calls) {
    const newlyUncovered = after.calls - before.calls;
    return {
      reason: `${newlyUncovered} retained short call${newlyUncovered === 1 ? "" : "s"} uncovered by this close`,
      unbounded: true,
    };
  }
  if (after.puts > before.puts) {
    const newlyUncovered = after.puts - before.puts;
    return {
      reason: `${newlyUncovered} retained short put${newlyUncovered === 1 ? "" : "s"} unprotected by this close`,
      unbounded: false,
    };
  }
  return null;
}

/**
 * Margin estimate for a freshly-opened option order (no close-out).
 *
 * Classification:
 *   - Single-leg naked SHORT (the risk model returns an undefined-risk reason
 *     or an unbounded loss) → Reg-T per-leg estimate from spot/strike/right.
 *     This is the case where `maxLoss` (assignment-at-zero stress) is wrong —
 *     for a 15× MU 850P naked put it reads ~$1.275M, vs ~$138k Reg-T.
 *   - Everything else (defined-risk spreads, long debit, covered shorts that
 *     augmentation turned into spreads) → `maxLoss`, which IS the Reg-T margin
 *     for a defined-risk position. Long single options bottom out at the
 *     premium, so `maxLoss` already equals the debit there.
 *   - Multi-leg undefined/unbounded combos → no reliable client-side Reg-T;
 *     requirement null (renders "unavailable").
 */
/**
 * True iff the order is a single SELL CALL leg whose contracts are FULLY
 * covered by held LONG stock consumed by the augmenter (a covered call).
 * Partial stock cover leaves naked residue and must NOT match — the naked
 * path (unbounded risk + Reg-T naked estimate) stays authoritative there.
 */
function isFullyStockCoveredCall(
  opt: OptionOrderRiskInput,
  coveringLegs: CoveringPortfolioLeg[],
): boolean {
  if (opt.chainLegs.length !== 1) return false;
  const leg = opt.chainLegs[0];
  if (leg.action !== "SELL" || leg.right !== "C") return false;
  const contracts = Math.max(1, Math.trunc(leg.quantity));
  const stockCoveredContracts = coveringLegs.reduce(
    (sum, l) => (l.type === "Stock" ? sum + l.shares / 100 : sum),
    0,
  );
  return stockCoveredContracts >= contracts;
}

function describeCoveringLeg(leg: CoveringPortfolioLeg): string {
  return leg.type === "Option"
    ? `LONG ${leg.contracts}× $${leg.strike} ${leg.right === "C" ? "Call" : "Put"}`
    : `${leg.shares.toLocaleString()} held shares @ $${leg.avgCost.toFixed(2)}`;
}

/**
 * Coverage annotation for the confirm panel. The builder header already
 * chips coverage, but the confirm summary is where the operator reads the
 * risk numbers — without this note a covered call's stock-to-zero max loss
 * is indistinguishable from naked assignment stress (EWY 2026-07-21).
 */
function buildCoverageNote(
  opt: OptionOrderRiskInput,
  coveringLegs: CoveringPortfolioLeg[],
  coveredCall: boolean,
): string | null {
  if (coveringLegs.length === 0) return null;
  const held = coveringLegs.map(describeCoveringLeg).join(" + ");
  if (coveredCall) {
    const contracts = Math.max(1, Math.trunc(opt.chainLegs[0].quantity));
    return (
      `COVERED CALL: ${contracts} short call${contracts === 1 ? "" : "s"} covered by ${held}. ` +
      `Max loss is the held stock declining to zero net of premium, not naked assignment; ` +
      `no new margin is required.`
    );
  }
  return `COVERED BY HELD ${held}`;
}

function estimateOptionMargin(opt: OptionOrderRiskInput, risk: OrderRisk): MarginEstimate {
  const isUndefined = risk.maxLossUnbounded || risk.undefinedRiskReason != null;
  const single = opt.chainLegs.length === 1 ? opt.chainLegs[0] : null;
  if (isUndefined && single != null && single.action === "SELL") {
    return estimateInitialMargin({
      kind: "naked-option",
      right: single.right,
      strike: single.strike,
      spot: opt.underlyingSpot ?? null,
      quantity: Math.max(1, Math.trunc(single.quantity)),
      premiumPerShare: Math.abs(opt.netPremium),
    });
  }
  if (isUndefined) {
    // Multi-leg undefined/unbounded — no reliable client-side Reg-T estimate.
    return { requirement: null, source: "regt-estimate", approximate: true };
  }
  return estimateInitialMargin({ kind: "defined-risk", maxLoss: risk.maxLoss });
}

/**
 * Build the augmented summary from raw inputs + portfolio.
 *
 * `portfolio === undefined` → pending state.
 * `portfolio === null`      → no-portfolio state (still augmentation-aware
 *                             but with no coverage available).
 * `portfolio` populated     → full augmentation + risk math.
 */
export function useOrderRisk(
  input: OrderRiskInput | null,
  portfolio: PortfolioData | null | undefined,
): OrderRiskState | null {
  return useMemo(() => {
    if (input == null) return null;

    const traceId = makeTraceId();
    const correlationReport = portfolio?.risk_budget ?? null;
    const correlationStatus =
      portfolio === undefined
        ? "pending" as const
        : correlationReport == null
          ? "unavailable" as const
          : "resolved" as const;
    const portfolioRevision = buildPortfolioRevision(portfolio);
    const withCorrelation = (
      state: Omit<OrderRiskState, "correlationReport" | "correlationStatus" | "portfolioRevision">,
    ): OrderRiskState => ({ ...state, correlationReport, correlationStatus, portfolioRevision });

    // ---- Linear branch (futures + stock). Linear instruments have no
    // strike/expiry-as-option and don't flow through the augmentation
    // pipeline (no portfolio-option coverage applies). Held LONG / SHORT
    // counts are passed in by the caller, who looks them up from
    // `portfolio.positions`.
    if (input.type === "linear") {
      const baseSummary: OrderPresentationSummary = {
        description: input.description,
        totalCost:
          input.action === "SELL"
            ? -Math.abs(input.limitPrice * input.quantity * input.multiplier)
            : Math.abs(input.limitPrice * input.quantity * input.multiplier),
      };

      if (portfolio === undefined) {
        return withCorrelation({
          summary: brand(baseSummary, "pending", traceId),
          coverageStatus: "pending" as const,
          okToSubmit: false,
          coveringLegs: [],
        });
      }
      const coverageStatus: CoverageStatus =
        portfolio === null ? "no-portfolio" : "resolved";

      // Linear close-out: proceeds + realised P&L surfaced from
      // `closeOut.entryCostDollars`. `proceeds` is always the absolute
      // cash flow of the close (SELL-to-close: cash IN; BUY-to-close:
      // cash OUT — both rendered as positive magnitude with a label).
      // realised P&L = proceeds − basis for SELL-to-close; basis − cost
      // for BUY-to-close (mirror).
      if (input.closeOut != null) {
        const grossCash = Math.abs(input.limitPrice * input.quantity * input.multiplier);
        const postCloseUndefinedRiskReason = retainedShortCallRiskAfterStockClose(input, portfolio);
        const hasPostCloseUndefinedRisk =
          postCloseUndefinedRiskReason != null && postCloseUndefinedRiskReason.length > 0;
        const pnl =
          input.action === "SELL"
            ? grossCash - input.closeOut.entryCostDollars
            : input.closeOut.entryCostDollars - grossCash;
        const closeSummary: OrderPresentationSummary = {
          ...baseSummary,
          totalCost: grossCash,
          totalLabel:
            input.action === "SELL"
              ? "Proceeds:"
              : "Cost to Cover:",
          estimatedPnl: pnl,
          estimatedPnlLabel: input.closeOut.estimatedPnlLabel ?? "Est. Realized P&L:",
          maxLossUnbounded: hasPostCloseUndefinedRisk ? true : undefined,
          undefinedRiskReason: postCloseUndefinedRiskReason,
          // A pure close consumes no margin unless removing the linear leg
          // uncovers retained option exposure. In that case, fail closed on
          // the client estimate and require broker what-if confirmation.
          marginImpact: buildMarginImpact(
            hasPostCloseUndefinedRisk
              ? { requirement: null, source: "regt-estimate", approximate: true }
              : estimateInitialMargin({ kind: "close-out" }),
            portfolio,
            coverageStatus,
          ),
        };
        return withCorrelation({
          summary: brand(closeSummary, coverageStatus, traceId),
          coverageStatus,
          okToSubmit: true,
          coveringLegs: [],
        });
      }

      const risk = computeLinearRisk({
        action: input.action,
        quantity: input.quantity,
        limitPrice: input.limitPrice,
        multiplier: input.multiplier,
        heldLong: input.heldQuantity,
        heldShort: input.heldShortQuantity,
      });

      const linearMargin = estimateLinearMargin(input, risk.maxLoss, risk.maxLossUnbounded);

      const resolved: OrderPresentationSummary = {
        ...baseSummary,
        maxGain: risk.maxGain,
        maxLoss: risk.maxLoss,
        maxLossUnbounded: risk.maxLossUnbounded,
        maxGainUnbounded: risk.maxGainUnbounded,
        undefinedRiskReason: risk.undefinedRiskReason,
        marginImpact: buildMarginImpact(linearMargin, portfolio, coverageStatus),
      };

      // Unbounded / undefined risk is advisory (Gate 1 warning), not a
      // block — only unresolved coverage disables submit.
      const okToSubmit = coverageStatus === "resolved";

      return withCorrelation({
        summary: brand(resolved, coverageStatus, traceId),
        coverageStatus,
        okToSubmit,
        coveringLegs: [],
      });
    }

    // ---- Option branch (default). Absent `type` is treated as options for
    // backwards compatibility with pre-2026-05-26 call sites.
    const opt = input as OptionOrderRiskInput;
    const baseSummary: OrderPresentationSummary = {
      description: opt.description,
      totalCost: opt.totalCost,
      totalLabel: opt.totalLabel,
      breakeven: opt.breakeven ?? null,
    };

    // Pending: portfolio not yet provided. Surface as skeleton; no risk math.
    if (portfolio === undefined) {
      return withCorrelation({
        summary: brand(baseSummary, "pending", traceId),
        coverageStatus: "pending" as const,
        okToSubmit: false,
        coveringLegs: [],
      });
    }

    const coverageStatus: CoverageStatus =
      portfolio === null ? "no-portfolio" : "resolved";

    // Close-out: project the exact closing legs through retained portfolio
    // exposure before surfacing proceeds + realized P&L.
    if (opt.closeOut != null) {
      const proceeds = opt.totalCost ?? 0;
      const pnl = proceeds - opt.closeOut.entryCostDollars;
      const residualRisk = retainedOptionRiskAfterClose(opt, portfolio);
      const closeSummary: OrderPresentationSummary = {
        ...baseSummary,
        totalCost: Math.abs(proceeds),
        totalLabel: opt.totalLabel ?? (proceeds >= 0 ? "Close Credit:" : "Close Debit:"),
        estimatedPnl: pnl,
        estimatedPnlLabel: opt.closeOut.estimatedPnlLabel ?? "Est. Realized P&L:",
        maxLossUnbounded: residualRisk?.unbounded || undefined,
        undefinedRiskReason: residualRisk?.reason ?? null,
        // A pure close consumes no margin. A close that exposes retained risk
        // requires broker what-if and is blocked by the canonical gate.
        marginImpact: buildMarginImpact(
          residualRisk
            ? { requirement: null, source: "regt-estimate", approximate: true }
            : estimateInitialMargin({ kind: "close-out" }),
          portfolio,
          coverageStatus,
        ),
      };
      return withCorrelation({
        summary: brand(closeSummary, coverageStatus, traceId),
        coverageStatus,
        okToSubmit: coverageStatus === "resolved" && residualRisk == null,
        coveringLegs: [],
      });
    }

    // Augment chain legs with portfolio coverage. When portfolio is null
    // (no-portfolio), augmentation still runs the quantity normalisation
    // step and returns the chain legs as per-combo ratios — the qty²
    // regression guard from the prior fix stays intact.
    const augmented = augmentOrderLegsWithPortfolioCoverage(
      opt.chainLegs,
      opt.ticker,
      portfolio,
    );

    const adjustedNetPremium = opt.netPremium + augmented.netPremiumAdjustment;

    // F1 net-of-cost (FU7): when the surface threads a live entry quote, run
    // the shared cost model and fold the round-trip cost into the verdict.
    // `comboQuantity` is the per-combo contract count; `chainLegs.length` is
    // the structural leg count (NOT the augmented riskLegs, which include
    // injected virtual coverage legs that aren't separately ticketed). Absent
    // a quote, `costs` is undefined and `computeOrderRisk` is a pure no-op.
    const costs =
      opt.quote != null
        ? {
            roundTripCost: estimateRoundTripCost({
              contracts: augmented.comboQuantity,
              numLegs: opt.chainLegs.length,
              entryBid: opt.quote.bid,
              entryAsk: opt.quote.ask,
            }),
          }
        : undefined;

    const risk = computeOrderRisk(
      augmented.riskLegs,
      adjustedNetPremium,
      augmented.comboQuantity,
      costs,
    );

    const coveredCall = isFullyStockCoveredCall(opt, augmented.coveringLegs);
    const optionMargin = coveredCall
      ? estimateInitialMargin({ kind: "stock-covered-call" })
      : estimateOptionMargin(opt, risk);

    const resolved: OrderPresentationSummary = {
      ...baseSummary,
      maxGain: risk.maxGain,
      maxLoss: risk.maxLoss,
      maxLossUnbounded: risk.maxLossUnbounded,
      maxGainUnbounded: risk.maxGainUnbounded,
      undefinedRiskReason: risk.undefinedRiskReason,
      marginImpact: buildMarginImpact(optionMargin, portfolio, coverageStatus),
      coverageNote: buildCoverageNote(opt, augmented.coveringLegs, coveredCall),
    };

    // Unbounded / undefined risk is advisory (Gate 1 warning), not a
    // block — only unresolved coverage disables submit.
    const okToSubmit = coverageStatus === "resolved";

    return withCorrelation({
      summary: brand(resolved, coverageStatus, traceId),
      coverageStatus,
      okToSubmit,
      coveringLegs: augmented.coveringLegs,
    });
  }, [input, portfolio]);
}
