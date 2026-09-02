"use client";

import { useEffect } from "react";

/**
 * OrderRiskGate — the renderless contract that pairs `useOrderRisk` with
 * `<OrderConfirmSummary>`.
 *
 * Why a wrapper at all when the hook already produces the branded summary?
 * Because the chokepoint should be visible at call sites. A surface that
 * imports `useOrderRisk` and feeds the result to `<OrderConfirmSummary>` by
 * hand is technically equivalent to using the gate, but the gate makes the
 * contract literal: ONE component, ALL the wiring, no opportunity for the
 * next refactor to introduce a forgotten step (telemetry, pending render,
 * coverage chips later).
 *
 * Surfaces pass `input` and `portfolio`. The gate handles the rest.
 */

import type { PortfolioData } from "@/lib/types";
import { OrderConfirmSummary } from "../components/OrderConfirmSummary";
import { LocateFeeChip } from "../components/LocateFeeChip";
import { PaperModeToggle } from "../components/PaperModeToggle";
import { useOrderRisk, type OrderRiskInput, type OrderRiskState } from "./useOrderRisk";
import { useRecordOrderRiskTrace } from "./telemetry";
import { useShortAvailability } from "../hooks/useShortAvailability";
import { useWhatIfMargin } from "./useWhatIfMargin";
import { mergeWhatIfMargin } from "./mergeWhatIfMargin";
import CorrelationRiskBanner from "@/components/CorrelationRiskBanner";
import type { CorrelationOrderContext } from "@/lib/correlationRiskBanner";

// Phase-2 IB what-if margin. Ships OFF: the backend (endpoint + script branch)
// is inert until this flag is set, after live verification on an authenticated
// gateway during market hours. See tasks/ib-whatif-margin-plan.md.
const WHATIF_MARGIN_ENABLED = process.env.NEXT_PUBLIC_WHATIF_MARGIN_ENABLED === "1";

export interface OrderRiskGateProps {
  /**
   * Full risk input. Pass `null` when the form is not in a confirm state
   * (e.g. user is still typing); the gate then renders nothing.
   */
  input: OrderRiskInput | null;
  /**
   * Live portfolio snapshot. `undefined` = still loading → renders pending
   * skeleton. `null` = surface intentionally has no portfolio context →
   * renders "Coverage indeterminate — portfolio not in scope" skeleton.
   */
  portfolio: PortfolioData | null | undefined;
  /**
   * Surface tag used for telemetry. Required so a future bug report's
   * sessionStorage dump identifies WHICH surface produced which trace.
   * Use kebab-case literals: "chain-builder", "order-tab-single",
   * "instrument-modal", etc.
   */
  surface: string;
  /** Pass-through to `<OrderConfirmSummary>`. */
  variant?: "info" | "neutral";
  /** Custom class on the rendered summary. */
  className?: string;
  /**
   * Optional callback fired with the resolved risk state. Lets the parent
   * gate its submit button on `okToSubmit` without duplicating the hook
   * call. Equivalent to `useOrderRisk` directly but no extra render.
   */
  onState?: (state: OrderRiskState | null) => void;
  /**
   * Paper-mode flag (F13). When `onPaperModeChange` is supplied the gate
   * renders a Live/Paper toggle; the parent owns the mode and routes the
   * order via `resolvePlacementTarget(paperMode)`. When the handler is
   * omitted the toggle is hidden and the surface is live-only.
   */
  paperMode?: boolean;
  onPaperModeChange?: (next: boolean) => void;
}

export function OrderRiskGate({
  input,
  portfolio,
  surface,
  variant = "info",
  className,
  onState,
  paperMode = false,
  onPaperModeChange,
}: OrderRiskGateProps) {
  const state = useOrderRisk(input, portfolio);

  // Parent state updates belong after commit; firing during render produces
  // React cross-component update warnings and can cause render loops.
  useEffect(() => {
    onState?.(state);
  }, [onState, state]);

  // Telemetry: record one trace per resolved-state observation. The hook
  // unconditionally runs (React hooks rule) — when `state` is null it
  // simply records nothing. `chainLegs` only exists on the option branch;
  // linear inputs report legCount = 1 (one instrument) and contracts =
  // the linear quantity.
  const isLinear = input?.type === "linear";
  const isOption = input != null && !isLinear;
  const legCount = isOption ? (input as { chainLegs: unknown[] }).chainLegs.length : isLinear ? 1 : 0;
  const totalContracts = isOption
    ? (input as { chainLegs: { quantity: number }[] }).chainLegs.reduce(
        (sum, l) => sum + Math.max(1, Math.trunc(l.quantity)),
        0,
      )
    : isLinear
      ? Math.max(1, Math.trunc((input as { quantity: number }).quantity))
      : 0;
  useRecordOrderRiskTrace(
    surface,
    state?.summary ?? null,
    input?.ticker ?? "",
    legCount,
    totalContracts,
    state?.coveringLegs.length ?? 0,
    0, // netPremiumAdjustment is internal; future: surface on state if needed
    state?.summary.maxLossUnbounded === true ||
      (state?.summary.undefinedRiskReason != null && state.summary.undefinedRiskReason.length > 0),
  );

  // LOCATE/FEE chip: shown when a SELL/SHORT order has no held position in
  // the underlying. Applicable to both option SELL legs and linear SELL orders.
  const locateEnabled = resolveLocateChipEnabled(input, portfolio, state);
  const locateTicker = locateEnabled ? (input?.ticker ?? null) : null;
  const { status: locateStatus, data: locateData } = useShortAvailability(
    locateTicker,
    locateEnabled,
  );

  // Phase-2: real IB margin for undefined-risk combos whose Reg-T estimate was
  // null. Hook is always called (hooks rule) but stays idle unless the flag is
  // on AND the gate predicate holds. Informational only — never flips submit.
  const whatIf = useWhatIfMargin(input, state, WHATIF_MARGIN_ENABLED);

  if (state == null) return null;

  const showLocateChip = locateEnabled && locateStatus != null && locateData != null;

  const summaryForRender =
    whatIf.status === "success" && whatIf.initMargin != null
      ? mergeWhatIfMargin(state.summary, whatIf.initMargin)
      : state.summary;

  const marginWhatIf =
    whatIf.status === "loading"
      ? ({ status: "loading" } as const)
      : whatIf.status === "error"
        ? ({ status: "error" } as const)
        : undefined;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      {onPaperModeChange != null && (
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <PaperModeToggle paperMode={paperMode} onChange={onPaperModeChange} />
        </div>
      )}
      <OrderConfirmSummary
        summary={summaryForRender}
        variant={variant}
        className={className}
        marginWhatIf={marginWhatIf}
      />
      <CorrelationRiskBanner
        report={state.correlationReport}
        showUnavailable={state.coverageStatus === "resolved"}
        order={resolveCorrelationOrderContext(input)}
      />
      {showLocateChip && (
        <LocateFeeChip status={locateStatus} data={locateData} />
      )}
    </div>
  );
}

/**
 * Determines whether the LOCATE/FEE chip should fire for this order.
 *
 * Rules:
 *   - For option orders: at least one leg must be SELL action.
 *   - For linear orders: action must be SELL.
 *   - The portfolio (when loaded) must have NO position in the underlying.
 *     When portfolio is still loading (undefined), we do NOT fetch yet so we
 *     avoid a stale/wrong chip showing before coverage is known.
 */
export function resolveLocateChipEnabled(
  input: OrderRiskInput | null,
  portfolio: PortfolioData | null | undefined,
  state: OrderRiskState | null,
): boolean {
  if (input == null) return false;

  const hasSellLeg = inputHasSellLeg(input);
  if (!hasSellLeg) return false;

  // The canonical risk projection already accounts for order-internal legs,
  // exact held options, stock coverage, and held quantities. A ticker-only
  // portfolio lookup is unsafe: an unrelated option position must not hide a
  // genuinely naked short. Show locate telemetry only after coverage resolves
  // and the projected order still carries an uncovered/naked obligation.
  if (portfolio == null || state?.coverageStatus !== "resolved") return false;
  return state.summary.undefinedRiskReason != null;
}

/**
 * Working-order context for the Gate-3 correlation banner. Both input
 * variants mark a close/reduce of held exposure with `closeOut` (the
 * chokepoint's close-out branch), so its presence is the reduce signal:
 * a breached cluster containing this ticker must not present as a block
 * on the order that trims it.
 */
export function resolveCorrelationOrderContext(
  input: OrderRiskInput | null,
): CorrelationOrderContext | null {
  if (input == null || !input.ticker) return null;
  return {
    ticker: input.ticker,
    reducesExposure: (input as { closeOut?: unknown }).closeOut != null,
  };
}

function inputHasSellLeg(input: OrderRiskInput): boolean {
  if (input.type === "linear") return input.action === "SELL";
  // Option order: any leg with action SELL
  const opt = input as { chainLegs: { action: string }[] };
  return opt.chainLegs.some((l) => l.action === "SELL");
}
