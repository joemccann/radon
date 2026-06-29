/**
 * Brand-preserving merge of a real IB what-if margin into a resolved summary.
 *
 * Spreading `...summary` copies own enumerable properties INCLUDING the
 * `ORDER_RISK_BRAND` symbol, so the result still passes `isAugmentedOrderSummary`
 * — no need to import the symbol, and the dev-mode brand assertion in
 * `<OrderConfirmSummary>` stays satisfied. This is the ONLY sanctioned place to
 * produce a `marginImpact` with `source: "ib-whatif"`; never hand-build one with
 * an `as`-cast elsewhere.
 */
import type { AugmentedOrderSummary } from "../types";

export function mergeWhatIfMargin(
  summary: AugmentedOrderSummary,
  initMargin: number,
): AugmentedOrderSummary {
  const availableBefore = summary.marginImpact?.availableBefore ?? null;
  return {
    ...summary,
    marginImpact: {
      requirement: initMargin,
      availableBefore,
      availableAfter: availableBefore == null ? null : availableBefore - initMargin,
      baselineLabel: summary.marginImpact?.baselineLabel ?? "Available Funds",
      source: "ib-whatif",
      approximate: false,
    },
  } as AugmentedOrderSummary;
}
