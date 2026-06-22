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
} from "./internal/computeOrderRisk";
import { computeLinearRisk } from "./internal/computeLinearRisk";
import { estimateRoundTripCost } from "../costs";

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
   * Close-out short-circuit. When set, the augmentation pipeline is
   * bypassed: max-loss/max-gain are zeroed (the SELL is a close, not a new
   * exposure) and `estimatedPnl` is computed from order proceeds minus
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
   * Optional live entry quote for the order as a whole (the NET combo quote
   * for multi-leg, or the single option's quote for single-leg). Both fields
   * are per-share positive magnitudes — the same `netPrices.bid` / `.ask` the
   * chain already computes. When supplied, `useOrderRisk` runs the F1 cost
   * model (`estimateRoundTripCost`) and folds the round-trip cost into the
   * risk verdict: bounded max-loss grows by the cost, bounded max-gain shrinks
   * by it (clamped at 0). Unbounded legs stay unbounded.
   *
   * Absent (the historical default) → no cost adjustment; behavior is
   * byte-for-byte identical to before FU7. A null bid/ask falls back to the
   * estimated half-spread inside the cost model rather than disabling cost.
   */
  quote?: { bid: number | null; ask: number | null } | null;
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
   * True iff coverage is fully resolved AND the underlying risk model
   * returned a finite, defined-risk verdict (no UNBOUNDED, no undefined
   * risk reason). Surfaces use this to gate the submit button.
   */
  okToSubmit: boolean;
  /** Coverage entries injected, exposed for chip rendering. */
  coveringLegs: CoveringPortfolioLeg[];
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
        return {
          summary: brand(baseSummary, "pending", traceId),
          coverageStatus: "pending" as const,
          okToSubmit: false,
          coveringLegs: [],
        };
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
        };
        return {
          summary: brand(closeSummary, coverageStatus, traceId),
          coverageStatus,
          okToSubmit: true,
          coveringLegs: [],
        };
      }

      const risk = computeLinearRisk({
        action: input.action,
        quantity: input.quantity,
        limitPrice: input.limitPrice,
        multiplier: input.multiplier,
        heldLong: input.heldQuantity,
        heldShort: input.heldShortQuantity,
      });

      const resolved: OrderPresentationSummary = {
        ...baseSummary,
        maxGain: risk.maxGain,
        maxLoss: risk.maxLoss,
        maxLossUnbounded: risk.maxLossUnbounded,
        maxGainUnbounded: risk.maxGainUnbounded,
        undefinedRiskReason: risk.undefinedRiskReason,
      };

      const okToSubmit =
        coverageStatus === "resolved" &&
        !risk.maxLossUnbounded &&
        !risk.hasUndefinedRisk;

      return {
        summary: brand(resolved, coverageStatus, traceId),
        coverageStatus,
        okToSubmit,
        coveringLegs: [],
      };
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
      return {
        summary: brand(baseSummary, "pending", traceId),
        coverageStatus: "pending" as const,
        okToSubmit: false,
        coveringLegs: [],
      };
    }

    const coverageStatus: CoverageStatus =
      portfolio === null ? "no-portfolio" : "resolved";

    // Close-out: short-circuit risk math; surface proceeds + realized P&L.
    if (opt.closeOut != null) {
      const proceeds = opt.totalCost ?? 0;
      const pnl = proceeds - opt.closeOut.entryCostDollars;
      const closeSummary: OrderPresentationSummary = {
        ...baseSummary,
        totalCost: Math.abs(proceeds),
        totalLabel: opt.totalLabel ?? (proceeds >= 0 ? "Close Credit:" : "Close Debit:"),
        estimatedPnl: pnl,
        estimatedPnlLabel: opt.closeOut.estimatedPnlLabel ?? "Est. Realized P&L:",
      };
      return {
        summary: brand(closeSummary, coverageStatus, traceId),
        coverageStatus,
        okToSubmit: true,
        coveringLegs: [],
      };
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

    const resolved: OrderPresentationSummary = {
      ...baseSummary,
      maxGain: risk.maxGain,
      maxLoss: risk.maxLoss,
      maxLossUnbounded: risk.maxLossUnbounded,
      maxGainUnbounded: risk.maxGainUnbounded,
      undefinedRiskReason: risk.undefinedRiskReason,
    };

    const okToSubmit =
      coverageStatus === "resolved" &&
      !risk.maxLossUnbounded &&
      !risk.hasUndefinedRisk;

    return {
      summary: brand(resolved, coverageStatus, traceId),
      coverageStatus,
      okToSubmit,
      coveringLegs: augmented.coveringLegs,
    };
  }, [input, portfolio]);
}
