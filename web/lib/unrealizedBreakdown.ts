/**
 * Unrealized P&L breakdown — pure functions for the Account "Unrealized P&L"
 * modal so entry / market / P&L stay sign-consistent and unit-testable.
 */

import type { PortfolioData } from "@/lib/types";
import {
  getPnlDollars,
  getPnlPct,
  resolveEntryCost,
  resolveMarketValue,
} from "@/lib/positionUtils";
import { fmtSigned } from "@/lib/format/money";
import type { PnlBreakdownRow } from "@/components/PnlBreakdownModal";

/**
 * Per-position open P&L: signed market_value − signed entry_cost.
 *
 * ENTRY COST and MKT VALUE columns keep their signs (credits negative, short
 * marks negative) so a reader can verify each row as col2 − col1 without
 * reconstructing hidden signs. Prior UI used Math.abs on both columns, which
 * made META/AAOI-style credit and short marks look arithmetically impossible.
 */
export function computeUnrealizedBreakdown(
  portfolio: PortfolioData,
): PnlBreakdownRow[] {
  return portfolio.positions.flatMap((pos) => {
    const mv = resolveMarketValue(pos);
    if (mv == null) return [];
    const entry = resolveEntryCost(pos);
    const pnl = getPnlDollars(pos, mv);
    if (pnl == null) return [];
    const pnlPct = getPnlPct(pos, mv);
    return [{
      id: pos.id,
      ticker: pos.ticker,
      structure: pos.structure,
      col1: fmtSigned(entry, 2),
      col2: fmtSigned(mv, 2),
      pnl,
      pnlPct,
    }];
  });
}

/** Sum of per-position open P&L (same basis as the breakdown rows). */
export function sumUnrealizedBreakdown(portfolio: PortfolioData): number {
  let total = 0;
  for (const pos of portfolio.positions) {
    const mv = resolveMarketValue(pos);
    if (mv == null) continue;
    total += mv - resolveEntryCost(pos);
  }
  return total;
}
