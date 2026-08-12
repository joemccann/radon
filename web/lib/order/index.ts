/**
 * Unified Order System
 *
 * Composable components, hooks, and types for consistent order
 * placement, modification, and display across the application.
 *
 * Usage:
 *   import { OrderPriceStrip, useOrderPrices, OrderAction } from "@/lib/order";
 */

// Types
export type {
  OrderAction,
  OrderTif,
  OrderType,
  OrderPrices,
  OrderLeg,
  OrderFormState,
  OrderValidation,
  // OrderSummary is the legacy alias to OrderPresentationSummary; kept for
  // existing consumer-code references during the migration but should not be
  // newly authored against. Render through <OrderRiskGate> instead.
  OrderSummary,
  OrderPresentationSummary,
  AugmentedOrderSummary,
  CoverageStatus,
  PriceDisplayProps,
  LegDisplayProps,
  OrderFormProps,
} from "./types";

// Risk chokepoint — the only public way to compute order risk math.
export { useOrderRisk, OrderRiskGate } from "./risk";
// Paper-mode routing (F13): single source of truth for live vs paper target.
export { resolvePlacementTarget } from "./risk";
export type { PlacementTarget } from "./risk";
export type {
  OrderRiskInput,
  OptionOrderRiskInput,
  LinearOrderRiskInput,
  OrderRiskState,
  OrderRiskGateProps,
  ChainOrderLeg,
  CoveringPortfolioLeg,
} from "./risk";

export type { IbOrderType } from "./stopOrder";
export {
  parseIbOrderType,
  isStopOrderType,
  pricesValidForOrderType,
  riskPriceForOrderType,
  ibPlaceFields,
  paperPlaceFields,
  orderTypeLabel,
} from "./stopOrder";

// Hooks
export { useOrderPrices } from "./hooks/useOrderPrices";
export { useOrderValidation } from "./hooks/useOrderValidation";

// Components
export { OrderPriceStrip } from "./components/OrderPriceStrip";
export type { OrderQuoteSide } from "./components/OrderPriceStrip";
export { OrderLegPills } from "./components/OrderLegPills";
export { OrderPriceButtons } from "./components/OrderPriceButtons";
export { OrderActionToggle } from "./components/OrderActionToggle";
export { OrderTifSelector } from "./components/OrderTifSelector";
export { OrderQuantityInput } from "./components/OrderQuantityInput";
export { OrderPriceInput } from "./components/OrderPriceInput";
export { OrderConfirmSummary } from "./components/OrderConfirmSummary";
