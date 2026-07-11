/**
 * Portfolio snapshot age vs the portfolio-sync writer schedule.
 *
 * GET /api/portfolio is snapshot-only (no IB). The writer
 * (radon-portfolio-sync.timer) only runs Mon–Fri RTH. A flat 60s "stale"
 * threshold painted LIVE DATA DEGRADED all weekend after Friday's last sync.
 * Reuse the same market-state windows as service-health for portfolio-sync.
 */

import {
  getMarketStateFromDate,
  isStale,
  type MarketState,
} from "@/lib/serviceHealthWindows";

export const PORTFOLIO_WRITER_SERVICE = "portfolio-sync";

/**
 * True when the snapshot is older than the portfolio-sync freshness window
 * for the current market state (open / extended / closed).
 */
export function isPortfolioSnapshotUnexpectedlyStale(
  timestampMs: number | null | undefined,
  nowMs: number = Date.now(),
): boolean {
  if (timestampMs == null || !Number.isFinite(timestampMs)) {
    return true;
  }
  const updatedAt = new Date(timestampMs).toISOString();
  const market: MarketState = getMarketStateFromDate(new Date(nowMs));
  return isStale(PORTFOLIO_WRITER_SERVICE, updatedAt, market, nowMs);
}

/** Operator-facing copy when the writer should have refreshed but did not. */
export const PORTFOLIO_SNAPSHOT_STALE_WARNING =
  "Portfolio snapshot is older than the scheduled refresh window; live sync was not requested";
