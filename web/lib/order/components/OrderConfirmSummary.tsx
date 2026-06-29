"use client";

/**
 * OrderConfirmSummary — Order summary panel for confirmation step.
 *
 * **Prop contract changed 2026-05-26.** This component now accepts only
 * `AugmentedOrderSummary` — a branded type that can be produced ONLY by the
 * `useOrderRisk` hook in `@/lib/order/risk`. Plain object literals no longer
 * typecheck. This is deliberate: every prior production bug in order risk
 * math shipped because a surface hand-built one of these literals and
 * forgot to thread portfolio coverage. The brand makes that impossible.
 *
 * Use via `<OrderRiskGate input={...} surface="..." />` — never instantiate
 * this component directly outside the gate.
 */

import { useEffect } from "react";
import type { AugmentedOrderSummary } from "../types";
import { isAugmentedOrderSummary } from "../types";

interface OrderConfirmSummaryProps {
  /**
   * Branded augmented summary from `useOrderRisk`. Coverage state is carried
   * on `summary.coverageStatus`; when `"pending"` or `"no-portfolio"` the
   * component renders a labeled skeleton and the parent should disable
   * submit.
   */
  summary: AugmentedOrderSummary;
  /** Show as info callout (blue) or neutral */
  variant?: "info" | "neutral";
  /** Custom class name */
  className?: string;
  /**
   * Phase-2 IB what-if state (from `useWhatIfMargin` via `OrderRiskGate`). When
   * `loading`, the otherwise-UNAVAILABLE margin row shows a "Calculating IB
   * margin…" placeholder; `error` (or absent) keeps today's UNAVAILABLE text.
   * The resolved number itself arrives merged into `summary.marginImpact` with
   * `source: "ib-whatif"`.
   */
  marginWhatIf?: { status: "loading" | "error" };
}

function formatCurrency(value: number | null | undefined): string {
  if (value == null) return "---";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value);
}

function formatPrice(value: number | null | undefined): string {
  if (value == null) return "---";
  return `$${value.toFixed(2)}`;
}

export function OrderConfirmSummary({
  summary,
  variant = "info",
  className = "",
  marginWhatIf,
}: OrderConfirmSummaryProps) {
  // Dev-mode brand check. Catches an `as AugmentedOrderSummary` cast that
  // smuggles a hand-built literal past TypeScript. Production builds skip
  // the check (compile-time brand still enforced).
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" && !isAugmentedOrderSummary(summary)) {
      console.error(
        "[order-risk] <OrderConfirmSummary> received a non-branded summary. " +
        "This is a programming error — render through <OrderRiskGate> from " +
        "@/lib/order/risk so the portfolio-aware augmentation pipeline runs. " +
        "See web/CLAUDE.md → Order-risk chokepoint.",
        summary,
      );
    }
  }, [summary]);

  // Pending / no-portfolio: render a labeled skeleton instead of zeroes.
  // The parent surface is expected to ALSO disable submit when status !== "resolved".
  if (summary.coverageStatus !== "resolved") {
    const pendingLabel =
      summary.coverageStatus === "no-portfolio"
        ? "Coverage indeterminate — portfolio not in scope"
        : "Coverage indeterminate — portfolio resolving";
    return (
      <div
        className={`order-confirm-summary order-confirm-summary-pending ${className}`.trim()}
        data-coverage-status={summary.coverageStatus}
        role="status"
      >
        <div className="order-confirm-description">{summary.description}</div>
        <div className="order-confirm-metrics">
          <span
            className="order-confirm-metric"
            style={{ fontStyle: "italic", color: "var(--text-secondary)" }}
          >
            {pendingLabel}
          </span>
        </div>
      </div>
    );
  }

  const variantClass = variant === "info" ? "order-confirm-summary-info" : "";
  const showMaxGain = summary.maxGainUnbounded === true || summary.maxGain != null;
  const showMaxLoss = summary.maxLossUnbounded === true || summary.maxLoss != null;
  const marginImpact = summary.coverageStatus === "resolved" ? summary.marginImpact ?? null : null;
  const marginRequirementUnavailable = marginImpact != null && marginImpact.requirement == null;
  const hasUndefinedRisk =
    summary.maxLossUnbounded === true ||
    (summary.undefinedRiskReason != null && summary.undefinedRiskReason.length > 0);

  return (
    <div
      className={`order-confirm-summary ${variantClass} ${className}`.trim()}
      data-undefined-risk={hasUndefinedRisk ? "true" : undefined}
    >
      <div className="order-confirm-description">{summary.description}</div>
      <div className="order-confirm-metrics">
        {summary.totalCost != null && (
          <span className="order-confirm-metric">
            <span className="order-confirm-metric-label">{summary.totalLabel ?? "Total:"}</span>
            <span className="order-confirm-metric-value">{formatCurrency(summary.totalCost)}</span>
          </span>
        )}
        {marginImpact != null && (
          <>
            <span className="order-confirm-metric">
              <span className="order-confirm-metric-label">Margin Req:</span>
              <span
                className="order-confirm-metric-value"
                data-margin-requirement-unavailable={
                  marginRequirementUnavailable ? "true" : undefined
                }
                style={marginRequirementUnavailable ? { color: "var(--warning)" } : undefined}
              >
                {marginRequirementUnavailable ? (
                  marginWhatIf?.status === "loading" ? (
                    <span style={{ color: "var(--text-muted)" }}>Calculating IB margin&hellip;</span>
                  ) : (
                    <>
                      UNAVAILABLE
                      <span
                        style={{ marginLeft: "6px", fontSize: "0.85em", color: "var(--text-muted)" }}
                      >
                        IB what-if required
                      </span>
                    </>
                  )
                ) : (
                  <>
                    {marginImpact.approximate ? "~" : ""}
                    {formatCurrency(marginImpact.requirement)}
                    {marginImpact.source === "regt-estimate" && (
                      <span
                        style={{ marginLeft: "6px", fontSize: "0.85em", color: "var(--text-muted)" }}
                      >
                        est. Reg-T
                      </span>
                    )}
                    {marginImpact.source === "ib-whatif" && (
                      <span
                        style={{ marginLeft: "6px", fontSize: "0.85em", color: "var(--text-muted)" }}
                      >
                        IB margin
                      </span>
                    )}
                  </>
                )}
              </span>
            </span>
            <span className="order-confirm-metric">
              <span className="order-confirm-metric-label">{marginImpact.baselineLabel} After:</span>
              <span
                className="order-confirm-metric-value"
                data-margin-exceeded={
                  marginImpact.availableAfter != null &&
                  marginImpact.availableAfter < 0
                    ? "true"
                    : undefined
                }
                style={
                  marginImpact.availableAfter != null &&
                  marginImpact.availableAfter < 0
                    ? { color: "var(--negative)" }
                    : undefined
                }
              >
                {formatCurrency(marginImpact.availableAfter)}
              </span>
            </span>
          </>
        )}
        {showMaxGain && (
          <span className="order-confirm-metric">
            <span className="order-confirm-metric-label">Max Gain:</span>
            <span className="order-confirm-metric-value order-confirm-positive">
              {summary.maxGainUnbounded === true ? "UNBOUNDED" : formatCurrency(summary.maxGain)}
            </span>
          </span>
        )}
        {showMaxLoss && (
          <span className="order-confirm-metric">
            <span className="order-confirm-metric-label">Max Loss:</span>
            <span
              className="order-confirm-metric-value order-confirm-negative"
              data-unbounded={summary.maxLossUnbounded === true ? "true" : undefined}
            >
              {summary.maxLossUnbounded === true ? "UNBOUNDED" : formatCurrency(summary.maxLoss)}
            </span>
          </span>
        )}
        {summary.breakeven != null && (
          <span className="order-confirm-metric">
            <span className="order-confirm-metric-label">Breakeven:</span>
            <span className="order-confirm-metric-value">{formatPrice(summary.breakeven)}</span>
          </span>
        )}
        {summary.estimatedPnl != null && (
          <span className="order-confirm-metric">
            <span className="order-confirm-metric-label">{summary.estimatedPnlLabel ?? "Est. P&L:"}</span>
            <span className={`order-confirm-metric-value ${summary.estimatedPnl >= 0 ? "order-confirm-positive" : "order-confirm-negative"}`}>
              {formatCurrency(summary.estimatedPnl)}
            </span>
          </span>
        )}
      </div>
      {hasUndefinedRisk && (
        <div
          className="order-confirm-undefined-risk"
          role="alert"
          data-testid="order-undefined-risk-warning"
        >
          <span className="order-confirm-undefined-risk-label">GATE 1: Undefined risk</span>
          <span className="order-confirm-undefined-risk-detail">
            {summary.maxLossUnbounded === true
              ? `${summary.undefinedRiskReason ?? "Uncovered short option"} — loss is theoretically unbounded.`
              : `${summary.undefinedRiskReason ?? "Naked short exposure"} — max loss reflects assignment-at-zero stress, not a defined-risk cap.`}
          </span>
        </div>
      )}
    </div>
  );
}
