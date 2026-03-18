import { expect, test } from "@playwright/test";

const PORTFOLIO_MOCK = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 2,
  defined_risk_count: 2,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [
    {
      id: 1,
      ticker: "AAOI",
      structure: "Long Call",
      structure_type: "Long Call",
      risk_profile: "Defined",
      expiry: "2026-03-20",
      contracts: 50,
      direction: "LONG",
      entry_cost: 0,
      max_risk: null,
      market_value: 0,
      legs: [
        { direction: "LONG", contracts: 50, type: "Call", strike: 105, entry_cost: 0, avg_cost: 0, market_price: 0, market_value: 0 },
      ],
      ib_daily_pnl: null,
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-17",
    },
    {
      id: 2,
      ticker: "AAOI",
      structure: "Risk Reversal",
      structure_type: "Risk Reversal",
      risk_profile: "Undefined",
      expiry: "2026-04-17",
      contracts: 25,
      direction: "COMBO",
      entry_cost: 0,
      max_risk: null,
      market_value: 0,
      legs: [
        { direction: "SHORT", contracts: 25, type: "Put", strike: 85, entry_cost: 0, avg_cost: 0, market_price: 0, market_value: 0 },
        { direction: "LONG", contracts: 25, type: "Call", strike: 115, entry_cost: 0, avg_cost: 0, market_price: 0, market_value: 0 },
      ],
      ib_daily_pnl: null,
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-17",
    },
  ],
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 100_000,
    daily_pnl: null,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 200_000,
    dividends: 0,
  },
};

const ORDERS_MOCK = {
  last_sync: new Date().toISOString(),
  open_orders: [
    {
      orderId: 72,
      permId: 653611397,
      symbol: "AAOI C105",
      contract: {
        conId: 859556363,
        symbol: "AAOI",
        secType: "OPT",
        strike: 105,
        right: "C",
        expiry: "2026-03-20",
      },
      action: "SELL",
      orderType: "LMT",
      totalQuantity: 50,
      limitPrice: 5,
      auxPrice: 0,
      status: "Submitted",
      filled: 0,
      remaining: 50,
      avgFillPrice: 0,
      tif: "DAY",
    },
    {
      orderId: 73,
      permId: 653611398,
      symbol: "AAOI P85",
      contract: {
        conId: 859556111,
        symbol: "AAOI",
        secType: "OPT",
        strike: 85,
        right: "P",
        expiry: "2026-04-17",
      },
      action: "SELL",
      orderType: "LMT",
      totalQuantity: 25,
      limitPrice: 1.25,
      auxPrice: 0,
      status: "Submitted",
      filled: 0,
      remaining: 25,
      avgFillPrice: 0,
      tif: "DAY",
    },
    {
      orderId: 74,
      permId: 653611399,
      symbol: "AAOI C115",
      contract: {
        conId: 859558041,
        symbol: "AAOI",
        secType: "OPT",
        strike: 115,
        right: "C",
        expiry: "2026-04-17",
      },
      action: "BUY",
      orderType: "LMT",
      totalQuantity: 25,
      limitPrice: 2.3,
      auxPrice: 0,
      status: "Submitted",
      filled: 0,
      remaining: 25,
      avgFillPrice: 0,
      tif: "DAY",
    },
  ],
  executed_orders: [],
  open_count: 3,
  executed_count: 0,
};

async function stubApis(page: import("@playwright/test").Page) {
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
      body: JSON.stringify(ORDERS_MOCK),
    }),
  );

  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        as_of: new Date().toISOString(),
        summary: {
          closed_trades: 0,
          open_trades: 0,
          total_commissions: 0,
          realized_pnl: 0,
        },
        closed_trades: [],
        open_trades: [],
      }),
    }),
  );

  await page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: true }),
    }),
  );

  await page.route("**/api/prices", (route) => route.abort());
}

test.describe("Orders open-order single detail rendering", () => {
  test("renders single option order detail alongside combo detail", async ({ page }) => {
    await stubApis(page);
    await page.goto("http://127.0.0.1:3000/orders");

    const singleOptionRow = page.locator("tbody tr").filter({ hasText: "AAOI" }).filter({ hasText: "$5.00" }).first();
    await expect(singleOptionRow).toBeVisible({ timeout: 10_000 });
    await expect(singleOptionRow).toContainText("Long $105 Call 2026-03-20");

    const comboRow = page.locator("tbody tr").filter({ hasText: "Risk Reversal" }).first();
    await expect(comboRow).toContainText("Short Put 85");
    await expect(comboRow).toContainText("Long Call 115");
  });
});
