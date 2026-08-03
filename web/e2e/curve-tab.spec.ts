import { test, expect } from "@playwright/test";

// 24 daily sessions ending 2026-07-31. Yields drift so the spread narrows
// toward the pinned current value (+0.47), matching the fixture pins.
function buildSeries() {
  const rows = [];
  for (let i = 0; i < 24; i++) {
    const day = new Date(Date.UTC(2026, 6, 8 + i));
    const y2 = 4.1 + i * 0.008;
    const y10 = 4.7 + i * 0.003;
    rows.push({
      date: day.toISOString().slice(0, 10),
      y3m: 3.83,
      y2: Number(y2.toFixed(2)),
      y10: Number(y10.toFixed(2)),
      spread: Number((y10 - y2).toFixed(4)),
      spx_close: 7400 + i * 8,
    });
  }
  const last = rows[rows.length - 1];
  last.y2 = 4.28;
  last.y10 = 4.75;
  last.spread = 0.47;
  return rows;
}

const SERIES = buildSeries();

const YIELD_CURVE_MOCK = {
  scan_time: new Date().toISOString(),
  source: "treasury",
  count: SERIES.length,
  current: { date: "2026-07-31", y3m: 3.83, y2: 4.28, y10: 4.75, spread: 0.47 },
  series: SERIES,
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

async function setupMocks(
  page: import("@playwright/test").Page,
  curvePayload: Record<string, unknown> = YIELD_CURVE_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/yield-curve", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(curvePayload) }),
  );
  await page.route("**/api/yield-curve/live", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        y10: 4.686,
        y3m: 3.7,
        spread_10y_3m: 0.986,
        asof: "2026-08-03T19:35:00Z",
        source: "yahoo",
      }),
    }),
  );
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

test.describe("/regime/curve — 10Y-2Y Treasury spread tab", () => {
  test("activates the CURVE tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/curve");

    await expect(page.locator(".ticker-tab", { hasText: "CURVE" })).toHaveClass(/active/);

    const spread = page.locator('[data-testid="yield-curve-spread-value"]');
    await spread.waitFor({ timeout: 10_000 });
    await expect(spread).toHaveText("+0.47%");

    await expect(page.locator('[data-testid="yield-curve-strip-y10"]')).toContainText("4.75%");
    await expect(page.locator('[data-testid="yield-curve-strip-y2"]')).toContainText("4.28%");
    await expect(page.locator('[data-testid="yield-curve-strip-session"]')).toContainText("31 Jul 2026");
  });

  test("renders the dual-axis chart with both series and the brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/curve");

    const section = page.locator('[data-testid="yield-curve-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    // Two drawn series lines (S&P 500 log-left + spread linear-right).
    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    expect(await section.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(2);

    await expect(section).toContainText("US Treasury daily par yield curve");
    await expect(section.locator('[data-testid="yield-curve-brush"]')).toBeVisible();
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, {
      missing: true,
      scan_time: null,
      count: 0,
      series: [],
      current: null,
    });

    // Scope to the yield-curve API only: unmocked ambient session routes
    // (/api/profile, /api/watchlist) legitimately 401 without a Clerk session.
    const failedApiResponses: string[] = [];
    page.on("response", (res) => {
      if (res.url().includes("/api/yield-curve") && res.status() >= 400) {
        failedApiResponses.push(`${res.status()} ${res.url()}`);
      }
    });

    await page.goto("/regime/curve");

    await expect(page.getByText("No yield curve data yet")).toBeVisible({ timeout: 10_000 });
    expect(failedApiResponses).toEqual([]);
  });
});
