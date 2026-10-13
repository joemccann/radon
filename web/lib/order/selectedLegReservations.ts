import type { OrdersData, PortfolioPosition } from "@/lib/types";
import { heldPositionTradeUnits, resolvePositionTradeTarget, type TradeTarget } from "./positionTrade";

type Side = "BUY" | "SELL";
type ReservedLeg = { key: string; side: Side; ratio: number };

const TERMINAL = new Set(["FILLED", "CANCELLED", "APICANCELLED", "INACTIVE"]);

function sideOf(value: unknown): Side | null {
  const side = String(value ?? "").toUpperCase();
  return side === "BUY" || side === "SELL" ? side : null;
}

function identity(contract: { expiry?: unknown; strike?: unknown; right?: unknown }): string | null {
  const expiry = String(contract.expiry ?? "").replace(/-/g, "");
  const strike = contract.strike;
  const right = String(contract.right ?? "").toUpperCase();
  if (!/^\d{8}$/.test(expiry) || typeof strike !== "number" || !Number.isFinite(strike) || strike <= 0) return null;
  if (right !== "C" && right !== "P") return null;
  return `${expiry}|${right}|${strike}`;
}

/**
 * Units that can still close the selected held legs after existing working
 * orders fill. A BAG's SELL envelope reverses its structure actions; reserve
 * raw remaining contracts on each exact option identity, then reduce those
 * contracts back to units of the selected structure.
 *
 * null means the orders snapshot has not resolved. Incomplete same-ticker
 * option/BAG identities conservatively consume all availability so an unknown
 * working close cannot be classified as an additional harmless close.
 */
export function availablePositionTradeUnits(
  position: PortfolioPosition,
  target: TradeTarget,
  orders: Pick<OrdersData, "open_orders"> | null | undefined,
): number | null {
  if (orders == null || !Array.isArray(orders.open_orders)) return null;
  const selected = resolvePositionTradeTarget(position, target);
  const heldUnits = heldPositionTradeUnits(position, target);
  if (selected == null || heldUnits <= 0) return 0;

  const selectedLegs = new Map<string, { held: number; ratio: number; reserved: number }>();
  for (const leg of selected.legs) {
    const key = identity({ expiry: leg.expiry ?? position.expiry, strike: leg.strike,
      right: leg.type === "Call" ? "C" : leg.type === "Put" ? "P" : null });
    if (key == null || !Number.isSafeInteger(leg.contracts) || leg.contracts <= 0) return 0;
    const side = leg.direction === "LONG" ? "SELL" : "BUY";
    const sideKey = `${key}|${side}`;
    const held = selectedLegs.get(sideKey) ?? { held: 0, ratio: 0, reserved: 0 };
    held.held += leg.contracts;
    held.ratio += leg.contracts / heldUnits;
    selectedLegs.set(sideKey, held);
  }

  const ticker = position.ticker.toUpperCase();
  for (const row of orders.open_orders) {
    if (row == null || typeof row !== "object") return null;
    if (TERMINAL.has(String(row.status ?? "").toUpperCase())) continue;
    const contract = row.contract;
    if (String(contract?.symbol || row.symbol || "").toUpperCase() !== ticker) continue;
    const secType = String(contract?.secType ?? "").toUpperCase();
    if (!secType) return 0;
    if (secType !== "OPT" && secType !== "BAG") continue;

    const remaining = row.remaining ?? (row.totalQuantity - (row.filled ?? 0));
    if (!Number.isSafeInteger(remaining) || remaining < 0) return 0;
    if (remaining === 0) continue;
    const envelope = sideOf(row.action);
    if (envelope == null) return 0;
    const economicLegs: ReservedLeg[] = [];
    if (secType === "OPT") {
      const key = identity(contract);
      if (key == null) return 0;
      economicLegs.push({ key, side: envelope, ratio: 1 });
    } else {
      if (!Array.isArray(contract.comboLegs) || contract.comboLegs.length < 2) return 0;
      for (const leg of contract.comboLegs) {
        if (leg == null || typeof leg !== "object") return 0;
        const key = identity(leg);
        const structureSide = sideOf(leg.action);
        if (key == null || structureSide == null || !Number.isSafeInteger(leg.ratio) || leg.ratio <= 0) return 0;
        const side = envelope === "SELL" ? (structureSide === "BUY" ? "SELL" : "BUY") : structureSide;
        economicLegs.push({ key, side, ratio: leg.ratio });
      }
    }

    for (const leg of economicLegs) {
      const selectedLeg = selectedLegs.get(`${leg.key}|${leg.side}`);
      if (selectedLeg) selectedLeg.reserved += remaining * leg.ratio;
    }
  }

  return Math.max(0, Math.min(heldUnits, ...[...selectedLegs.values()].map(leg =>
    Math.floor((leg.held - leg.reserved) / leg.ratio))));
}
