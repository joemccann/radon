import { currentIbDailyPnl } from "./ibDailyPnlSession";
import { assessMargin } from "./marginWarning";
import { computeExposureDetailed } from "./exposureBreakdown";
import { legPriceKey } from "./positionUtils";
import type { PriceData } from "./pricesProtocol";
import type { PerformanceData, PortfolioData } from "./types";

export const CLEAR_PERIODS = ["1W", "1M", "3M", "1Y", "ALL"] as const;
export type ClearPeriod = (typeof CLEAR_PERIODS)[number];
export type ClearHistoryPoint = { date: string; value: number };
export type ClearHistory = {
  points: ClearHistoryPoint[];
  availablePeriods: ClearPeriod[];
  asOf: string | null;
  status: PerformanceData["status"];
};

const PERIOD_DAYS: Record<Exclude<ClearPeriod, "ALL">, number> = { "1W": 7, "1M": 30, "3M": 90, "1Y": 365 };
const DAY_MS = 86_400_000;

function finite(value: number | undefined | null): number | null {
  return value != null && Number.isFinite(value) ? value : null;
}

/** Account value is dollar NAV, not the cash-flow-adjusted performance index. */
export function buildClearHistory(data: PerformanceData | null): ClearHistory {
  const byDate = new Map<string, ClearHistoryPoint>();
  for (const point of data?.series ?? []) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(point.date) || !Number.isFinite(Date.parse(`${point.date}T00:00:00Z`))) continue;
    const value = finite(point.nav) ?? (point.twr_index == null && data?.schema_version !== 2 ? finite(point.equity) : null);
    if (value != null) byDate.set(point.date, { date: point.date, value });
  }
  const points = [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date));
  const span = points.length > 1 ? (Date.parse(points.at(-1)!.date) - Date.parse(points[0].date)) / DAY_MS : 0;
  const end = points.length ? Date.parse(points.at(-1)!.date) : 0;
  const availablePeriods = points.length < 2 ? [] : CLEAR_PERIODS.filter((period) => period === "ALL" || (
    span >= PERIOD_DAYS[period] && points.filter((point) => Date.parse(point.date) >= end - PERIOD_DAYS[period] * DAY_MS).length >= 2
  ));
  return { points, availablePeriods, asOf: points.at(-1)?.date ?? null, status: data?.status };
}

export function selectClearHistory(history: ClearHistory, period: ClearPeriod): ClearHistoryPoint[] {
  if (period === "ALL" || !history.availablePeriods.includes(period) || !history.asOf) return history.points;
  const cutoff = Date.parse(history.asOf) - PERIOD_DAYS[period] * DAY_MS;
  return history.points.filter((point) => Date.parse(point.date) >= cutoff);
}

export function deriveClearAccount(portfolio: PortfolioData | null, now = new Date()) {
  const account = portfolio?.account_summary;
  const value = finite(account?.net_liquidation);
  const maintenance = finite(account?.maintenance_margin);
  return {
    value,
    // Realized fills alone are not total account daily P&L. Do not substitute
    // them for a missing broker observation, or use NLV as a return baseline.
    dailyPnl: finite(currentIbDailyPnl(account?.daily_pnl, now, portfolio?.last_sync)),
    buyingPower: finite(account?.buying_power),
    maintenance,
    marginUsedPct: maintenance != null && value != null && value > 0 ? maintenance / value * 100 : null,
    margin: assessMargin(account),
  };
}

/** Reuse canonical signed deltas, withholding incomplete/approximated totals. */
export function deriveClearExposure(portfolio: PortfolioData | null, prices: Record<string, PriceData>) {
  if (!portfolio) return { dollarDelta: null, complete: false };
  const complete = portfolio.positions.every((position) => {
    const spot = finite(prices[position.ticker]?.last);
    if (spot == null || spot <= 0 || position.legs.length === 0) return false;
    return position.legs.every((leg) => {
      if (leg.type === "Stock") return true;
      const key = legPriceKey(position.ticker, position.expiry, leg);
      return key != null && finite(prices[key]?.delta) != null;
    });
  });
  return { dollarDelta: complete ? computeExposureDetailed(portfolio, prices).dollarDelta : null, complete };
}
