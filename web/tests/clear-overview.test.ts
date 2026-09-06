import { describe, expect, it } from "vitest";
import { buildClearHistory, deriveClearAccount, deriveClearExposure, selectClearHistory } from "../lib/clearOverview";
import { normalizePerformanceData } from "../lib/performanceData";
import type { PortfolioData, PortfolioPosition } from "../lib/types";
import type { PriceData } from "../lib/pricesProtocol";

const account: PortfolioData = {
  bankroll: 100_000, peak_value: 100_000, last_sync: "2026-09-04T15:00:00Z",
  positions: [], total_deployed_pct: 0, total_deployed_dollars: 0,
  remaining_capacity_pct: 100, position_count: 0, defined_risk_count: 0,
  undefined_risk_count: 0, avg_kelly_optimal: null,
  account_summary: { net_liquidation: 100_000, daily_pnl: 200, unrealized_pnl: 0,
    realized_pnl: 0, settled_cash: 20_000, maintenance_margin: 25_000,
    excess_liquidity: 60_000, buying_power: 150_000, dividends: 0 },
};

function performance(series: object[], extra = {}) {
  return normalizePerformanceData({ series, ...extra });
}

describe("Clear account history", () => {
  it("plots dollar NAV, never a normalized base-100 TWR index", () => {
    const data = performance([
      { date: "2026-08-01", nav: 100_000, twr_index: 100 },
      { date: "2026-09-01", nav: 152_000, twr_index: 102 },
    ], { schema_version: 2 });
    expect(data?.series[1].equity).toBe(102);
    const history = buildClearHistory(data);
    expect(history.points.map((point) => point.value)).toEqual([100_000, 152_000]);
    expect(history.availablePeriods).toEqual(["ALL"]);
  });

  it("keeps TWR-only, missing, and single-observation history unavailable", () => {
    expect(buildClearHistory(null).points).toEqual([]);
    expect(buildClearHistory(performance([{ date: "2026-09-01", twr_index: 104 }])).points).toEqual([]);
    expect(buildClearHistory(performance([{ date: "2026-09-01", equity: 100_000 }])).availablePeriods).toEqual([]);
  });

  it("accepts legacy dollar history, orders dates and excludes invalid observations", () => {
    const history = buildClearHistory(performance([
      { date: "2026-09-01", equity: 104_000 },
      { date: "not-a-date", equity: 4 },
      { date: "2026-08-01", equity: 100_000 },
      { date: "2026-09-01", equity: 105_000 },
    ]));
    expect(history.points).toEqual([
      { date: "2026-08-01", value: 100_000 },
      { date: "2026-09-01", value: 105_000 },
    ]);
  });

  it("selects supported periods relative to the last observation, not wall-clock time", () => {
    const history = buildClearHistory(performance([
      { date: "2026-07-01", equity: 90_000 },
      { date: "2026-08-01", equity: 100_000 },
      { date: "2026-08-28", equity: 103_000 },
      { date: "2026-09-01", equity: 104_000 },
      { date: "2026-09-04", equity: 105_000 },
    ]));
    expect(selectClearHistory(history, "1W").map((point) => point.date)).toEqual(["2026-08-28", "2026-09-01", "2026-09-04"]);
    expect(selectClearHistory(history, "1Y")).toEqual(history.points);
    expect(history.availablePeriods).not.toContain("1D");
  });

  it("retains stale provenance instead of making dated history appear live", () => {
    const history = buildClearHistory(performance([
      { date: "2026-08-01", nav: 100_000 }, { date: "2026-09-01", nav: 104_000 },
    ], { schema_version: 2, status: "stale", nav_source: "ib_flex" }));
    expect(history.status).toBe("stale");
    expect(history.asOf).toBe("2026-09-01");
  });
});

describe("Clear account and risk presentation", () => {
  it("does not substitute bankroll or realized-only P&L for missing account values", () => {
    expect(deriveClearAccount(null, new Date("2026-09-04T16:00:00Z")).value).toBeNull();
    expect(deriveClearAccount({ ...account, account_summary: undefined }).value).toBeNull();
    const result = deriveClearAccount(account, new Date("2026-09-04T16:00:00Z"));
    expect(result.value).toBe(100_000);
    expect(result.dailyPnl).toBe(200);
    expect(result.marginUsedPct).toBe(25);
  });

  it("suppresses stale-session and non-finite account P&L", () => {
    expect(deriveClearAccount(account, new Date("2026-09-08T16:00:00Z")).dailyPnl).toBeNull();
    expect(deriveClearAccount(account, new Date("2026-09-05T16:00:00Z")).dailyPnl).toBeNull();
    const invalid = { ...account, account_summary: { ...account.account_summary!, daily_pnl: Infinity, net_liquidation: NaN } };
    expect(deriveClearAccount(invalid, new Date("2026-09-04T16:00:00Z")).value).toBeNull();
    expect(deriveClearAccount(invalid, new Date("2026-09-04T16:00:00Z")).dailyPnl).toBeNull();
  });

  it("does not label missing margin inputs healthy", () => {
    expect(deriveClearAccount(null).margin.degraded).toBe(true);
    expect(deriveClearAccount(account).margin.degraded).toBe(false);
  });

  it("withholds delta when an underlying or provider option delta is missing", () => {
    const position = { id: 1, ticker: "XYZ", expiry: "2026-12-18", structure: "Long Put", structure_type: "Long Put", contracts: 2, market_value: 1_000,
      legs: [{ type: "Put", direction: "LONG", strike: 100, contracts: 2, market_value: 1_000 }] } as PortfolioPosition;
    const portfolio = { ...account, positions: [position] };
    expect(deriveClearExposure(portfolio, {}).dollarDelta).toBeNull();
    expect(deriveClearExposure(portfolio, { XYZ: { last: 100 } } as Record<string, PriceData>).dollarDelta).toBeNull();
    const prices = { XYZ: { last: 100 }, XYZ_20261218_100_P: { delta: 0.7 } } as Record<string, PriceData>;
    expect(deriveClearExposure(portfolio, prices).dollarDelta).toBeCloseTo(-6_000);
  });

  it("distinguishes an empty verified portfolio from an unresolved portfolio", () => {
    expect(deriveClearExposure(null, {}).dollarDelta).toBeNull();
    expect(deriveClearExposure(account, {}).dollarDelta).toBe(0);
  });

  it("uses exact stock exposure without requiring option Greeks", () => {
    const position = { id: 1, ticker: "XYZ", structure: "Stock", structure_type: "Stock", contracts: 100, legs: [{ type: "Stock", direction: "LONG", contracts: 100, strike: null }] } as PortfolioPosition;
    expect(deriveClearExposure({ ...account, positions: [position] }, { XYZ: { last: 100 } } as Record<string, PriceData>).dollarDelta).toBe(10_000);
  });
});
