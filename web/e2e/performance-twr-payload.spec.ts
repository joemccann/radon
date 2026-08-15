import { expect, test } from "@playwright/test";

const TWR_SNAPSHOT = {
  status: "ok",
  methodology: {
    curve_type: "twr_modified_dietz_daily",
    return_basis: "time_weighted",
    risk_free_rate: 0.0412,
    library_strategy: "fred_dgs3mo",
  },
  summary: {
    total_return: 0.1,
    annualized_return: 0.5,
    trading_days: 57,
    period_start: "2026-01-02",
    period_end: "2026-03-20",
    max_drawdown: -0.05,
    sharpe_ratio: 1.2,
    beta: 0.8,
    alpha: 0.02,
    var_95: null,
    cvar_95: null,
  },
  series: [
    { date: "2026-01-02", nav: 100_000, return: 0, cum_return: 0, equity: 100 },
    { date: "2026-01-05", nav: 105_000, return: 0.05, cum_return: 0.05, equity: 105 },
    { date: "2026-03-20", nav: 110_000, return: 0.047619, cum_return: 0.1, equity: 110 },
  ],
  warnings: [],
  period_start: "2026-01-02",
  period_end: "2026-03-20",
  benchmark: "SPY",
  nav_source: "disk_cache",
};

const PORTFOLIO_EMPTY = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-03-20T18:55:00Z",
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

test.describe("/performance TWR snapshot", () => {
  test("renders TWR metrics instead of the route error boundary", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await page.route("**/api/performance", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(TWR_SNAPSHOT) }),
    );
    await page.route("**/api/portfolio", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_EMPTY) }),
    );
    await page.route("**/api/orders", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ last_sync: "2026-03-20T18:55:00Z", open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 }),
      }),
    );

    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto("/performance");
    await expect(page.locator('[data-testid="performance-panel"]')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('[data-testid="performance-hero-twr"]')).toHaveText("+10.00%");
    await expect(page.locator('[data-testid="performance-hero-subtitle"]')).toContainText("Ending equity $110,000.00");
    await expect(page.getByText("RUNTIME ERROR")).toHaveCount(0);
    expect(pageErrors.filter((message) => message.includes("length") || message.includes("toUpperCase"))).toEqual([]);
    await page.screenshot({ path: "test-results/performance-twr-payload.png", fullPage: true });
  });
});
