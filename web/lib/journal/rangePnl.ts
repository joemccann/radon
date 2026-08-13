/**
 * Date-range realized P&L for the Trade Journal.
 *
 * Default semantics: sum realized_pnl for trades that **closed** in an
 * inclusive America/New_York calendar-day range [from, to].
 */

import type { TradeEntry } from "@/lib/types";
import { ET_TZ, etCalendarDateString } from "@/lib/orders/executedToday";

export type JournalRangeMode = "closed" | "activity";

export type JournalRangePreset = "today" | "7d" | "mtd" | "ytd" | "all" | "custom";

export type JournalRangeSummary = {
  realizedPnl: number;
  closedCount: number;
  openCount: number;
  winners: number;
  losers: number;
  tradeCount: number;
  scratches: number;
};

const CLOSE_ACTIONS = new Set([
  "CLOSED",
  "SELL_OPTION",
  "BUY_TO_CLOSE",
  "SELL",
]);

const CLOSE_DECISIONS = new Set(["CLOSED", "FREED", "CONVERTED"]);

/** Normalize any timestamp-ish string to ET YYYY-MM-DD, or null. */
export function toEtDay(raw: string | null | undefined): string | null {
  if (!raw) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
  const prefix = raw.slice(0, 10);
  if (/^\d{4}-\d{2}-\d{2}$/.test(prefix) && Number.isNaN(Date.parse(raw))) {
    return prefix;
  }
  const ms = Date.parse(raw);
  if (Number.isNaN(ms)) {
    return /^\d{4}-\d{2}-\d{2}$/.test(prefix) ? prefix : null;
  }
  return etCalendarDateString(new Date(ms));
}

/**
 * Best available activity / display date for a journal row (opens and closes).
 * Prefer filled_at, then date, then close_date.
 */
export function journalActivityDate(trade: TradeEntry): string | null {
  const ext = trade as TradeEntry & { filled_at?: string };
  return (
    toEtDay(ext.filled_at) ??
    toEtDay(trade.date) ??
    toEtDay(trade.close_date) ??
    null
  );
}

/**
 * Close date for realized-P&L inclusion.
 * Prefer close_date; else filled_at/date when the row is a close.
 */
export function journalCloseDate(trade: TradeEntry): string | null {
  const close = toEtDay(trade.close_date);
  if (close) return close;
  if (!isClosedTrade(trade)) return null;
  const ext = trade as TradeEntry & { filled_at?: string };
  return toEtDay(ext.filled_at) ?? toEtDay(trade.date) ?? null;
}

export function isClosedTrade(trade: TradeEntry): boolean {
  if (typeof trade.realized_pnl === "number"
      && Number.isFinite(trade.realized_pnl)
      && trade.realized_pnl !== 0) {
    return true;
  }
  const action = (trade.action ?? "").toUpperCase();
  if (CLOSE_ACTIONS.has(action)) return true;
  const decision = (trade.decision ?? "").toUpperCase();
  if (CLOSE_DECISIONS.has(decision)) return true;
  return false;
}

function dayInInclusiveRange(day: string, from: string, to: string): boolean {
  return day >= from && day <= to;
}

/**
 * Filter trades for a range.
 * - closed: closed trades whose close date is in [from, to]
 * - activity: any trade whose activity date is in [from, to]
 * Empty from/to treated as unbounded on that side.
 */
export function filterTradesByRange(
  trades: readonly TradeEntry[],
  from: string | null,
  to: string | null,
  mode: JournalRangeMode = "closed",
): TradeEntry[] {
  const fromDay = from && from.length > 0 ? from : null;
  const toDay = to && to.length > 0 ? to : null;

  return trades.filter((t) => {
    if (mode === "closed") {
      if (!isClosedTrade(t)) return false;
      const day = journalCloseDate(t);
      if (!day) return false;
      if (fromDay && day < fromDay) return false;
      if (toDay && day > toDay) return false;
      return true;
    }
    // activity
    const day = journalActivityDate(t);
    if (!day) return false;
    if (fromDay && day < fromDay) return false;
    if (toDay && day > toDay) return false;
    return true;
  });
}

/**
 * Realized P&L summary for a set of trades (typically already range-filtered).
 * Sums only finite realized_pnl; open rows without P&L contribute openCount.
 */
export function summarizeRangePnl(trades: readonly TradeEntry[]): JournalRangeSummary {
  let realizedPnl = 0;
  let closedCount = 0;
  let openCount = 0;
  let winners = 0;
  let losers = 0;
  let scratches = 0;

  for (const t of trades) {
    if (isClosedTrade(t) && typeof t.realized_pnl === "number" && Number.isFinite(t.realized_pnl)) {
      closedCount += 1;
      realizedPnl += t.realized_pnl;
      if (t.realized_pnl > 0) winners += 1;
      else if (t.realized_pnl < 0) losers += 1;
      else scratches += 1;
    } else if (!isClosedTrade(t)) {
      openCount += 1;
    } else {
      // Closed-ish without numeric P&L
      closedCount += 1;
      scratches += 1;
    }
  }

  return {
    realizedPnl,
    closedCount,
    openCount,
    winners,
    losers,
    tradeCount: trades.length,
    scratches,
  };
}

/** Shift an ET YYYY-MM-DD by `deltaDays` (calendar arithmetic via UTC noon). */
export function shiftEtDay(day: string, deltaDays: number): string {
  const [y, m, d] = day.split("-").map(Number);
  const utc = new Date(Date.UTC(y, m - 1, d + deltaDays, 12, 0, 0));
  return utc.toISOString().slice(0, 10);
}

export function firstOfMonthEt(day: string): string {
  return `${day.slice(0, 7)}-01`;
}

export function firstOfYearEt(day: string): string {
  return `${day.slice(0, 4)}-01-01`;
}

/**
 * Resolve preset → inclusive [from, to] ET dates.
 * `all` returns null bounds (no date filter).
 */
export function rangeForPreset(
  preset: JournalRangePreset,
  now: Date = new Date(),
): { from: string | null; to: string | null } {
  const today = etCalendarDateString(now);
  switch (preset) {
    case "today":
      return { from: today, to: today };
    case "7d":
      return { from: shiftEtDay(today, -6), to: today };
    case "mtd":
      return { from: firstOfMonthEt(today), to: today };
    case "ytd":
      return { from: firstOfYearEt(today), to: today };
    case "all":
      return { from: null, to: null };
    case "custom":
      return { from: null, to: null };
    default:
      return { from: firstOfMonthEt(today), to: today };
  }
}

export { ET_TZ, etCalendarDateString, dayInInclusiveRange };
