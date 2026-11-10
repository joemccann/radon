import { test, expect } from "@playwright/test";

const DAY_MS = 86_400_000;
const SERIES_LENGTH = 240;

function isoDaysAgo(days: number): string {
  return new Date(Date.now() - days * DAY_MS).toISOString().slice(0, 10);
}

const DATA_DATE = isoDaysAgo(1);

function buildSeries() {
  const points = [];
  for (let i = 0; i < SERIES_LENGTH; i++) {
    points.push({
      date: i === SERIES_LENGTH - 1 ? DATA_DATE : isoDaysAgo(SERIES_LENGTH - i),
      streak: i % 40,
      close: Number((5000 + i * 11.5).toFixed(2)),
    });
  }
  return points;
}

const SERIES = buildSeries();

const CALM_STREAK_MOCK = {
  schema_version: 1,
  scan_time: new Date().toISOString(),
  data_date: DATA_DATE,
  source_last_modified: null,
  source: { name: "cboe", url: "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_SPX.json" },
  threshold_pct: 1,
  current: { date: DATA_DATE, streak: 28, band_pct: 0.7312, close: 7619.98 },
  stats: {
    max: { streak: 64, date: "2017-03-20" },
    window: { start: "1996-01-01", end: "2016-12-31", streak: 26, date: "2014-06-23" },
    percentile: 98.9,
  },
  series: SERIES,
  missing: false,
};

const MISSING_CALM_STREAK = {
  missing: true,
  scan_time: null,
  data_date: null,
  current: null,
  stats: null,
  series: [],
};

const PORTFOLIO_EMPTY = { bankroll: 100_000, positions: [], account_summary: {}, exposure: {}, violations: [] };

const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

async function setupMocks(
  page: import("@playwright/test").Page,
  payload: Record<string, unknown> = CALM_STREAK_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/calm-streak", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) }),
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

test.describe("/regime/calm-streak - SPX calm streak tab", () => {
  test("activates the CALM STREAK tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/calm-streak");

    await expect(page.locator('.regime-rail__item[data-tab="calm-streak"]')).toHaveClass(/active/);

    const value = page.locator('[data-testid="calm-streak-value"]');
    await value.waitFor({ timeout: 10_000 });
    await expect(value).toHaveText("28");
    await expect(page.locator('[data-testid="calm-streak-state"]')).toHaveText("EXTREME CALM");
    await expect(page.locator('[data-testid="calm-streak-band"]')).toHaveText("0.73%");
    await expect(page.locator('[data-testid="calm-streak-window-max"]')).toHaveText("26");
    await expect(page.locator('[data-testid="calm-streak-max"]')).toHaveText("64");
    await expect(page.locator('[data-testid="calm-streak-percentile"]')).toHaveText("98.9%");
    await expect(page.locator('[data-testid="calm-streak-strip-asof"]')).toHaveText(DATA_DATE);
  });

  test("renders the SPX overlay, the streak series, and the brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/calm-streak");

    const section = page.locator('[data-testid="calm-streak-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    expect(await section.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(2);
    await expect(section).toContainText("CONSECUTIVE SESSIONS WITHOUT A >1% INTRADAY BAND");
    await expect(section.locator('[data-testid="calm-streak-brush"]')).toBeVisible();
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, MISSING_CALM_STREAK);

    const failedApiResponses: string[] = [];
    page.on("response", (res) => {
      if (res.url().includes("/api/calm-streak") && res.status() >= 400) {
        failedApiResponses.push(`${res.status()} ${res.url()}`);
      }
    });

    await page.goto("/regime/calm-streak");

    await expect(page.getByText("No calm streak data yet")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/the calm-streak refresh timer/i)).toBeVisible();
    expect(failedApiResponses).toEqual([]);
  });
});
