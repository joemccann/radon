import { test, expect } from "@playwright/test";

const PORTFOLIO_EMPTY = {
  bankroll: 100_000,
  positions: [],
  account_summary: {},
  exposure: {},
  violations: [],
};

const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

async function setupNonRegimeMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_EMPTY),
    }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_EMPTY),
    }),
  );
  await page.route("**/api/prices", (route) => route.abort());
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: false }),
    }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  await page.route("**/api/menthorq/cta", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ tables: [] }),
    }),
  );
}

test.describe("/regime page — live cache RVOL history", () => {
  test("renders 20 RVOL history dots from the live CRI cache artifact", async ({ page }) => {
    await setupNonRegimeMocks(page);
    await page.goto("/regime");

    const charts = page.locator('[data-testid="cri-history-chart"]');
    await expect(charts).toHaveCount(2);

    const rvolChart = charts.nth(1);
    await expect(rvolChart.locator("svg")).toBeVisible();
    await expect(rvolChart.locator("svg .dot-realized_vol")).toHaveCount(20);
    await expect(page.locator('[data-testid="strip-rvol"] .regime-strip-value')).not.toHaveText("---");
  });
});
