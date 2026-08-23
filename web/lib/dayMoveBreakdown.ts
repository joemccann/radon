/**
 * Day Move breakdown — pure functions extracted from MetricCards.tsx
 * so they can be unit-tested without importing a React client component.
 */

import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import {
  getTodayPnlDollars,
  isSameDay,
  legPriceKey,
  positionDirectionSign,
  resolveRealtimePrice,
} from "@/lib/positionUtils";
import type { PnlBreakdownRow } from "@/components/PnlBreakdownModal";

/**
 * Resolve the "current price" for a position's price data.
 *
 * Day-Move-specific: NEVER falls back to close. A close-based "current"
 * would yield (close − close) = 0 and pollute the breakdown with zero rows
 * for stale subscriptions. The panel is only meaningful when a live tick or
 * mid is available.
 *
 * Priority:
 *   1. `last` if it exists and is > 0
 *   2. `(bid + ask) / 2` if both bid and ask are defined and > 0
 *   3. null — position should be excluded from the Day Move calculation
 */
export function resolveLastOrMid(p: PriceData): number | null {
  const r = resolveRealtimePrice(p);
  if (r.price == null) return null;
  // The portfolio chain falls back to close as a final tier (marked
  // isCalculated=true). For day-move we treat that case as "no live data".
  if (r.isCalculated && p.close != null && p.close > 0 && r.price === p.close) {
    return null;
  }
  return r.price;
}

/** Returns true when the resolved price came from the mid (bid/ask), not last. */
function isMid(p: PriceData): boolean {
  const r = resolveRealtimePrice(p);
  if (r.price == null || !r.isCalculated) return false;
  // Exclude the close fallback case — only a real bid/ask mid counts as MID.
  if (p.close != null && p.close > 0 && r.price === p.close) return false;
  return true;
}

/** Optional close/last labels for the breakdown modal when quotes exist. */
function quoteLabelsForPosition(
  pos: PortfolioPosition,
  prices: Record<string, PriceData>,
): { col1: string; col2: string; pnlPct: number | null } {
  if (pos.structure_type === "Stock") {
    const p = prices[pos.ticker];
    const current = p ? resolveLastOrMid(p) : null;
    if (p?.close != null && p.close > 0 && current != null) {
      const closeValue = p.close * Math.abs(pos.contracts);
      const pnlPct =
        pos.ib_daily_pnl != null && closeValue !== 0
          ? (pos.ib_daily_pnl / Math.abs(closeValue)) * 100
          : null;
      return {
        col1: `$${p.close.toFixed(2)}`,
        col2: isMid(p) ? `$${current.toFixed(2)} (MID)` : `$${current.toFixed(2)}`,
        pnlPct,
      };
    }
    return { col1: "IB daily", col2: "reqPnLSingle", pnlPct: null };
  }

  let closeStr = "";
  let lastStr = "";
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    const lp = key ? prices[key] : null;
    const current = lp ? resolveLastOrMid(lp) : null;
    if (!lp || current == null || lp.close == null || lp.close <= 0) continue;
    if (!closeStr) closeStr = `$${lp.close.toFixed(2)}`;
    if (!lastStr) {
      lastStr = isMid(lp)
        ? `$${current.toFixed(2)} (MID)`
        : `$${current.toFixed(2)}`;
    }
  }
  if (closeStr && lastStr) {
    return { col1: closeStr, col2: lastStr, pnlPct: null };
  }
  return { col1: "IB daily", col2: "reqPnLSingle", pnlPct: null };
}

/**
 * Per-position day move for the Account Day P&L fallback and the Day Move
 * breakdown modal.
 *
 * Precedence (must match operator expectation vs TWS):
 *   1. `pos.ib_daily_pnl` — IB reqPnLSingle, no quotes required. Handles
 *      same-day opens and intraday adds correctly.
 *   2. Same-day positions (stocks AND options) — entry-cost baseline via
 *      getTodayPnlDollars (prior close is meaningless when the position did
 *      not exist yesterday).
 *   3. Overnight stock — close-based with SHORT sign awareness + last/mid.
 *   4. Overnight options — last/mid vs prior close.
 */
export function computeDayMoveBreakdown(
  portfolio: PortfolioData,
  prices: Record<string, PriceData>,
): { rows: PnlBreakdownRow[]; total: number } {
  let total = 0;
  const rows: PnlBreakdownRow[] = [];

  for (const pos of portfolio.positions) {
    // 1. IB per-position daily is authoritative — never gate on live quotes.
    //    Missing bid/ask/last used to drop the row or fall through to prior-
    //    close math and blow up ESTIMATED (LIVE) Day P&L during RTH.
    if (pos.ib_daily_pnl != null) {
      total += pos.ib_daily_pnl;
      const labels = quoteLabelsForPosition(pos, prices);
      rows.push({
        id: pos.id,
        ticker: pos.ticker,
        structure: pos.structure,
        col1: labels.col1,
        col2: labels.col2,
        pnl: pos.ib_daily_pnl,
        pnlPct: labels.pnlPct,
      });
      continue;
    }

    // 2. Same-day positions: entry baseline (shared with position-table Today
    //    P&L). Equities included — a stock opened today has no prior close of
    //    its own either, and a stock-only exception here made the Day P&L card
    //    disagree with the Today P&L column it summarises.
    if (isSameDay(pos)) {
      const todayPnl = getTodayPnlDollars(pos, prices);
      if (todayPnl == null) continue;
      total += todayPnl;
      rows.push({
        id: pos.id,
        ticker: pos.ticker,
        structure: pos.structure,
        col1: "entry",
        col2: "same-day",
        pnl: todayPnl,
        pnlPct: null,
      });
      continue;
    }

    if (pos.structure_type === "Stock" || pos.structure_type === "Crypto") {
      const p = prices[pos.ticker];
      const current = p ? resolveLastOrMid(p) : null;
      if (current == null || p?.close == null || p.close <= 0) continue;

      // Sign-aware: contracts is a magnitude; SHORT loses when price rises.
      const effectivePnl =
        positionDirectionSign(pos) * (current - p.close) * Math.abs(pos.contracts);
      total += effectivePnl;
      const closeValue = p.close * Math.abs(pos.contracts);
      const pnlPct = closeValue !== 0 ? (effectivePnl / Math.abs(closeValue)) * 100 : null;
      const currentLabel = isMid(p)
        ? `$${current.toFixed(2)} (MID)`
        : `$${current.toFixed(2)}`;
      rows.push({
        id: pos.id,
        ticker: pos.ticker,
        structure: pos.structure,
        col1: `$${p.close.toFixed(2)}`,
        col2: currentLabel,
        pnl: effectivePnl,
        pnlPct,
      });
      continue;
    }

    // 3. Overnight options: last-or-mid vs prior close.
    let legPnl = 0;
    let allLegsValid = true;
    let closeStr = "";
    let lastStr = "";

    for (const leg of pos.legs) {
      const key = legPriceKey(pos.ticker, pos.expiry, leg);
      const lp = key ? prices[key] : null;
      const current = lp ? resolveLastOrMid(lp) : null;
      if (!lp || current == null || lp.close == null || lp.close <= 0) {
        allLegsValid = false;
        break;
      }
      const sign = leg.direction === "LONG" ? 1 : -1;
      legPnl += sign * (current - lp.close) * leg.contracts * 100;
      if (!closeStr) closeStr = `$${lp.close.toFixed(2)}`;
      if (!lastStr) {
        lastStr = isMid(lp)
          ? `$${current.toFixed(2)} (MID)`
          : `$${current.toFixed(2)}`;
      }
    }

    if (allLegsValid) {
      total += legPnl;
      rows.push({
        id: pos.id,
        ticker: pos.ticker,
        structure: pos.structure,
        col1: closeStr || "---",
        col2: lastStr || "---",
        pnl: legPnl,
        pnlPct: null,
      });
    }
  }

  return { rows, total };
}
