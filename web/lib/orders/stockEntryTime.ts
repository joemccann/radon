import type { ExecutedOrder } from "../types";

type FillGroup = { isClosing: boolean; fills: ExecutedOrder[] };
type Lot = { quantity: number; time: string; side: "BUY" | "SELL" };

function stockKey(fill: ExecutedOrder): string | null {
  const contract = fill.contract;
  if (contract.secType !== "STK" || contract.conId == null || !Number.isFinite(contract.conId) || contract.conId <= 0) return null;
  return `STK:${contract.conId}`;
}

function side(fill: ExecutedOrder): "BUY" | "SELL" | null {
  if (fill.side === "BOT" || fill.side === "BUY") return "BUY";
  if (fill.side === "SLD" || fill.side === "SELL") return "SELL";
  return null;
}

/** Recover stock entry timing from fully matched visible executions. Earlier
 * closes consume opening lots before the target close, so a re-open cannot
 * inherit the previous trade's timestamp. Timing only: preserve existing P&L
 * and basis conventions. Insufficient history returns null. */
export function resolveStockEntryTime(target: FillGroup, groups: readonly FillGroup[] | undefined): string | null {
  if (!groups || !target.isClosing || target.fills.length === 0 || target.fills.some((f) => !stockKey(f))) return null;
  const targetIds = new Set(target.fills.map((f) => f.execId));
  if (targetIds.size !== target.fills.length) return null;
  const keys = new Set(target.fills.map((f) => stockKey(f)!));
  const targetTimes = target.fills.map((f) => Date.parse(f.time));
  if (targetTimes.some((time) => !Number.isFinite(time))) return null;
  const lastTargetTime = Math.max(...targetTimes);
  const events = groups.flatMap((group) => group.fills
    .filter((fill) => keys.has(stockKey(fill) ?? ""))
    .map((fill) => ({ fill, closing: group.isClosing })));
  // An unorderable fill makes consumption ambiguous, even if the target's own
  // timestamp is valid. Never guess that it belongs before or after the close.
  if (events.some(({ fill }) => !Number.isFinite(Date.parse(fill.time)))) return null;
  events.sort((a, b) => Date.parse(a.fill.time) - Date.parse(b.fill.time));

  const lots = new Map<string, Lot[]>();
  const seen = new Set<string>();
  let matchedTargets = 0;
  let earliest: string | null = null;
  for (const { fill, closing } of events) {
    if (Date.parse(fill.time) > lastTargetTime) break;
    if (seen.has(fill.execId)) continue;
    seen.add(fill.execId);
    const direction = side(fill);
    if (!direction || !Number.isFinite(fill.quantity) || fill.quantity <= 0) return null;
    const key = stockKey(fill)!;
    const queue = lots.get(key) ?? [];
    if (!closing) {
      // Opposite-side visible inventory makes an "opening" classification
      // suspect (e.g. a close with its commission report still pending).
      if (queue.some((lot) => lot.side !== direction)) return null;
      queue.push({ quantity: fill.quantity, time: fill.time, side: direction });
      lots.set(key, queue);
      continue;
    }

    let remaining = fill.quantity;
    const isTarget = targetIds.has(fill.execId);
    while (remaining > 0 && queue.length > 0) {
      const lot = queue[0];
      if (lot.side === direction) break;
      const taken = Math.min(remaining, lot.quantity);
      if (isTarget && (earliest == null || Date.parse(lot.time) < Date.parse(earliest))) earliest = lot.time;
      remaining -= taken;
      lot.quantity -= taken;
      if (lot.quantity === 0) queue.shift();
    }
    if (isTarget) {
      if (remaining > 0) return null;
      matchedTargets += 1;
    }
    lots.set(key, queue);
  }
  return matchedTargets === targetIds.size ? earliest : null;
}
