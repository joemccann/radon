/**
 * Unrealized P&L breakdown — pure functions for the Account "Unrealized P&L"
 * modal so entry / market / P&L stay sign-consistent and unit-testable.
 */

import type { PortfolioData } from "@/lib/types";
import {
  getPnlDollars,
  getPnlPct,
  hasBlendedLegBasis,
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
    if (entry == null || pnl == null) return [];
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
    // The account-level total remains reconcilable with the breakdown table,
    // which cannot publish a mixed aggregate entry column.
    if (hasBlendedLegBasis(pos)) continue;
    const pnl = getPnlDollars(pos, resolveMarketValue(pos));
    if (pnl != null) total += pnl;
  }
  return total;
}

/**
 * Positions the open-P&L total leaves out because their legs disagree about
 * their basis (T-315). Non-zero means the total is a partial read.
 */
export function countUnmeasuredBasis(portfolio: PortfolioData): number {
  return portfolio.positions.filter(hasBlendedLegBasis).length;
}
