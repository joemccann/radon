/**
 * Operator-facing display helpers for the /orders surface.
 * Pure functions — map IB plumbing into recognition-friendly labels and
 * fill/intent/distance signals used by desktop and mobile order lists.
 */

import type { OpenOrder, PortfolioPosition } from "@/lib/types";
import { findPortfolioLegDirection } from "@/lib/openOrderCombos";

export type OperatorOrderStatus =
  | "Working"
  | "Queued"
  | "Partial"
  | "Pending cancel"
  | "Pending modify"
  | "Inactive"
  | "Filled"
  | "Cancelled"
  | "Unknown";

export type OrderStatusTone =
  | "working"
  | "partial"
  | "pending"
  | "inactive"
  | "done"
  | "neutral";

export type MappedOrderStatus = {
  label: OperatorOrderStatus;
  raw: string;
  tone: OrderStatusTone;
};

export type DistanceToFill = {
  /** Signed market − limit (BUY) or limit − market (SELL). Negative = through. */
  delta: number;
  urgency: "near" | "far" | "through";
};

export type OrderIntent = "OPEN" | "CLOSE" | "UNKNOWN";

export type OpenOrdersSummary = {
  openCount: number;
  partialCount: number;
  workingCount: number;
};

const NEAR_THRESHOLD = 0.05;

export function isPartialFill(order: {
  filled: number;
  remaining: number;
}): boolean {
  return order.filled > 0 && order.remaining > 0;
}

export function formatFillQuantity(order: {
  filled: number;
  remaining: number;
  totalQuantity: number;
}): string {
  if (isPartialFill(order)) {
    return `${order.filled}/${order.totalQuantity}`;
  }
  return String(order.totalQuantity);
}

export function mapOrderStatus(
  raw: string,
  opts?: {
    filled?: number;
    remaining?: number;
    isPendingCancel?: boolean;
    isPendingModify?: boolean;
  },
): MappedOrderStatus {
  const status = (raw ?? "").trim();
  const filled = opts?.filled ?? 0;
  const remaining = opts?.remaining ?? 0;

  if (opts?.isPendingCancel) {
    return { label: "Pending cancel", raw: status, tone: "pending" };
  }
  if (opts?.isPendingModify) {
    return { label: "Pending modify", raw: status, tone: "pending" };
  }
  if (isPartialFill({ filled, remaining })) {
    return { label: "Partial", raw: status, tone: "partial" };
  }

  const lower = status.toLowerCase();

  if (lower === "presubmitted" || lower === "pendingsubmit" || lower === "apipending") {
    return { label: "Queued", raw: status, tone: "working" };
  }
  if (
    lower === "submitted" ||
    lower === "pendingcancel" ||
    lower === "pendingsubmit"
  ) {
    // pendingCancel as raw IB without our local pending flag still shows Working
    // until the client marks isPendingCancel.
    if (lower === "pendingcancel") {
      return { label: "Pending cancel", raw: status, tone: "pending" };
    }
    return { label: "Working", raw: status, tone: "working" };
  }
  if (lower === "inactive") {
    return { label: "Inactive", raw: status, tone: "inactive" };
  }
  if (lower === "filled") {
    return { label: "Filled", raw: status, tone: "done" };
  }
  if (lower.includes("cancel")) {
    return { label: "Cancelled", raw: status, tone: "done" };
  }

  return { label: "Unknown", raw: status || "Unknown", tone: "neutral" };
}

/**
 * Distance from last trade to limit for LMT-style working orders.
 * BUY fills when last drops to/through limit → delta = last − limit.
 * SELL fills when last rises to/through limit → delta = limit − last.
 * Negative delta means the market is through the limit.
 */
export function distanceToFill(params: {
  action: string;
  limitPrice: number | null;
  lastPrice: number | null;
}): DistanceToFill | null {
  const { action, limitPrice, lastPrice } = params;
  if (limitPrice == null || lastPrice == null) return null;
  if (!Number.isFinite(limitPrice) || !Number.isFinite(lastPrice)) return null;

  const isBuy = action === "BUY";
  const delta = isBuy ? lastPrice - limitPrice : limitPrice - lastPrice;
  const abs = Math.abs(delta);

  if (delta <= 0) {
    return { delta, urgency: "through" };
  }
  if (abs <= NEAR_THRESHOLD) {
    return { delta, urgency: "near" };
  }
  return { delta, urgency: "far" };
}

function normalizeRight(right: string | null): "C" | "P" | null {
  if (!right) return null;
  if (right === "C" || right === "CALL") return "C";
  if (right === "P" || right === "PUT") return "P";
  return null;
}

function findStockDirection(
  positions: readonly PortfolioPosition[] | undefined,
  symbol: string,
): "LONG" | "SHORT" | null {
  if (!positions) return null;
  const target = symbol.toUpperCase();
  for (const position of positions) {
    if (position.ticker.toUpperCase() !== target) continue;
    if (position.legs.length !== 1) continue;
    const leg = position.legs[0];
    if (leg?.type !== "Stock") continue;
    if (leg.direction === "LONG" || leg.direction === "SHORT") return leg.direction;
  }
  return null;
}

/**
 * OPEN vs CLOSE from portfolio coverage.
 * SELL vs held LONG or BUY vs held SHORT → CLOSE; otherwise OPEN.
 * BAG/combo without clear single-leg coverage → UNKNOWN.
 */
export function resolveOrderIntent(
  order: OpenOrder,
  portfolio?: readonly PortfolioPosition[],
): OrderIntent {
  const c = order.contract;
  const action = order.action === "SELL" ? "SELL" : order.action === "BUY" ? "BUY" : null;
  if (!action) return "UNKNOWN";

  if (c.secType === "BAG") {
    return "UNKNOWN";
  }

  if (c.secType === "STK") {
    const dir = findStockDirection(portfolio, c.symbol);
    if (!dir) return "OPEN";
    if (action === "SELL" && dir === "LONG") return "CLOSE";
    if (action === "BUY" && dir === "SHORT") return "CLOSE";
    return "OPEN";
  }

  if (c.secType === "OPT") {
    const right = normalizeRight(c.right);
    if (!right || c.strike == null || !c.expiry) return "UNKNOWN";
    const dir = findPortfolioLegDirection(
      portfolio,
      c.symbol,
      c.expiry,
      c.strike,
      right,
    );
    if (!dir) return "OPEN";
    if (action === "SELL" && dir === "LONG") return "CLOSE";
    if (action === "BUY" && dir === "SHORT") return "CLOSE";
    return "OPEN";
  }

  return "UNKNOWN";
}

export function cardToneForIntent(
  intent: OrderIntent,
  action: string,
): "positive" | "negative" | "default" {
  if (intent === "CLOSE") return "default";
  if (action === "SELL") return "negative";
  if (action === "BUY") return "positive";
  return "default";
}

export function summarizeOpenOrders(
  orders: readonly OpenOrder[],
): OpenOrdersSummary {
  let partialCount = 0;
  let workingCount = 0;
  for (const order of orders) {
    if (isPartialFill(order)) {
      partialCount += 1;
    } else {
      workingCount += 1;
    }
  }
  return {
    openCount: orders.length,
    partialCount,
    workingCount,
  };
}

/** Format signed distance for table cells, e.g. +0.30 / -0.05 */
export function formatDistanceDelta(delta: number): string {
  const abs = Math.abs(delta);
  const body =
    abs >= 1 ? abs.toFixed(2) : abs >= 0.1 ? abs.toFixed(2) : abs.toFixed(2);
  if (delta > 0) return `+${body}`;
  if (delta < 0) return `-${body}`;
  return body;
}

export function statusPillClass(tone: OrderStatusTone): string {
  switch (tone) {
    case "partial":
      return "order-status-pill order-status-pill--partial";
    case "pending":
      return "order-status-pill order-status-pill--pending";
    case "working":
      return "order-status-pill order-status-pill--working";
    case "inactive":
      return "order-status-pill order-status-pill--inactive";
    case "done":
      return "order-status-pill order-status-pill--done";
    default:
      return "order-status-pill order-status-pill--neutral";
  }
}
