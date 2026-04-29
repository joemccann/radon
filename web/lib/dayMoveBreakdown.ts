/**
 * Day Move breakdown — pure functions extracted from MetricCards.tsx
 * so they can be unit-tested without importing a React client component.
 */

import type { PortfolioData } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { legPriceKey, resolveRealtimePrice } from "@/lib/positionUtils";
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

export function computeDayMoveBreakdown(
  portfolio: PortfolioData,
  prices: Record<string, PriceData>,
): { rows: PnlBreakdownRow[]; total: number } {
  let total = 0;
  const rows: PnlBreakdownRow[] = [];

  for (const pos of portfolio.positions) {
    if (pos.structure_type === "Stock") {
      const p = prices[pos.ticker];
      const current = p ? resolveLastOrMid(p) : null;
      if (current == null || p?.close == null || p.close <= 0) continue;

      const wsPnl = (current - p.close) * pos.contracts;
      const effectivePnl = pos.ib_daily_pnl != null ? pos.ib_daily_pnl : wsPnl;
      total += effectivePnl;
      const closeValue = p.close * pos.contracts;
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
      const effectivePnl = pos.ib_daily_pnl != null ? pos.ib_daily_pnl : legPnl;
      total += effectivePnl;
      rows.push({
        id: pos.id,
        ticker: pos.ticker,
        structure: pos.structure,
        col1: closeStr || "---",
        col2: lastStr || "---",
        pnl: effectivePnl,
        pnlPct: null,
      });
    }
  }

  return { rows, total };
}
