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

/**
 * The ONE inclusion rule for open P&L (R-656): a position is in the account
 * total exactly when `getPnlDollars` can measure it — the same call every row
 * surface makes — so rows always sum to the header. A blended-basis combo has
 * measurable per-leg P&L (it renders in PositionTable rows) even though its
 * aggregate ENTRY basis stays unpublishable.
 */
function measuredOpenPnl(pos: PortfolioData["positions"][number]): number | null {
  const mv = resolveMarketValue(pos);
  if (mv == null) return null;
  return getPnlDollars(pos, mv);
}
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
    const pnl = getPnlDollars(pos, mv);
    if (pnl == null) return [];
    // A blended-basis combo has no publishable aggregate entry (T-315) but its
    // per-leg P&L is measured, so it keeps a row: entry renders unavailable.
    const entry = resolveEntryCost(pos);
    const pnlPct = getPnlPct(pos, mv);
    return [{
      id: pos.id,
      ticker: pos.ticker,
      structure: pos.structure,
      col1: entry == null ? "---" : fmtSigned(entry, 2),
      col2: fmtSigned(mv, 2),
      pnl,
      pnlPct,
    }];
  });
}

/** Sum of per-position open P&L — same inclusion rule as the breakdown rows. */
export function sumUnrealizedBreakdown(portfolio: PortfolioData): number {
  let total = 0;
  for (const pos of portfolio.positions) {
    const pnl = measuredOpenPnl(pos);
    if (pnl != null) total += pnl;
  }
  return total;
}

/**
 * Positions whose legs disagree about their basis (T-315): their measured leg
 * P&L is in the total, but their aggregate entry basis is unpublishable, so
 * the UNMEASURED BASIS caveat still names them.
 */
export function countUnmeasuredBasis(portfolio: PortfolioData): number {
  return portfolio.positions.filter(hasBlendedLegBasis).length;
}
