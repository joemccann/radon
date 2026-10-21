/**
 * Day Move breakdown — pure functions extracted from MetricCards.tsx
 * so they can be unit-tested without importing a React client component.
 */

import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import {
  getTodayPnlDollars,
  isMixedAgeCombo,
  isSameDay,
  legPriceKey,
  positionDirectionSign,
  resolveRealtimePrice,
} from "@/lib/positionUtils";
import { fmtSigned } from "@/lib/format/money";
import type { PnlBreakdownFlag, PnlBreakdownRow } from "@/components/PnlBreakdownModal";

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

/** Signed price for the modal: credits (net short premium) print negative. */
function fmtPrice(value: number): string {
  return `${value < 0 ? "-" : ""}$${Math.abs(value).toFixed(2)}`;
}

function fmtCurrent(value: number, mid: boolean): string {
  return mid ? `${fmtPrice(value)} (MID)` : fmtPrice(value);
}

/**
 * What live quotes say about a position's day: the CLOSE / CURRENT labels,
 * the absolute baseline the % column divides by, and the dollar move the
 * quotes alone imply (sign-aware). Null when any leg lacks a live mark or a
 * prior close, so a combo is never described by one of its legs.
 */
type QuoteContext = {
  col1: string;
  col2: string;
  closeValue: number;
  quotedMove: number;
};

function stockQuoteContext(
  pos: PortfolioPosition,
  prices: Record<string, PriceData>,
): QuoteContext | null {
  const p = prices[pos.ticker];
  const current = p ? resolveLastOrMid(p) : null;
  if (!p || current == null || p.close == null || p.close <= 0) return null;
  const shares = Math.abs(pos.contracts);
  return {
    col1: fmtPrice(p.close),
    col2: fmtCurrent(current, isMid(p)),
    closeValue: p.close * shares,
    quotedMove: positionDirectionSign(pos) * (current - p.close) * shares,
  };
}

/**
 * Net per-unit combo quote: Σ sign × leg price × (leg contracts / units),
 * +1 LONG / −1 SHORT, so a credit structure prints negative, matching the
 * table's Last Price convention.
 */
function optionQuoteContext(
  pos: PortfolioPosition,
  prices: Record<string, PriceData>,
): QuoteContext | null {
  const units = Math.abs(pos.contracts);
  if (pos.legs.length === 0 || units === 0) return null;
  let netClose = 0;
  let netCurrent = 0;
  let mid = false;
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    const lp = key ? prices[key] : null;
    const current = lp ? resolveLastOrMid(lp) : null;
    if (!lp || current == null || lp.close == null || lp.close <= 0) return null;
    const sign = leg.direction === "LONG" ? 1 : -1;
    const ratio = Math.abs(leg.contracts) / units;
    netClose += sign * lp.close * ratio;
    netCurrent += sign * current * ratio;
    if (isMid(lp)) mid = true;
  }
  return {
    col1: fmtPrice(netClose),
    col2: fmtCurrent(netCurrent, mid),
    closeValue: netClose * units * 100,
    quotedMove: (netCurrent - netClose) * units * 100,
  };
}

function quoteContextForPosition(
  pos: PortfolioPosition,
  prices: Record<string, PriceData>,
): QuoteContext | null {
  return pos.structure_type === "Stock" || pos.structure_type === "Crypto"
    ? stockQuoteContext(pos, prices)
    : optionQuoteContext(pos, prices);
}

function pctOfCloseValue(pnl: number, closeValue: number): number | null {
  return closeValue !== 0 ? (pnl / Math.abs(closeValue)) * 100 : null;
}

/**
 * IB's reqPnLSingle daily and the relay quotes carry independent prior
 * closes. When they disagree on the direction of the day the row must say
 * so, instead of printing a P&L that contradicts the prices beside it.
 */
function ibQuoteDisagreement(ibDailyPnl: number, quotedMove: number): PnlBreakdownFlag | undefined {
  if (ibDailyPnl === 0 || quotedMove === 0) return undefined;
  if (Math.sign(ibDailyPnl) === Math.sign(quotedMove)) return undefined;
  return {
    label: "IB≠QUOTE",
    title: `IB daily ${fmtSigned(ibDailyPnl, 2)} disagrees with the quoted move ${fmtSigned(quotedMove, 2)}`,
  };
}

const NO_QUOTE_LABELS = { col1: "IB daily", col2: "reqPnLSingle" } as const;

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
 *   4. Overnight options — net per-unit last/mid vs prior close.
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
    if (pos.ib_daily_pnl != null && Number.isFinite(pos.ib_daily_pnl)) {
      total += pos.ib_daily_pnl;
      const quote = quoteContextForPosition(pos, prices);
      rows.push({
        id: pos.id,
        ticker: pos.ticker,
        structure: pos.structure,
        col1: quote?.col1 ?? NO_QUOTE_LABELS.col1,
        col2: quote?.col2 ?? NO_QUOTE_LABELS.col2,
        pnl: pos.ib_daily_pnl,
        pnlPct: quote ? pctOfCloseValue(pos.ib_daily_pnl, quote.closeValue) : null,
        pnlFlag: quote ? ibQuoteDisagreement(pos.ib_daily_pnl, quote.quotedMove) : undefined,
      });
      continue;
    }

    // 2. Same-day positions and mixed-age combos: share getTodayPnlDollars
    //    with the table column. Mixed-age (overnight + session add) is not
    //    same-day; the helper uses close on overnight legs and fill on the add.
    if (isMixedAgeCombo(pos) || isSameDay(pos)) {
      const todayPnl = getTodayPnlDollars(pos, prices);
      if (todayPnl == null) continue;
      total += todayPnl;
      const mixed = isMixedAgeCombo(pos);
      rows.push({
        id: pos.id,
        ticker: pos.ticker,
        structure: pos.structure,
        col1: mixed ? "legs" : "entry",
        col2: mixed ? "mixed-age" : "same-day",
        pnl: todayPnl,
        pnlPct: null,
      });
      continue;
    }

    // 3. Overnight stock and options: sign-aware last-or-mid vs prior close,
    //    netted per unit for combos. A missing leg quote drops the row.
    const quote = quoteContextForPosition(pos, prices);
    if (quote == null) continue;
    total += quote.quotedMove;
    rows.push({
      id: pos.id,
      ticker: pos.ticker,
      structure: pos.structure,
      col1: quote.col1,
      col2: quote.col2,
      pnl: quote.quotedMove,
      pnlPct: pctOfCloseValue(quote.quotedMove, quote.closeValue),
    });
  }

  return { rows, total };
}
