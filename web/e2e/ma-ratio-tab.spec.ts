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
    const ratio = Number((0.55 + 0.5 * Math.abs(Math.sin(i / 30))).toFixed(4));
    points.push({
      date: i === SERIES_LENGTH - 1 ? DATA_DATE : isoDaysAgo(SERIES_LENGTH - i),
      pct_above_50: Number((35 + 30 * Math.abs(Math.sin(i / 30))).toFixed(2)),
      pct_above_200: Number((55 + 15 * Math.abs(Math.cos(i / 45))).toFixed(2)),
      ratio,
      spx_close: Number((5000 + i * 11.5).toFixed(2)),
    });
  }
  return points;
}

const SERIES = buildSeries();

const MA_RATIO_MOCK = {
  schema_version: 1,
  scan_time: new Date().toISOString(),
  data_date: DATA_DATE,
  source: { constituents: "cache", constituents_count: 503, member_close_fetches: { yahoo: 490, stored: 13 } },
  zone: { low: 0.25, high: 0.5 },
  current: {
    ...SERIES[SERIES.length - 1],
    pct_above_50: 46.5,
    pct_above_200: 64.6,
    ratio: 0.72,
    count_above_50: 234,
    count_above_200: 325,
    eligible_50: 503,
    eligible_200: 503,
    spx_close: 7631.47,
  },
  series: SERIES,
  missing: false,
};

const MISSING_MA_RATIO = {
  missing: true,
  scan_time: null,
  data_date: null,
  current: null,
  series: [],
  zone: null,
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
  payload: Record<string, unknown> = MA_RATIO_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/ma-ratio", (route) =>
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

test.describe("/regime/ma-ratio - SPX MA breadth ratio tab", () => {
  test("activates the MA RATIO tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/ma-ratio");

    await expect(page.locator('.regime-rail__item[data-tab="ma-ratio"]')).toHaveClass(/active/);

    const ratio = page.locator('[data-testid="ma-ratio-value"]');
    await ratio.waitFor({ timeout: 10_000 });
    await expect(ratio).toHaveText("0.72");
    await expect(page.locator('[data-testid="ma-ratio-state"]')).toHaveText("50D LAGGING");
    await expect(page.locator('[data-testid="ma-ratio-pct50"]')).toHaveText("46.5%");
    await expect(page.locator('[data-testid="ma-ratio-pct200"]')).toHaveText("64.6%");
    await expect(page.locator('[data-testid="ma-ratio-strip-asof"]')).toHaveText(DATA_DATE);
  });

  test("renders the SPX overlay, the ratio series, the zone band, and the brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/ma-ratio");

    const section = page.locator('[data-testid="ma-ratio-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    expect(await section.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(2);

    await expect(section).toContainText("SPX PCT ABOVE 50D MA / PCT ABOVE 200D MA");
    await expect(section.locator('[data-testid="chart-reference-band"]')).toBeVisible();
    await expect(section.locator('[data-testid="ma-ratio-brush"]')).toBeVisible();
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, MISSING_MA_RATIO);

    const failedApiResponses: string[] = [];
    page.on("response", (res) => {
      if (res.url().includes("/api/ma-ratio") && res.status() >= 400) {
        failedApiResponses.push(`${res.status()} ${res.url()}`);
      }
    });

    await page.goto("/regime/ma-ratio");

    await expect(page.getByText("No MA ratio data yet")).toBeVisible({ timeout: 10_000 });
    expect(failedApiResponses).toEqual([]);
  });
});
