import { test, expect } from "@playwright/test";

// 60 sessions; closes repeat +1, +1, -0.5 so streaks cycle 0,1,2,0 and the
// final session (index 59, 59 % 4 === 3) is overridden below to extend the
// last run to a live 3-day streak for the strip assertions.
function buildSeries() {
  const series: Array<{ date: string; close: number; streak: number }> = [];
  const start = Date.UTC(2026, 4, 1);
  let close = 100;
  let streak = 0;
  for (let i = 0; i < 60; i += 1) {
    if (i > 0) {
      const up = i % 4 === 1 || i % 4 === 2 || i === 59;
      close = up ? close + 1 : close - 0.5;
      streak = up ? streak + 1 : 0;
    }
    const date = new Date(start + i * 86_400_000).toISOString().slice(0, 10);
    series.push({ date, close: Number(close.toFixed(2)), streak });
  }
  return series;
}

function buildPayload(symbol: string) {
  const series = buildSeries();
  const last = series[series.length - 1];
  return {
    symbol,
    scan_time: new Date().toISOString(),
    source: "yahoo",
    missing: false,
    count: series.length,
    first_date: series[0].date,
    last_date: last.date,
    current: {
      date: last.date,
      close: last.close,
      streak: last.streak,
      day_change_pct: 0.42,
    },
    stats: {
      max_streak: 3,
      max_streak_end: last.date,
      runs_total: 15,
      runs_ge_current: 1,
      avg_run: 1.93,
      up_day_pct: 52.5,
    },
    series,
  };
}

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

async function setupMocks(
  page: import("@playwright/test").Page,
  streaksPayload?: Record<string, unknown>,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/streaks*", (route) => {
    const url = new URL(route.request().url());
    const symbol = (url.searchParams.get("symbol") ?? "SPY").toUpperCase();
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(streaksPayload ?? buildPayload(symbol)),
    });
  });
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_EMPTY) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/prices", (route) => route.abort());
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: false }) }),
  );
}

test.describe("/regime/streaks — Consecutive Daily Gains tab", () => {
  test("activates the STREAKS tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/streaks");

    // Key on the accessibility contract, not CSS: RegimeRail marks the active
    // item with aria-current="page".
    const rail = page.getByRole("navigation", { name: "Regime indicators" });
    await expect(rail.locator('[aria-current="page"]')).toHaveAttribute("data-tab", "streaks");

    const current = page.locator('[data-testid="streaks-strip-current"]');
    await current.waitFor({ timeout: 10_000 });
    await expect(current).toContainText("3 DAYS");
    await expect(page.locator('[data-testid="streaks-strip-record"]')).toContainText("LAST HIT");
    await expect(page.locator('[data-testid="streaks-strip-precedent"]')).toContainText("1 RUN");
    await expect(page.locator('[data-testid="streaks-strip-last"]')).toContainText("YAHOO");
  });

  test("renders the two-pane chart: price path, streak bars, brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/streaks");

    const section = page.locator('[data-testid="streaks-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section.getByTestId("streaks-price-path")).toBeVisible();
    const bars = await section.getByTestId("streaks-bar").count();
    expect(bars).toBeGreaterThanOrEqual(10);
    await expect(section.locator('[data-testid="streaks-brush"]')).toBeVisible();
    await expect(section).toContainText("DAILY CLOSE VS CONSECUTIVE DAILY GAINS");
    await page.screenshot({ path: "test-results/streaks-tab-chart.png", fullPage: true });
  });

  test("submitting a new symbol requests it and updates the URL", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/streaks");

    // Let the initial SPY payload settle before driving the form — clicking
    // mid-hydration on a cold-compiled route can lose the submit.
    await page.locator('[data-testid="streaks-strip-current"]').waitFor({ timeout: 10_000 });
    const input = page.locator('[data-testid="streaks-symbol-input"]');

    const request = page.waitForRequest((req) => req.url().includes("/api/streaks?symbol=QQQ"));
    await input.fill("QQQ");
    await page.locator('[data-testid="streaks-form"] button[type="submit"]').click();
    await request;

    await expect(page).toHaveURL(/\/regime\/streaks\?symbol=QQQ/);
    await expect(page.getByText("QQQ DAILY CLOSE VS CONSECUTIVE DAILY GAINS")).toBeVisible({ timeout: 10_000 });
  });

  test("shows the missing state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, {
      symbol: "SPY",
      scan_time: null,
      source: null,
      missing: true,
      count: 0,
      first_date: null,
      last_date: null,
      current: null,
      stats: null,
      series: [],
    });
    await page.goto("/regime/streaks");

    await expect(page.getByText("No daily history for SPY")).toBeVisible({ timeout: 10_000 });
  });
});
