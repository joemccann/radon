/**
 * Public risk module surface.
 *
 * This is the ONLY entry point that order-entry surfaces should import from.
 * Everything reachable through this barrel is portfolio-aware; the raw
 * `computeOrderRisk` / `augmentOrderLegsWithPortfolioCoverage` functions
 * live under `./internal/` and are blocked by ESLint
 * (`no-restricted-imports`) from being imported anywhere else in the app.
 */

export { useOrderRisk } from "./useOrderRisk";
export type {
  OrderRiskInput,
  OptionOrderRiskInput,
  LinearOrderRiskInput,
  OrderRiskState,
  ChainOrderLeg,
  CoveringPortfolioLeg,
} from "./useOrderRisk";
export { OrderRiskGate } from "./OrderRiskGate";
export type { OrderRiskGateProps } from "./OrderRiskGate";
// Paper-mode routing (F13): single source of truth for IB vs shadow engine.
export { resolvePlacementTarget } from "./paperMode";
export type { PlacementTarget } from "./paperMode";
export type { AugmentedOrderSummary, CoverageStatus, OrderPresentationSummary } from "../types";
// Telemetry: per-session ring buffer for bug-report correlation. Operators /
// devs can paste `dumpOrderRiskTraces()` into the console to inspect.
export {
  dumpOrderRiskTraces,
  clearOrderRiskTraces,
  type OrderRiskTrace,
} from "./telemetry";
