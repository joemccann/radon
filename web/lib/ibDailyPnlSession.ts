import { isUsTradingDay } from "./serviceHealthWindows";
import type { PortfolioData, PortfolioPosition } from "./types";

/**
 * IB's account-level `reqPnL().dailyPnL` only describes a real session on a
 * US trading day. On weekends and full-closure holidays IB keeps streaming
 * the field and re-baselines it at its daily account rollover, so a Saturday
 * sync can report a five-figure "day" P&L with a flat NLV and no trades
 * (2026-08-22: +$13,951.76 after Friday closed at -$5,339.04). Gate every
 * consumer of `account_summary.daily_pnl` on this.
 */
export function isIbDailyPnlCurrent(now: Date = new Date()): boolean {
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const pad = (n: number) => String(n).padStart(2, "0");
  return isUsTradingDay(`${et.getFullYear()}-${pad(et.getMonth() + 1)}-${pad(et.getDate())}`);
}

/** `account_summary.daily_pnl` when it describes today's session, else null. */
export function currentIbDailyPnl(
  dailyPnl: number | null | undefined,
  now: Date = new Date(),
): number | null {
  if (dailyPnl == null) return null;
  return isIbDailyPnlCurrent(now) ? dailyPnl : null;
}

/** Spot crypto (IB secType CRYPTO, mapped by ib_sync) trades 24/7. */
export function isCryptoPosition(pos: Pick<PortfolioPosition, "structure_type">): boolean {
  return pos.structure_type === "Crypto";
}

/**
 * Positions that have a live session right now: every position on a US
 * trading day, only spot crypto on weekends and full-closure holidays. The
 * day-move math must run over this subset so a weekend never reports an
 * equity "day move" off stale Friday marks.
 */
export function sessionPositions(
  portfolio: PortfolioData,
  now: Date = new Date(),
): PortfolioPosition[] {
  if (isIbDailyPnlCurrent(now)) return portfolio.positions;
  return portfolio.positions.filter(isCryptoPosition);
}

/**
 * `ib_daily_pnl` (IB reqPnLSingle) is re-baselined on weekends and holidays
 * just like the account aggregate, so outside a live session every equity row
 * would show a five-figure "day" P&L under a MARKET CLOSED card. Mask it on
 * those days; spot crypto keeps its own because it trades through them.
 */
export function withSessionIbDailyPnl(
  positions: PortfolioPosition[],
  now: Date = new Date(),
): PortfolioPosition[] {
  if (isIbDailyPnlCurrent(now)) return positions;
  return positions.map((pos) =>
    isCryptoPosition(pos) || pos.ib_daily_pnl == null ? pos : { ...pos, ib_daily_pnl: null },
  );
}
