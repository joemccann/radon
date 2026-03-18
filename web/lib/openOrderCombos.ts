import type { ExecutedOrder, OpenOrder, PortfolioPosition } from "./types";
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
  summary: string | null;
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

function formatStrike(strike: number): string {
  return Number.isInteger(strike) ? String(strike) : String(strike);
}

function findPortfolioLegDirection(
  positions: readonly PortfolioPosition[] | undefined,
  symbol: string,
  expiry: string,
  strike: number,
  right: NormalizedRight,
): "LONG" | "SHORT" | null {
  if (!positions) return null;
  const targetSymbol = symbol.toUpperCase();
  const targetExpiry = expiry;
  for (const position of positions) {
    if (position.ticker.toUpperCase() !== targetSymbol) continue;
    if (position.expiry !== targetExpiry) continue;
    for (const leg of position.legs) {
      if (leg.type !== (right === "C" ? "Call" : "Put")) continue;
      if (leg.strike !== strike) continue;
      if (leg.direction === "LONG" || leg.direction === "SHORT") return leg.direction;
    }
  }
  return null;
}

function findSingleStockDirection(
  positions: readonly PortfolioPosition[] | undefined,
  symbol: string,
): "LONG" | "SHORT" | null {
  if (!positions) return null;
  const targetSymbol = symbol.toUpperCase();
  for (const position of positions) {
    if (position.ticker.toUpperCase() !== targetSymbol) continue;
    if (position.legs.length !== 1) continue;
    const leg = position.legs[0];
    if (leg?.type !== "Stock") continue;
    if (leg.direction === "LONG" || leg.direction === "SHORT") return leg.direction;
  }
  return null;
}

function buildSingleOrderSummary(
  order: OpenOrder,
  portfolioPositions?: readonly PortfolioPosition[],
): string | null {
  if (order.contract.secType === "OPT") {
    const right = normalizeRight(order.contract.right);
    const expiry = normalizeExpiry(order.contract.expiry);
    const strike = order.contract.strike;
    if (!right || expiry == null || strike == null) return "Option";

    const portfolioDirection = findPortfolioLegDirection(
      portfolioPositions,
      order.contract.symbol,
      expiry,
      strike,
      right,
    );
    const direction = portfolioDirection ?? (order.action === "BUY" ? "LONG" : "SHORT");
    const directionLabel = direction === "LONG" ? "Long" : "Short";
    const optionType = right === "C" ? "Call" : "Put";

    return `${directionLabel} $${formatStrike(strike)} ${optionType} ${expiry}`;
  }

  if (order.contract.secType === "STK") {
    const portfolioDirection = findSingleStockDirection(portfolioPositions, order.contract.symbol);
    const direction = portfolioDirection ?? (order.action === "BUY" ? "LONG" : "SHORT");
    return `${direction === "LONG" ? "Long" : "Short"} Stock`;
  }

  if (order.contract.secType === "BAG") {
    return "Combo";
  }

  return order.contract.secType || null;
}

function formatExecutedLegDirection(
  side: string,
  isClosing: boolean,
): "Long" | "Short" | null {
  if (side === "SLD" || side === "SELL") return isClosing ? "Long" : "Short";
  if (side === "BOT" || side === "BUY") return isClosing ? "Short" : "Long";
  return null;
}

function makeExecutedLegKey(order: ExecutedOrder): string {
  if (order.contract.conId != null) return `${order.contract.conId}`;
  const symbol = order.contract.symbol;
  const right = order.contract.right ?? "";
  const strike = order.contract.strike == null ? "" : order.contract.strike;
  const expiry = order.contract.expiry ?? "";
  return `${symbol}|${right}|${expiry}|${strike}`;
}

function inferExecutedLegDirectionFromFills(
  fills: ExecutedOrder[],
  isClosing: boolean,
): "Long" | "Short" | null {
  let latestDirection: "Long" | "Short" | null = null;
  let latestTime = Number.NEGATIVE_INFINITY;

  for (const fill of fills) {
    const direction = formatExecutedLegDirection(fill.side, isClosing);
    if (direction == null) continue;
    const t = Date.parse(fill.time);
    if (Number.isNaN(t)) {
      if (latestDirection == null) {
        latestDirection = direction;
      }
      continue;
    }
    if (t > latestTime) {
      latestTime = t;
      latestDirection = direction;
    }
  }

  return latestDirection;
}

export function buildExecutedGroupDescription(
  fills: ExecutedOrder[],
  isClosing: boolean,
  portfolioPositions?: readonly PortfolioPosition[],
): string {
  const first = fills[0];
  if (!first) return "Unknown";

  if (first.contract.secType !== "OPT") {
    const side = first.side === "BOT"
      ? (isClosing ? "Closed" : "Bought")
      : (isClosing ? "Closed" : "Sold");
    return `${side} ${first.contract.symbol}`;
  }

  const legs = fills.filter((fill) => fill.contract.secType === "OPT");
  if (legs.length === 0) {
    const side = first.side === "BOT"
      ? (isClosing ? "Closed" : "Bought")
      : (isClosing ? "Closed" : "Sold");
    return `${side} ${first.contract.symbol}`;
  }

  const legGroups = new Map<string, ExecutedOrder[]>();
  for (const leg of legs) {
    const key = makeExecutedLegKey(leg);
    const existing = legGroups.get(key) ?? [];
    existing.push(leg);
    legGroups.set(key, existing);
  }

  const parts: string[] = [];
  for (const legGroup of legGroups.values()) {
    const c = legGroup[0].contract;
    const right = c.right === "C" || c.right === "CALL"
      ? "Call"
      : c.right === "P" || c.right === "PUT"
        ? "Put"
        : "Unknown";
    const strike = c.strike != null ? `$${c.strike}` : "Unknown";

    const explicitDir = inferExecutedLegDirectionFromFills(legGroup, isClosing);
    const portfolioDir = c.right == null || c.expiry == null || c.strike == null
      ? null
      : findPortfolioLegDirection(
          portfolioPositions,
          c.symbol,
          c.expiry,
          c.strike,
          c.right === "C" || c.right === "CALL" ? "C" : "P",
      );

    const direction = explicitDir
      ? explicitDir
      : portfolioDir
        ? (portfolioDir === "LONG" ? "Long" : "Short")
        : "Long";
    parts.push(`${direction} ${strike} ${right}`);
  }

  if (parts.length === 2) {
    parts.sort((a, b) => {
      const aIsShort = a.startsWith("Short");
      const bIsShort = b.startsWith("Short");
      if (aIsShort === bIsShort) return 0;
      return aIsShort ? -1 : 1;
    });
  }

  const base = isClosing ? "Closed" : "Opened";
  const structure = parts.length === 2 ? "Risk Reversal" : parts.length > 2 ? "Combo" : "";
  return `${base} ${fills[0].contract.symbol} ${structure} (${parts.join(" / ")})`;
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

function buildComboStructureAndSummary(
  candidates: OptionLegCandidate[],
  portfolioPositions?: readonly PortfolioPosition[],
): { structure: string; summary: string } {
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

  let parts: string[];

  if (structure === "Risk Reversal") {
    const putLeg = candidates.find((leg) => leg.right === "P");
    const callLeg = candidates.find((leg) => leg.right === "C");

    if (putLeg && callLeg) {
      const putDirection = findPortfolioLegDirection(
        portfolioPositions,
        putLeg.order.contract.symbol,
        putLeg.expiry,
        putLeg.strike,
        "P",
      );
      const callDirection = findPortfolioLegDirection(
        portfolioPositions,
        callLeg.order.contract.symbol,
        callLeg.expiry,
        callLeg.strike,
        "C",
      );

      if (putDirection && callDirection) {
        const putSummary = `${putDirection === "LONG" ? "Long" : "Short"} Put ${putLeg.strike}`;
        const callSummary = `${callDirection === "LONG" ? "Long" : "Short"} Call ${callLeg.strike}`;

        parts = [putSummary, callSummary].sort((a, b) => {
          const aIsShort = a.startsWith("Short");
          const bIsShort = b.startsWith("Short");
          if (aIsShort === bIsShort) return 0;
          return aIsShort ? -1 : 1;
        });
      } else {
        const isBearish = putLeg.strike > callLeg.strike;
        const shortLeg = isBearish ? callLeg : putLeg;
        const longLeg = isBearish ? putLeg : callLeg;

        const shortLabel = `${shortLeg.right === "C" ? "Call" : "Put"}`;
        const longLabel = `${longLeg.right === "C" ? "Call" : "Put"}`;
        parts = [
          `Short ${shortLabel} ${shortLeg.strike}`,
          `Long ${longLabel} ${longLeg.strike}`,
        ];
      }
    } else {
      parts = [];
    }
  } else {
    const orderedLegs = [...candidates].sort((a, b) => {
      if (a.action !== b.action) return a.action === "SELL" ? -1 : 1;
      if (a.right !== b.right) return a.right === "P" ? -1 : 1;
      return a.strike - b.strike;
    });

    parts = orderedLegs.map((leg) => {
      const side = leg.action === "BUY" ? "Long" : "Short";
      const right = leg.right === "C" ? "Call" : "Put";
      return `${side} ${right} ${leg.strike}`;
    });
  }

  return { structure, summary: `${structure} (${parts.join(" / ")})` };
}

export function resolveOpenOrderComboPrice(orders: OpenOrder[], prices?: Record<string, PriceData>): number | null {
  if (!prices) return null;
  if (orders.length === 0) return null;

  const nonZeroLegSizes = orders.map((order) => Math.abs(order.totalQuantity)).filter((q) => q > 0);
  if (nonZeroLegSizes.length === 0) return null;
  const baseQuantity = Math.min(...nonZeroLegSizes);

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
    const quantityScale = Math.abs(order.totalQuantity) / baseQuantity;
    if (!Number.isFinite(quantityScale) || quantityScale <= 0) return null;
    netLast += sign * quote * quantityScale;
  }

  if (!Number.isFinite(netLast)) return null;
  return Math.round(netLast * 100) / 100;
}

export function buildOpenOrderDisplayRows(
  orders: OpenOrder[],
  portfolioPositions?: readonly PortfolioPosition[],
): OpenOrderDisplayRow[] {
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

    const { structure, summary } = buildComboStructureAndSummary(candidates, portfolioPositions);
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
    singleRows.push({
      kind: "single",
      order,
      index,
      summary: buildSingleOrderSummary(order, portfolioPositions),
    });
  });

  const allRows: OpenOrderDisplayRow[] = [...singleRows, ...comboRows];
  return allRows.sort((a, b) => {
    const orderA = a.index;
    const orderB = b.index;
    return orderA - orderB;
  });
}
