/**
 * TEST-ONLY HELPERS for the order-risk module.
 *
 * Production code MUST go through `useOrderRisk` / `<OrderRiskGate>`. The
 * ESLint rule blocks imports of `./internal/*` from app code; this file
 * is also blocked from app imports (allow-list: `tests/**`, `**\/*.test.ts`,
 * `**\/*.test.tsx`).
 *
 * Provided:
 *   - re-export of the raw math (`computeOrderRisk`, augmentation helper) so
 *     `order-risk.test.ts` continues to test the pure functions directly.
 *   - `brandAugmentedSummaryForTest` — attach the augmentation brand to a
 *     plain literal so `<OrderConfirmSummary>` render tests can verify
 *     presentation logic without spinning up the full pipeline. This is
 *     the ONLY supported way to construct a brand outside `useOrderRisk`.
 */

import { ORDER_RISK_BRAND, type AugmentedOrderSummary, type CoverageStatus, type OrderPresentationSummary } from "../types";

export {
  computeOrderRisk,
  augmentOrderLegsWithPortfolioCoverage,
  type OrderRisk,
  type OrderRiskLeg,
  type ChainOrderLeg,
  type CoveringPortfolioLeg,
  type AugmentedOrderLegs,
} from "./internal/computeOrderRisk";

export {
  estimateInitialMargin,
  type MarginEstimate,
  type MarginEstimateArgs,
  type MarginEstimateSource,
} from "./internal/marginEstimate";

/**
 * Wrap a plain presentation summary with the augmentation brand for use
 * in component-render tests. The `coverageStatus` defaults to `"resolved"`
 * because most render tests want to exercise the resolved-state branch
 * (skeleton rendering for pending/no-portfolio is a separate test concern).
 *
 * Do NOT export this from any non-test entry point.
 */
export function brandAugmentedSummaryForTest(
  summary: OrderPresentationSummary,
  options: { coverageStatus?: CoverageStatus; traceId?: string } = {},
): AugmentedOrderSummary {
  return {
    ...summary,
    [ORDER_RISK_BRAND]: "augmented",
    coverageStatus: options.coverageStatus ?? "resolved",
    traceId: options.traceId ?? "test-trace-00000000",
  } as AugmentedOrderSummary;
}
