import { expect, test } from "@playwright/test";

const PORTFOLIO_MOCK = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-03-22T09:00:00Z",
  positions: [],
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  account_summary: {
    net_liquidation: 100_000,
    daily_pnl: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 100_000,
    dividends: 0,
  },
};

const ORDERS_EMPTY = {
  last_sync: "2026-03-22T09:00:00Z",
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

async function setupMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_MOCK) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, days_until_expiry: 14 }) }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: false }) }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: "2026-03-22T09:00:00Z", summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
}

test.describe("Sidebar navigation", () => {
  test("does not show the hidden Performance route in the visible nav", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");

    await expect(page.getByRole("link", { name: "Portfolio" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Orders" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Performance" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Discover" })).toHaveCount(0);
  });
});
