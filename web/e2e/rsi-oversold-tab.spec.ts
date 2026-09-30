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
    const pct = Number((2 + 10 * Math.abs(Math.sin(i / 30))).toFixed(4));
    points.push({
      date: i === SERIES_LENGTH - 1 ? DATA_DATE : isoDaysAgo(SERIES_LENGTH - i),
      pct_below_30: pct,
      count_below_30: Math.round(pct * 5),
      eligible: 500,
      spx_close: Number((5000 + i * 11.5).toFixed(2)),
    });
  }
  return points;
}

const SERIES = buildSeries();

const RSI_OVERSOLD_MOCK = {
  schema_version: 1,
  scan_time: new Date().toISOString(),
  data_date: DATA_DATE,
  source: { constituents: "cache", constituents_count: 503, member_close_fetches: { yahoo: 490, stored: 13 } },
  threshold: 10.0,
  current: {
    ...SERIES[SERIES.length - 1],
    pct_below_30: 12.4,
    count_below_30: 62,
    eligible: 500,
    spx_close: 6630.0,
    state: "OVERSOLD CLUSTER",
    cross_up: false,
    highest_since: "2026-03-13",
  },
  series: SERIES,
  missing: false,
};

const MISSING_RSI_OVERSOLD = {
  missing: true,
  scan_time: null,
  data_date: null,
  current: null,
  series: [],
  threshold: null,
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
  payload: Record<string, unknown> = RSI_OVERSOLD_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/rsi-oversold", (route) =>
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

test.describe("/regime/rsi-oversold - SPX RSI oversold breadth tab", () => {
  test("activates the RSI OVERSOLD tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/rsi-oversold");

    await expect(page.locator('.regime-rail__item[data-tab="rsi-oversold"]')).toHaveClass(/active/);

    const pct = page.locator('[data-testid="rsi-oversold-value"]');
    await pct.waitFor({ timeout: 10_000 });
    await expect(pct).toHaveText("12.4%");
    await expect(page.locator('[data-testid="rsi-oversold-state"]')).toHaveText("OVERSOLD CLUSTER");
    await expect(page.locator('[data-testid="rsi-oversold-members"]')).toHaveText("62 / 500");
    await expect(page.locator('[data-testid="rsi-oversold-strip-asof"]')).toHaveText(DATA_DATE);
  });

  test("renders the SPX overlay, the pct series, the threshold line, and the brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/rsi-oversold");

    const section = page.locator('[data-testid="rsi-oversold-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    expect(await section.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(2);

    await expect(section).toContainText("SPX PCT OF MEMBERS WITH RSI(14) BELOW 30");
    await expect(section.locator('[data-testid="chart-reference-band"]')).toBeVisible();
    await expect(section.locator('[data-testid="rsi-oversold-brush"]')).toBeVisible();
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, MISSING_RSI_OVERSOLD);

    const failedApiResponses: string[] = [];
    page.on("response", (res) => {
      if (res.url().includes("/api/rsi-oversold") && res.status() >= 400) {
        failedApiResponses.push(`${res.status()} ${res.url()}`);
      }
    });

    await page.goto("/regime/rsi-oversold");

    await expect(page.getByText("No RSI oversold data yet")).toBeVisible({ timeout: 10_000 });
    expect(failedApiResponses).toEqual([]);
  });
});
