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
    const total = 480 + (i % 24);
    const pct = Number((5 + 20 * Math.abs(Math.sin(i / 40))).toFixed(2));
    points.push({
      date: i === SERIES_LENGTH - 1 ? DATA_DATE : isoDaysAgo(SERIES_LENGTH - i),
      pct_above: pct,
      count_above: Math.round((pct / 100) * total),
      total,
      y10: Number((4 + (i % 10) * 0.1).toFixed(2)),
      approximate: i < SERIES_LENGTH / 2 ? 1 : 0,
    });
  }
  return points;
}

const SERIES = buildSeries();

const DIVYIELD_MOCK = {
  scan_time: new Date().toISOString(),
  source: { constituents: "github-datasets", constituents_count: 503, quote_errors: 0 },
  data_date: DATA_DATE,
  y10_date: DATA_DATE,
  current: {
    date: DATA_DATE,
    pct_above: 3.78,
    count_above: 19,
    total: 503,
    y10: 4.74,
    leaders: [{ ticker: "TDG", yield_pct: 7.5 }],
  },
  series: SERIES,
  backfill_cutover: SERIES[Math.floor(SERIES_LENGTH / 2)].date,
};

const MISSING_DIVYIELD = {
  missing: true,
  scan_time: null,
  data_date: null,
  current: null,
  series: [],
  backfill_cutover: null,
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
  payload: Record<string, unknown> = DIVYIELD_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/divyield", (route) =>
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

test.describe("/regime/divyield - dividend yield breadth tab", () => {
  test("activates the DIV YIELD tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/divyield");

    await expect(page.locator('.regime-rail__item[data-tab="divyield"]')).toHaveClass(/active/);

    const above = page.locator('[data-testid="divyield-above"]');
    await above.waitFor({ timeout: 10_000 });
    await expect(above).toHaveText("3.78%");
    await expect(page.locator('[data-testid="divyield-count"]')).toHaveText("19 / 503");
    await expect(page.locator('[data-testid="divyield-y10"]')).toHaveText("4.74%");
    await expect(page.locator('[data-testid="divyield-regime"]')).toHaveText("SCARCE");
    await expect(page.locator('[data-testid="divyield-updated"]')).toContainText(DATA_DATE);
  });

  test("renders the pct-above series with the brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/divyield");

    const section = page.locator('[data-testid="divyield-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    expect(await section.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(1);

    await expect(section).toContainText("PCT OF SPX STOCKS YIELDING ABOVE 10Y");
    await expect(section.locator('[data-testid="divyield-brush"]')).toBeVisible();
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, MISSING_DIVYIELD);

    const failedApiResponses: string[] = [];
    page.on("response", (res) => {
      if (res.url().includes("/api/divyield") && res.status() >= 400) {
        failedApiResponses.push(`${res.status()} ${res.url()}`);
      }
    });

    await page.goto("/regime/divyield");

    await expect(page.getByText("No dividend yield data yet")).toBeVisible({ timeout: 10_000 });
    expect(failedApiResponses).toEqual([]);
  });
});
