import { expect, test } from "@playwright/test";

const PORTFOLIO_MOCK = {
  bankroll: 1_300_000,
  peak_value: 1_300_000,
  last_sync: "2026-04-22T14:30:00Z",
  total_deployed_pct: 4.2,
  total_deployed_dollars: 54_600,
  remaining_capacity_pct: 95.8,
  position_count: 1,
  defined_risk_count: 0,
  undefined_risk_count: 1,
  avg_kelly_optimal: 0.8,
  positions: [
    {
      id: 1,
      ticker: "TSLA",
      structure: "Ratio Risk Reversal 75x10 (P$400.0/C$410.0)",
      structure_type: "Ratio Risk Reversal",
      risk_profile: "undefined",
      expiry: "2026-06-19",
      contracts: 75,
      direction: "COMBO",
      entry_cost: 118200,
      max_risk: null,
      market_value: 51975,
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-04-15",
      legs: [
        {
          direction: "LONG",
          contracts: 75,
          type: "Call",
          strike: 410,
          entry_cost: 145875,
          avg_cost: 1945,
          market_price: 10.45,
          market_value: 78375,
        },
        {
          direction: "SHORT",
          contracts: 10,
          type: "Put",
          strike: 400,
          entry_cost: 27690,
          avg_cost: 2769,
          market_price: 26.41,
          market_value: -26410,
        },
      ],
    },
  ],
  exposure: {},
  violations: [],
  trade_log_dates: {},
  account_summary: {
    net_liquidation: 1_300_000,
    daily_pnl: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 0,
    maintenance_margin: 0,
    excess_liquidity: 0,
    buying_power: 0,
    dividends: 0,
  },
};

const ORDERS_EMPTY = {
  last_sync: "2026-04-22T14:30:00Z",
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

test("portfolio shows raw long-short counts for ratio risk reversal labels", async ({ page }) => {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_MOCK),
    }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_EMPTY),
    }),
  );
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, days_until_expiry: 14 }),
    }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: true }),
    }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: "2026-04-22T14:30:00Z", summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );

  await page.goto("http://127.0.0.1:3000/portfolio");

  const undefinedRiskSection = page.locator(".section").filter({ hasText: "Undefined Risk Positions" }).first();
  await expect(undefinedRiskSection).toContainText("Ratio Risk Reversal 75x10 (P$400.0/C$410.0)");
  await expect(undefinedRiskSection).not.toContainText("Ratio Risk Reversal 2x15");
  await expect(undefinedRiskSection).toContainText("LONG 75x Call $410");
  await expect(undefinedRiskSection).toContainText("SHORT 10x Put $400");
});
