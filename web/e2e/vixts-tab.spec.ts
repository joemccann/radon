import { test, expect } from "@playwright/test";

const SERIES_LENGTH = 600;

// Window-relative dates: the series always ends on the last completed
// session, never a hardcoded date.
function daysAgo(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

const DATA_DATE = daysAgo(1);

function buildSeries() {
  const points = [];
  for (let i = 0; i < SERIES_LENGTH; i++) {
    points.push({
      date: i === SERIES_LENGTH - 1 ? DATA_DATE : daysAgo(SERIES_LENGTH - i),
      vix: 15 + 3 * Math.sin(i / 20),
      vix3m: 18 + 2 * Math.sin(i / 30),
      // Smooth, always inside the plausible 0.40..2.50 band, never NaN.
      ratio: 0.88 + 0.12 * Math.sin(i / 25),
      spx: 6800 + 900 * Math.sin(i / 40),
    });
  }
  points[SERIES_LENGTH - 1] = {
    date: DATA_DATE,
    vix: 15.21,
    vix3m: 17.99,
    ratio: 0.8455,
    spx: 7654.32,
  };
  return points;
}

// Mirrors the spec payload: 15.21 / 17.99 = 0.8455 is CONTANGO.
const VIXTS_MOCK = {
  scan_time: new Date().toISOString(),
  source_last_modified: {
    vix: "Thu, 27 Aug 2026 01:50:46 GMT",
    vix3m: "Wed, 26 Aug 2026 22:00:57 GMT",
    spx: "Thu, 27 Aug 2026 00:31:07 GMT",
  },
  data_date: DATA_DATE,
  count: SERIES_LENGTH,
  current: {
    date: DATA_DATE,
    vix: 15.21,
    vix3m: 17.99,
    ratio: 0.8455,
    regime: "CONTANGO",
    spx: 7654.32,
  },
  stats: {
    min: 0.7104,
    max: 1.3437,
    mean: 0.894398,
    median: 0.8846,
    days_backwardation: 325,
    pct_backwardation: 7.6435,
    last_backwardation_date: daysAgo(140),
  },
  series: buildSeries(),
};

const MISSING_VIXTS = {
  missing: true,
  scan_time: null,
  source_last_modified: null,
  data_date: null,
  current: null,
  stats: null,
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
  payload: Record<string, unknown> = VIXTS_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/vixts", (route) =>
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

test.describe("/regime/vixts - VIX term structure tab", () => {
  test("activates the VIX TS tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/vixts");

    await expect(page.locator('.regime-rail__item[data-tab="vixts"]')).toHaveClass(/active/);

    const ratio = page.locator('[data-testid="vixts-ratio"]');
    await ratio.waitFor({ timeout: 10_000 });
    await expect(ratio).toHaveText("0.8455");
    await expect(page.locator('[data-testid="vixts-regime"]')).toHaveText("CONTANGO");
    await expect(page.locator('[data-testid="vixts-vix"]')).toHaveText("15.21");
    await expect(page.locator('[data-testid="vixts-vix3m"]')).toHaveText("17.99");
    await expect(page.locator('[data-testid="vixts-source-updated"]')).toHaveText(DATA_DATE);
  });

  test("renders the ratio and SPX series with the range chips and brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/vixts");

    const section = page.locator('[data-testid="vixts-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    await expect(section).toContainText("VIX TERM STRUCTURE - VIX / VIX3M");
    await expect(section.locator('[data-testid="vixts-range-chips-all"]')).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(section.locator('[data-testid="vixts-brush"]')).toBeVisible();
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, MISSING_VIXTS);

    const failedApiResponses: string[] = [];
    page.on("response", (res) => {
      if (res.url().includes("/api/vixts") && res.status() >= 400) {
        failedApiResponses.push(`${res.status()} ${res.url()}`);
      }
    });

    await page.goto("/regime/vixts");

    await expect(page.getByText("No VIX term structure data yet")).toBeVisible({ timeout: 10_000 });
    expect(failedApiResponses).toEqual([]);
  });
});
