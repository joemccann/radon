import { expect, test } from "@playwright/test";

const PORTFOLIO_MOCK = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [],
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

const ORDERS_RISK_REVERSAL = {
  last_sync: new Date().toISOString(),
  open_orders: [
    {
      orderId: 1001,
      permId: 9001,
      symbol: "AAPL",
      contract: {
        conId: 12001,
        symbol: "AAPL",
        secType: "OPT",
        strike: 150,
        right: "P",
        expiry: "2026-04-17",
      },
      action: "SELL",
      orderType: "LMT",
      totalQuantity: 10,
      limitPrice: 0.95,
      auxPrice: null,
      status: "Submitted",
      filled: 0,
      remaining: 10,
      avgFillPrice: null,
      tif: "DAY",
    },
    {
      orderId: 1002,
      permId: 9002,
      symbol: "AAPL",
      contract: {
        conId: 12002,
        symbol: "AAPL",
        secType: "OPT",
        strike: 165,
        right: "C",
        expiry: "2026-04-17",
      },
      action: "BUY",
      orderType: "LMT",
      totalQuantity: 10,
      limitPrice: 1.15,
      auxPrice: null,
      status: "Submitted",
      filled: 0,
      remaining: 10,
      avgFillPrice: null,
      tif: "DAY",
    },
  ],
  executed_orders: [],
  open_count: 2,
  executed_count: 0,
};

async function stubApis(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_MOCK),
    });
  });

  await page.route("**/api/orders", (route) => {
    const method = route.request().method();
    if (method === "POST") {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(ORDERS_RISK_REVERSAL),
      });
      return;
    }

    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_RISK_REVERSAL),
    });
  });

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

test.describe("Orders open-order combo rendering", () => {
  test("combines short put and long call as a risk reversal row", async ({ page }) => {
    await stubApis(page);
    await page.goto("/orders");

    const riskReversalRow = page
      .locator("tbody tr")
      .filter({ hasText: "AAPL" })
      .filter({ hasText: "Risk Reversal" });

    await expect(riskReversalRow).toBeVisible({ timeout: 10_000 });
    await expect(riskReversalRow).toContainText("COMBO");
    await expect(riskReversalRow).toContainText("Short Put 150");
    await expect(riskReversalRow).toContainText("Long Call 165");
    await expect(riskReversalRow.getByRole("button", { name: "CANCEL ALL" })).toBeVisible();
    await expect(riskReversalRow.getByRole("button", { name: "MODIFY" })).toBeDisabled();
  });
});
