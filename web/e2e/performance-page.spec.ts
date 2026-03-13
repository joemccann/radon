import { expect, test } from "@playwright/test";

const PERFORMANCE_MOCK = {
  as_of: "2026-03-10",
  last_sync: "2026-03-10T18:55:00Z",
  period_start: "2026-01-01",
  period_end: "2026-03-10",
  period_label: "YTD",
  benchmark: "SPY",
  benchmark_total_return: -0.0225,
  trades_source: "ib_flex",
  price_sources: {
    stocks: "ib_with_uw_yahoo_fallback",
    options: "unusual_whales_option_contract_historic",
  },
  methodology: {
    curve_type: "reconstructed_net_liquidation",
    return_basis: "daily_close_to_close",
    risk_free_rate: 0,
    library_strategy: "in_repo_formulas_aligned_to_empyrical_quantstats_conventions",
  },
  summary: {
    starting_equity: 1_050_000,
    ending_equity: 1_094_500,
    pnl: 44_500,
    trading_days: 46,
    total_return: 0.04238,
    annualized_return: 0.264,
    annualized_volatility: 0.118,
    downside_deviation: 0.081,
    sharpe_ratio: 1.84,
    sortino_ratio: 2.41,
    calmar_ratio: 2.87,
    max_drawdown: -0.0918,
    current_drawdown: -0.0121,
    max_drawdown_duration_days: 14,
    beta: 0.62,
    alpha: 0.038,
    correlation: 0.77,
    r_squared: 0.59,
    tracking_error: 0.094,
    information_ratio: 0.88,
    treynor_ratio: 0.43,
    upside_capture: 0.91,
    downside_capture: 0.68,
    var_95: -0.012,
    cvar_95: -0.017,
    tail_ratio: 1.12,
    ulcer_index: 0.029,
    skew: -0.2,
    kurtosis: 0.41,
    hit_rate: 0.57,
    positive_days: 26,
    negative_days: 19,
    flat_days: 1,
    best_day: 0.019,
    worst_day: -0.016,
    average_up_day: 0.005,
    average_down_day: -0.004,
    win_loss_ratio: 1.25,
  },
  warnings: [
    "Reconstructed YTD equity curve anchored to current net liquidation. External cash flows are assumed to be zero unless already embedded in the starting cash balance.",
  ],
  contracts_missing_history: [],
  series: Array.from({ length: 10 }, (_, index) => ({
    date: `2026-01-${String(index + 2).padStart(2, "0")}`,
    equity: 1_050_000 + index * 4_500,
    daily_return: index === 0 ? null : 0.0025,
    drawdown: index < 6 ? 0 : -0.01,
    benchmark_close: 670 + index * 2,
    benchmark_return: index === 0 ? 0 : 0.0014,
  })),
};

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
  await page.unrouteAll({ behavior: "ignoreErrors" });
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

test.describe("/performance page", () => {
  test("renders institutional metrics and chart", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/performance");

    await expect(page.locator('[data-testid="performance-panel"]')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-testid="performance-panel"] .section-label-mono').filter({ hasText: "RECONSTRUCTED YTD" })).toBeVisible();
    await expect(page.locator("text=Sharpe Ratio")).toBeVisible();
    await expect(page.locator("text=Sortino Ratio")).toBeVisible();
    await expect(page.locator("text=Max Drawdown")).toBeVisible();
    await expect(page.locator('[data-testid="performance-equity-chart"]')).toBeVisible();
    await expect(page.locator("text=Methodology")).toBeVisible();
    await expect(page.locator("text=Warnings")).toBeVisible();
  });

  test("performance metric cards are clickable and open explainability modals", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/performance");

    const cards = page.locator('[data-testid^="performance-card-"]');
    await expect(cards).toHaveCount(8, { timeout: 10_000 });
    await expect(cards.nth(0)).toHaveClass(/metric-card-clickable/);
    await expect(cards.nth(7)).toHaveClass(/metric-card-clickable/);

    await page.getByTestId("performance-card-ytd-return").click();
    const modal = page.locator(".modal-content");
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("YTD Return");
    await expect(modal).toContainText("Cumulative return");
    await expect(modal).toContainText("YTD Return = (Ending Equity / Starting Equity) - 1");
    await modal.getByRole("button", { name: "Close" }).click();
    await expect(modal).toBeHidden();

    await page.getByTestId("performance-card-beta").click();
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("Beta");
    await expect(modal).toContainText("Sensitivity");
    await expect(modal).toContainText("Covariance(Portfolio Returns, SPY Returns)");
  });

  test("refreshes stale cached ending equity to the current portfolio net liquidation snapshot", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });

    let performanceGetCalls = 0;
    await page.route("**/api/performance", async (route) => {
      performanceGetCalls += 1;

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...PERFORMANCE_MOCK,
          as_of: performanceGetCalls === 1 ? "2026-03-10" : "2026-03-11",
          last_sync: performanceGetCalls === 1 ? "2026-03-10T18:55:00Z" : "2026-03-11T13:37:14Z",
          summary: {
            ...PERFORMANCE_MOCK.summary,
            ending_equity: performanceGetCalls === 1 ? 1_063_031.86 : 1_313_112.03,
            pnl: performanceGetCalls === 1 ? 13_031.86 : 263_112.03,
            total_return: performanceGetCalls === 1 ? 0.01241 : 0.25058,
          },
        }),
      });
    });
    await page.route("**/api/portfolio", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...PORTFOLIO_EMPTY,
          last_sync: "2026-03-11T13:37:14Z",
          account_summary: {
            ...PORTFOLIO_EMPTY.account_summary,
            net_liquidation: 1_313_112.03,
          },
        }),
      }),
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

    await page.goto("/performance");

    await expect(page.locator('[data-testid="performance-panel"]')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("text=Ending equity $1,313,112.03")).toBeVisible({ timeout: 10_000 });
    expect(performanceGetCalls).toBeGreaterThan(1);
  });

  test("refreshes the hero as_of date after route revalidation returns the current session", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });

    let performanceGetCalls = 0;
    await page.route("**/api/performance", async (route) => {
      performanceGetCalls += 1;

      const isFresh = performanceGetCalls > 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...PERFORMANCE_MOCK,
          as_of: isFresh ? "2026-03-13" : "2026-03-12",
          last_sync: isFresh ? "2026-03-13T20:02:06Z" : "2026-03-12T13:23:21Z",
          summary: {
            ...PERFORMANCE_MOCK.summary,
            ending_equity: isFresh ? 1_250_902.19 : 1_218_410.03,
            pnl: isFresh ? 236_620.64 : 204_128.48,
            total_return: isFresh ? 0.23329 : 0.20125,
          },
        }),
      });
    });
    await page.route("**/api/portfolio", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...PORTFOLIO_EMPTY,
          last_sync: "2026-03-13T20:02:06Z",
          account_summary: {
            ...PORTFOLIO_EMPTY.account_summary,
            net_liquidation: 1_250_902.19,
          },
        }),
      }),
    );
    await page.route("**/api/orders", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
    );
    await page.route("**/api/blotter", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          as_of: "2026-03-13T20:02:06Z",
          summary: { closed_trades: 0, open_trades: 0, total_commissions: 0, realized_pnl: 0 },
          closed_trades: [],
          open_trades: [],
        }),
      }),
    );

    await page.goto("/performance");

    await expect(page.locator('[data-testid="performance-panel"]')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("text=AS OF 2026-03-13")).toBeVisible({ timeout: 10_000 });
    expect(performanceGetCalls).toBeGreaterThan(1);
  });

  test("revalidates reconstructed YTD after portfolio sync advances the shell snapshot", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });

    let performanceGetCalls = 0;
    let portfolioPostCalls = 0;

    await page.route("**/api/performance", async (route) => {
      performanceGetCalls += 1;

      const isFresh = portfolioPostCalls > 0 && performanceGetCalls > 2;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...PERFORMANCE_MOCK,
          as_of: isFresh ? "2026-03-13" : "2026-03-12",
          last_sync: isFresh ? "2026-03-13T21:01:00Z" : "2026-03-12T21:01:00Z",
          summary: {
            ...PERFORMANCE_MOCK.summary,
            ending_equity: isFresh ? 1_218_410.03 : 1_094_500,
            pnl: isFresh ? 204_128.48 : 44_500,
            total_return: isFresh ? 0.2013 : 0.04238,
          },
        }),
      });
    });

    await page.route("**/api/portfolio", async (route, request) => {
      const method = request.method();
      if (method === "POST") {
        portfolioPostCalls += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ...PORTFOLIO_EMPTY,
            last_sync: "2026-03-13T21:01:00Z",
            account_summary: {
              ...PORTFOLIO_EMPTY.account_summary,
              net_liquidation: 1_218_410.03,
            },
          }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...PORTFOLIO_EMPTY,
          last_sync: "2026-03-12T21:01:00Z",
          account_summary: {
            ...PORTFOLIO_EMPTY.account_summary,
            net_liquidation: 1_094_500,
          },
        }),
      });
    });
    await page.route("**/api/orders", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
    );
    await page.route("**/api/blotter", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          as_of: "2026-03-12T21:01:00Z",
          summary: { closed_trades: 0, open_trades: 0, total_commissions: 0, realized_pnl: 0 },
          closed_trades: [],
          open_trades: [],
        }),
      }),
    );

    await page.goto("/performance");

    await expect(page.locator('[data-testid="performance-panel"]')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("text=AS OF 2026-03-12")).toBeVisible();

    const callsBeforeSync = performanceGetCalls;
    await page.getByRole("button", { name: "Sync Now" }).click();

    await expect(page.locator("text=Ending equity $1,218,410.03")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("text=AS OF 2026-03-13")).toBeVisible({ timeout: 10_000 });
    expect(performanceGetCalls).toBeGreaterThan(callsBeforeSync);
  });

  test("sidebar exposes the performance route", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/performance");
    await expect(page.locator("a[href='/performance']")).toBeVisible({ timeout: 10_000 });
  });
});
