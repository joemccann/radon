import { expect, test } from "@playwright/test";
import { TRADING_DAY_ISO, freezeToTradingDay } from "./tradingDayClock";

/**
 * Figures from the 2026-11-06 screenshot: five rows summing to -$72,541.21 under
 * a TOTAL of IB's -$106,060.09. The total must be the rows' sum and IB's figure
 * must be named alongside it.
 */

const position = (id: number, ticker: string, entry_cost: number, market_value: number) => ({
  id,
  ticker,
  structure: `${ticker} position`,
  structure_type: "Stock",
  risk_profile: "equity",
  expiry: "N/A",
  contracts: 100,
  direction: "LONG",
  entry_cost,
  max_risk: null,
  market_value,
  ib_daily_pnl: 0,
  legs: [
    {
      direction: "LONG",
      contracts: 100,
      type: "Stock",
      strike: 0,
      entry_cost,
      avg_cost: entry_cost / 100,
      market_price: market_value / 100,
      market_value,
      market_price_is_calculated: false,
    },
  ],
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-10-01",
});

const PORTFOLIO_MOCK = {
  bankroll: 573_523.66,
  peak_value: 573_523.66,
  last_sync: TRADING_DAY_ISO,
  total_deployed_pct: 10,
  total_deployed_dollars: 57_000,
  remaining_capacity_pct: 90,
  position_count: 5,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [
    position(1, "WULF", 40_076.51, 3_465),
    position(2, "CBRS", 25_401.83, 150),
    position(3, "SPCX", 37_560.85, 53_925),
    position(4, "TQQQ", 541_087.12, 525_800),
    position(5, "IWO", 70_254.9, 58_500),
  ],
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 573_523.66,
    daily_pnl: 4_643,
    unrealized_pnl: -106_060.09,
    realized_pnl: 0,
    settled_cash: 0,
    maintenance_margin: 0,
    excess_liquidity: 0,
    buying_power: 0,
    dividends: 0,
  },
};

test("unrealized modal total is the rows' sum and names IB's account figure", async ({ page }) => {
  await freezeToTradingDay(page);
  await page.unrouteAll({ behavior: "ignoreErrors" });
  const json = (body: unknown) => (route: import("@playwright/test").Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/portfolio", json(PORTFOLIO_MOCK));
  await page.route(
    "**/api/orders",
    json({ last_sync: TRADING_DAY_ISO, open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 }),
  );
  await page.route("**/api/regime", json({ score: 15, level: "LOW", cri: { score: 15 } }));
  await page.route("**/api/flex-token", json({ ok: true, days_until_expiry: 14 }));
  await page.route("**/api/ib-status", json({ connected: true }));
  await page.route(
    "**/api/blotter",
    json({ as_of: TRADING_DAY_ISO, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
  );
  await page.route("**/api/prices**", (route) => route.abort());

  await page.goto("/portfolio");

  const card = page.locator(".metric-card").filter({
    has: page.locator(".metric-label", { hasText: "Unrealized P&L" }),
  });
  await card.first().click();

  const modal = page.locator(".pnl-breakdown-modal");
  await expect(modal).toBeVisible();
  await expect(modal.locator(".eb-total-value")).toHaveText("-$72,541.21");
  await expect(modal.locator(".eb-total-detail")).toHaveText(
    "IB account: -$106,060.09 · difference -$33,518.88 (IB marks and cost basis)",
  );
  await expect(modal.locator("tr").filter({ hasText: "TOTAL" })).toContainText("-$72,541.21");

  await modal.screenshot({ path: "test-results/unrealized-modal-reconciles-rows.png" });
});
