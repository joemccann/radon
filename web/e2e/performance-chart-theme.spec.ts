import { expect, test } from "@playwright/test";
import { performanceFixture, installPerformanceFixtures, resolvedColor } from "./performance-fixtures";

const PERFORMANCE_MOCK = performanceFixture();

const PORTFOLIO_EMPTY = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-03-10T18:55:00Z",
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
  last_sync: "2026-03-10T18:55:00Z",
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

async function setupMocks(page: import("@playwright/test").Page) {
  await installPerformanceFixtures(page);
  await page.route("**/api/performance", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PERFORMANCE_MOCK) }),
  );
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_EMPTY) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        as_of: "2026-03-10T18:55:00Z",
        summary: { closed_trades: 0, open_trades: 0, total_commissions: 0, realized_pnl: 0 },
        closed_trades: [],
        open_trades: [],
      }),
    }),
  );
}

test.describe("/performance page — chart theme", () => {
  test("equity chart and chart meta cards switch to light-mode surfaces", async ({ page }) => {
    await setupMocks(page);
    await page.addInitScript(() => {
      localStorage.setItem("theme", "dark");
    });

    await page.goto("/performance");

    const chart = page.getByTestId("performance-equity-chart");
    const chartMetaItem = page.locator(".performance-chart-meta .performance-meta-item").first();

    await expect(chart).toBeVisible({ timeout: 10_000 });
    await expect(chartMetaItem).toBeVisible();

    const darkChartBackground = await chart.evaluate((node) => getComputedStyle(node).backgroundColor);

    await page.getByLabel("Toggle theme").click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

    const lightChartBackground = await chart.evaluate((node) => getComputedStyle(node).backgroundColor);
    const lightMetaBackground = await chartMetaItem.evaluate((node) => getComputedStyle(node).backgroundColor);

    expect(lightChartBackground).not.toBe(darkChartBackground);
    expect(lightChartBackground).toBe(await resolvedColor(page, "--performance-chart-bg"));
    expect(lightMetaBackground).toBe(await resolvedColor(page, "--performance-chart-meta-bg"));
  });
});
