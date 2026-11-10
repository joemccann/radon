import { test, expect } from "@playwright/test";

// 300 weekday sessions of a gently rising curve with periodic dips so the
// drawdown series has structure to draw.
function buildSeries() {
  const dates: string[] = [];
  const equity: number[] = [];
  const start = Date.UTC(2025, 0, 2);
  let level = 100_000;
  for (let i = 0; dates.length < 300; i++) {
    const day = new Date(start + i * 86_400_000);
    if (day.getUTCDay() === 0 || day.getUTCDay() === 6) continue;
    level *= 1 + (dates.length % 7 === 3 ? -0.012 : 0.004);
    dates.push(day.toISOString().slice(0, 10));
    equity.push(Math.round(level * 100) / 100);
  }
  return { dates, equity };
}

const SERIES = buildSeries();

const QUANT_MOCK = {
  strategy: "trend_inverse_vol",
  label: "Trend, inverse vol (futures proxy)",
  missing: false,
  run: {
    run_at: "2026-10-30T22:00:00Z",
    since: SERIES.dates[0],
    until: SERIES.dates[SERIES.dates.length - 1],
    n_sessions: SERIES.dates.length,
    n_rebalances: 14,
    cost_bps: 5,
    universe: ["SPY", "QQQ", "IEF", "USO", "GLD", "CORN"],
    metrics: {
      sharpe: 0.93,
      sortino: 1.2,
      calmar: 0.61,
      max_drawdown: -0.097,
      annualized_return: 0.059,
      ann_vol: 0.063,
    },
    final_weights: { SPY: 0.3, QQQ: 0.2, IEF: -0.5 },
    dates: SERIES.dates,
    equity: SERIES.equity,
  },
  targets: {
    as_of: "2026-10-30",
    recorded_at: "2026-10-30T21:25:00Z",
    rebalance_today: true,
    rows: [
      { symbol: "SPY", weight: 0.3, notional: 30_000, close: 500 },
      { symbol: "QQQ", weight: 0.2, notional: 20_000, close: 480 },
      { symbol: "IEF", weight: -0.5, notional: -50_000, close: 95 },
    ],
  },
  orders: {
    as_of: "2026-10-30",
    placed_at: "2026-10-31T13:35:00Z",
    rows: [{ symbol: "SPY", action: "BUY", quantity: 60, limit_price: 501, status: "planned", message: "dry run" }],
  },
};

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

async function setupMocks(page: import("@playwright/test").Page, quantPayload: Record<string, unknown> = QUANT_MOCK) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/quant/*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(quantPayload) }),
  );
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_EMPTY) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
}

test.describe("/regime/quant — quant sleeve tab", () => {
  test("activates the QUANT tab and renders the backtest strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/quant");

    await expect(page.locator('.regime-rail__item[data-tab="quant"]')).toHaveClass(/active/);

    const sharpe = page.locator('[data-testid="quant-strip-sharpe"]');
    await sharpe.waitFor({ timeout: 10_000 });
    await expect(sharpe).toContainText("0.93");
    await expect(page.locator('[data-testid="quant-strip-return"]')).toContainText("+5.9%");
    await expect(page.locator('[data-testid="quant-strip-maxdd"]')).toContainText("-9.7%");
    await expect(page.locator('[data-testid="quant-rebalance-flag"]')).toHaveText("REBALANCE TODAY");
  });

  test("renders the equity chart, the targets table and the execution plan", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/quant");

    const chart = page.locator('[data-testid="quant-chart-section"]');
    await chart.waitFor({ timeout: 10_000 });
    await expect(chart.locator("svg path[stroke]").first()).toBeVisible();
    expect(await chart.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(2);
    await expect(chart.locator('[data-testid="quant-brush"]')).toBeVisible();

    const targets = page.locator('[data-testid="quant-targets-table"]');
    await expect(targets).toContainText("SPY");
    await expect(targets).toContainText("QQQ");
    await expect(targets).toContainText("IEF");
    await expect(page.locator('[data-testid="quant-target-IEF"]')).toContainText("-0.500");
    await expect(page.locator('[data-testid="quant-orders-table"]')).toContainText("PLANNED");

    await page.screenshot({ path: process.env.QUANT_SHOT_PATH ?? "test-results/quant-tab.png", fullPage: true });
  });

  test("switches strategies through the chips", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/quant");

    const chips = page.locator('[data-testid="quant-strategy-chips"]');
    await chips.waitFor({ timeout: 10_000 });
    const target = chips.locator("button", { hasText: "Monthly inverse volatility" });
    await target.click();
    await expect(target).toHaveClass(/is-active/);
    await expect(page.locator('[data-testid="quant-strip-sharpe"]')).toContainText("0.93");
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, { strategy: "trend_inverse_vol", missing: true, run: null, targets: null, orders: null });
    await page.goto("/regime/quant");

    const empty = page.locator('[data-testid="section-empty-state"]');
    await empty.waitFor({ timeout: 10_000 });
    await expect(empty).toContainText("No quant sleeve runs yet");
    await expect(page.locator('[data-testid="quant-strategy-chips"]')).toBeVisible();
  });
});
