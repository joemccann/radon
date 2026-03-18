import { expect, test } from "@playwright/test";

const PORTFOLIO = {
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
  exposure: {},
  violations: [],
  positions: [],
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

const ORDERS = {
  last_sync: new Date().toISOString(),
  open_orders: [
    {
      orderId: 95,
      permId: 653624857,
      symbol: "AAOI P90",
      contract: {
        conId: 987654,
        symbol: "AAOI",
        secType: "OPT",
        strike: 90,
        right: "P",
        expiry: "2026-03-27",
      },
      action: "SELL",
      orderType: "LMT",
      totalQuantity: 50,
      limitPrice: 5.7,
      auxPrice: null,
      status: "Submitted",
      filled: 0,
      remaining: 50,
      avgFillPrice: null,
      tif: "DAY",
    },
  ],
  executed_orders: [],
  open_count: 1,
  executed_count: 0,
};

async function stubApis(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO),
    }),
  );

  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS),
    }),
  );

  await page.route("**/api/orders/modify", (route) =>
    route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ error: "Modify not confirmed by refreshed orders" }),
    }),
  );

  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        as_of: new Date().toISOString(),
        summary: { closed_trades: 0, open_trades: 0, total_commissions: 0, realized_pnl: 0 },
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

  await page.route("**/api/regime", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ score: 15, cri: { score: 15 } }),
    }),
  );

  await page.route("**/api/prices", (route) => route.abort());
}

test.describe("Order modify confirmation", () => {
  test("does not enter a fake pending state when modify is not confirmed", async ({ page }) => {
    await stubApis(page);

    await page.goto("http://127.0.0.1:3000/orders");

    const row = page.locator("tbody tr").filter({ hasText: "AAOI" }).first();
    await expect(row).toBeVisible({ timeout: 10_000 });
    await expect(row).toContainText("$5.70");

    await row.getByRole("button", { name: "MODIFY" }).click();

    const modal = page.locator(".modify-dialog");
    await expect(modal).toBeVisible();
    await modal.locator("#modify-price-input").fill("5.55");
    await modal.getByRole("button", { name: /modify order/i }).click();

    await expect(row).toContainText("$5.70");
    await expect(row).not.toContainText("Modifying...");
    await expect(row).not.toContainText("PENDING");
  });
});
