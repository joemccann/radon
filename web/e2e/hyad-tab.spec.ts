import { test, expect } from "@playwright/test";

const DAY_MS = 86_400_000;
const SERIES_LENGTH = 240;

function isoDaysAgo(days: number): string {
  return new Date(Date.now() - days * DAY_MS).toISOString().slice(0, 10);
}

const DATA_DATE = isoDaysAgo(1);

function buildSeries() {
  const points = [];
  let cum = 0;
  for (let i = 0; i < SERIES_LENGTH; i++) {
    const net = Math.round(500 * Math.sin(i / 12));
    cum += net;
    points.push({
      date: i === SERIES_LENGTH - 1 ? DATA_DATE : isoDaysAgo(SERIES_LENGTH - i),
      net,
      cum,
      ma21: i >= 20 ? cum - 40 : null,
      ma50: i >= 49 ? cum - 90 : null,
      spx_close: i % 11 === 0 ? null : 5200 + 3 * i,
    });
  }
  return points;
}

const SERIES = buildSeries();

// Current mirrors the spec payload: cum below ma21 below ma50 is DETERIORATING.
const HYAD_MOCK = {
  scan_time: new Date().toISOString(),
  data_date: DATA_DATE,
  current: {
    date: DATA_DATE,
    advances: 1227,
    declines: 1504,
    unchanged: 69,
    total: 3163,
    net: -277,
    cum: -2535,
    ma21: -1010.4,
    ma50: 850.2,
  },
  series: SERIES,
};

const MISSING_HYAD = {
  missing: true,
  scan_time: null,
  data_date: null,
  current: null,
  series: [],
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
  payload: Record<string, unknown> = HYAD_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/hyad", (route) =>
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

test.describe("/regime/hyad - high yield breadth tab", () => {
  test("activates the HY AD tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/hyad");

    await expect(page.locator('.regime-rail__item[data-tab="hyad"]')).toHaveClass(/active/);

    const cum = page.locator('[data-testid="hyad-cum"]');
    await cum.waitFor({ timeout: 10_000 });
    await expect(cum).toHaveText("-2,535");
    await expect(page.locator('[data-testid="hyad-net"]')).toHaveText("-277");
    await expect(page.locator('[data-testid="hyad-ma21"]')).toHaveText("-1,010");
    await expect(page.locator('[data-testid="hyad-ma50"]')).toHaveText("+850");
    await expect(page.locator('[data-testid="hyad-regime"]')).toHaveText("DETERIORATING");
    await expect(page.locator('[data-testid="hyad-updated"]')).toContainText(DATA_DATE);
  });

  test("renders the cumulative A-D line with the SPX overlay and brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/hyad");

    const section = page.locator('[data-testid="hyad-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    expect(await section.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(2);

    await expect(section).toContainText("HIGH YIELD BOND CUMULATIVE A-D LINE");
    await expect(section.locator('[data-testid="hyad-brush"]')).toBeVisible();
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, MISSING_HYAD);

    const failedApiResponses: string[] = [];
    page.on("response", (res) => {
      if (res.url().includes("/api/hyad") && res.status() >= 400) {
        failedApiResponses.push(`${res.status()} ${res.url()}`);
      }
    });

    await page.goto("/regime/hyad");

    await expect(page.getByText("No high yield breadth data yet")).toBeVisible({ timeout: 10_000 });
    expect(failedApiResponses).toEqual([]);
  });
});
