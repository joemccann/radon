import type { OpenOrder } from "./types";
import { detectStructure, type OrderLeg } from "./optionsChainUtils";
import type { PriceData } from "./pricesProtocol";
import { optionKey } from "./pricesProtocol";

type NormalizedAction = "BUY" | "SELL";
type NormalizedRight = "C" | "P";

type OptionLegCandidate = {
  order: OpenOrder;
  action: NormalizedAction;
  right: NormalizedRight;
  strike: number;
  expiry: string;
  index: number;
};

export type OpenOrderSingleRow = {
  kind: "single";
  order: OpenOrder;
  index: number;
};

export type OpenOrderComboRow = {
  kind: "combo";
  id: string;
  index: number;
  symbol: string;
  structure: string;
  summary: string;
  orders: OpenOrder[];
  totalQuantity: number;
  orderType: string;
  status: string;
  tif: string;
  limitPrice: number | null;
};

export type OpenOrderDisplayRow = OpenOrderSingleRow | OpenOrderComboRow;

export type OpenOrderRowSortKey =
  | "symbol"
  | "action"
  | "orderType"
  | "totalQuantity"
  | "limitPrice"
  | "lastPrice"
  | "status"
  | "tif"
  | "actions";

function normalizeAction(action: string): NormalizedAction | null {
  if (action === "BUY") return "BUY";
  if (action === "SELL") return "SELL";
  return null;
}

function normalizeRight(right: string | null): NormalizedRight | null {
  if (!right) return null;
  if (right === "C" || right === "CALL") return "C";
  if (right === "P" || right === "PUT") return "P";
  return null;
}

function normalizeExpiry(expiry: string | null): string | null {
  if (!expiry) return null;
  const clean = expiry.replace(/-/g, "");
  if (clean.length !== 8) return null;
  return `${clean.slice(0, 4)}-${clean.slice(4, 6)}-${clean.slice(6, 8)}`;
}

function makeComboLeg(order: OpenOrder, index: number): OptionLegCandidate | null {
  if (order.contract.secType !== "OPT") return null;
  const action = normalizeAction(order.action);
  const right = normalizeRight(order.contract.right);
  const expiry = normalizeExpiry(order.contract.expiry);
  if (!action || !right || expiry == null) return null;
  if (order.contract.strike == null) return null;

  return {
    order,
    action,
    right,
    strike: order.contract.strike,
    expiry,
    index,
  };
}

function buildComboGroupKey(candidates: OptionLegCandidate[]): string {
  const first = candidates[0];
  const qty = Math.abs(first.order.totalQuantity);
  const symbol = first.order.contract.symbol.toUpperCase();
  const firstOrder = first.order;
  return `${symbol}|${first.expiry}|${firstOrder.orderType}|${firstOrder.tif}|${qty}`;
}

function isLikelyCombo(candidates: OptionLegCandidate[]): boolean {
  if (candidates.length < 2) return false;

  const rights = new Set(candidates.map((leg) => leg.right));
  const actions = new Set(candidates.map((leg) => leg.action));

  // Avoid collapsing accidental duplicates (same strike, same right, same direction).
  if (rights.size === 1 && actions.size === 1) return false;

  // Require same symbol + same expiry + same size + same order shape already in the key.
  return true;
}

function buildComboStructureAndSummary(candidates: OptionLegCandidate[]): { structure: string; summary: string } {
  const orderedLegs = [...candidates].sort((a, b) => {
    if (a.action !== b.action) return a.action === "SELL" ? -1 : 1;
    if (a.right !== b.right) return a.right === "P" ? -1 : 1;
    return a.strike - b.strike;
  });

  const legs: OrderLeg[] = candidates.map((leg) => ({
    id: `${leg.order.orderId}_${leg.index}`,
    action: leg.action,
    right: leg.right,
    strike: leg.strike,
    expiry: leg.expiry,
    quantity: leg.order.totalQuantity,
    limitPrice: leg.order.limitPrice,
  }));

  const structure = detectStructure(legs);

  const parts = orderedLegs.map((leg) => {
    const side = leg.action === "BUY" ? "Long" : "Short";
    const right = leg.right === "C" ? "Call" : "Put";
    return `${side} ${right} ${leg.strike}`;
  });

  return { structure, summary: `${structure} (${parts.join(" / ")})` };
}

export function resolveOpenOrderComboPrice(orders: OpenOrder[], prices?: Record<string, PriceData>): number | null {
  if (!prices) return null;

  let netLast = 0;

  for (const order of orders) {
    if (order.contract.secType !== "OPT") return null;
    if (order.contract.strike == null || order.contract.right == null || !order.contract.expiry) return null;

    const right = normalizeRight(order.contract.right);
    const expiry = normalizeExpiry(order.contract.expiry);
    if (!right || !expiry) return null;

    const symbol = order.contract.symbol.toUpperCase();
    const key = optionKey({ symbol, expiry: expiry.replace(/-/g, ""), strike: order.contract.strike, right });
    const pd = prices[key];
    if (!pd) return null;

    const quote = pd.last ?? (pd.bid == null || pd.ask == null ? null : (pd.bid + pd.ask) / 2);
    if (quote == null) return null;

    const sign = order.action === "BUY" ? 1 : -1;
    netLast += sign * quote;
  }

  if (!Number.isFinite(netLast)) return null;
  return Math.round(netLast * 100) / 100;
}

export function buildOpenOrderDisplayRows(orders: OpenOrder[]): OpenOrderDisplayRow[] {
  const grouped: Map<string, OptionLegCandidate[]> = new Map();

  orders.forEach((order, index) => {
    const candidate = makeComboLeg(order, index);
    if (!candidate) return;

    const key = buildComboGroupKey([candidate]);
    const existing = grouped.get(key) ?? [];
    existing.push(candidate);
    grouped.set(key, existing);
  });

  const comboRows: OpenOrderComboRow[] = [];
  const groupedIndices = new Set<number>();

  for (const candidates of grouped.values()) {
    if (!isLikelyCombo(candidates)) {
      continue;
    }

    const { structure, summary } = buildComboStructureAndSummary(candidates);
    if (!structure) {
      continue;
    }

    const ordersInCombo = candidates.map((candidate) => candidate.order);
    const totalQuantity = ordersInCombo[0].totalQuantity;
    const firstOrder = ordersInCombo[0];

    const sameLimit = ordersInCombo.every((o) => o.limitPrice === firstOrder.limitPrice);
    const limitPrice = sameLimit ? firstOrder.limitPrice : null;

    const sameTif = ordersInCombo.every((o) => o.tif === firstOrder.tif);
    const sameStatus = ordersInCombo.every((o) => o.status === firstOrder.status);
    const symbol = firstOrder.contract.symbol.toUpperCase();

    const combo: OpenOrderComboRow = {
      kind: "combo",
      id: `combo-${symbol}-${candidates[0].expiry}-${candidates[0].index}`,
      index: candidates[0].index,
      symbol,
      structure,
      summary,
      orders: ordersInCombo,
      totalQuantity,
      orderType: structure,
      status: sameStatus ? firstOrder.status : "MIXED",
      tif: sameTif ? firstOrder.tif : "MIXED",
      limitPrice,
    };

    for (const candidate of candidates) {
      groupedIndices.add(candidate.index);
    }

    comboRows.push(combo);
  }

  const singleRows: OpenOrderSingleRow[] = [];
  orders.forEach((order, index) => {
    if (groupedIndices.has(index)) return;
    singleRows.push({ kind: "single", order, index });
  });

  const allRows: OpenOrderDisplayRow[] = [...singleRows, ...comboRows];
  return allRows.sort((a, b) => {
    const orderA = a.index;
    const orderB = b.index;
    return orderA - orderB;
  });
}
