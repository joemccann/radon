import { test, expect } from "@playwright/test";

const SERIES_LENGTH = 90;

// Window-relative dates: the series always ends on the last completed
// session, never a hardcoded date.
function daysAgo(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

const DATA_DATE = daysAgo(1);
const SERIES_LOW_DATE = daysAgo(9);

function buildSeries() {
  const points = [];
  for (let i = 0; i < SERIES_LENGTH; i++) {
    points.push({
      date: i === SERIES_LENGTH - 1 ? DATA_DATE : daysAgo(SERIES_LENGTH - i),
      put_volume: 100_000 + i,
      call_volume: 300_000 + i,
      daily_ratio: i < 4 ? 0.4 : 0.33 + (i % 20) * 0.01,
      cum5_ratio: i < 4 ? null : 0.30 + (i % 15) * 0.01,
    });
  }
  points[SERIES_LENGTH - 1] = {
    date: DATA_DATE,
    put_volume: 573686,
    call_volume: 1729988,
    daily_ratio: 0.33161270482801036,
    cum5_ratio: 0.3188487844029556,
  };
  return points;
}

const VIX_PC_MOCK = {
  scan_time: new Date().toISOString(),
  status: "ok",
  source: "uw",
  as_of: DATA_DATE,
  data_date: DATA_DATE,
  expected_session: DATA_DATE,
  market_status: "closed",
  window: 5,
  count: SERIES_LENGTH,
  cum5_count: SERIES_LENGTH - 4,
  current: {
    date: DATA_DATE,
    put_volume: 573686,
    call_volume: 1729988,
    daily_ratio: 0.33161270482801036,
    cum5_ratio: 0.3188487844029556,
    percentile: 4.032258064516129,
    regime: "CAUTIOUS",
    series_min: 0.2417297884911582,
    series_max: 1.6973206456466932,
    series_p10: 0.3435408949777816,
    series_p90: 0.6760256318262985,
    series_min_date: SERIES_LOW_DATE,
    is_series_low: false,
  },
  stats: {
    min: 0.2417297884911582,
    p10: 0.3435408949777816,
    p25: 0.3943868581804748,
    median: 0.48050480250544647,
    p75: 0.5889207941175402,
    p90: 0.6760256318262985,
    max: 1.6973206456466932,
    mean: 0.5057092921999375,
  },
  series: buildSeries(),
};

const MISSING_VIX_PC = {
  missing: true,
  status: "missing",
  scan_time: null,
  as_of: null,
  data_date: null,
  count: 0,
  series: [],
  current: null,
  stats: null,
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
  payload: Record<string, unknown> = VIX_PC_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/vix-pc", (route) =>
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

test.describe("/regime/vix-pc - VIX put/call tab", () => {
  test("activates the VIX P/C tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/vix-pc");

    await expect(page.locator('.regime-rail__item[data-tab="vix-pc"]')).toHaveClass(/active/);

    const cum5 = page.locator('[data-testid="vix-pc-cum5"]');
    await cum5.waitFor({ timeout: 10_000 });
    await expect(cum5).toHaveText("0.319");
    await expect(page.locator('[data-testid="vix-pc-percentile"]')).toHaveText("4.0");
    await expect(page.locator('[data-testid="vix-pc-regime"]')).toHaveText("CAUTIOUS");
    await expect(page.locator('[data-testid="vix-pc-daily"]')).toHaveText("0.332");
    await expect(page.locator('[data-testid="vix-pc-series-low"]')).toContainText("0.242");
    await expect(page.locator('[data-testid="vix-pc-freshness-rail"]')).toContainText(DATA_DATE);
  });

  test("renders the cum5 series with the range chips and brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/vix-pc");

    const section = page.locator('[data-testid="vix-pc-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    await expect(section).toContainText("VIX 5S PUT/CALL");
    await expect(section.locator('[data-testid="vix-pc-range-chips-all"]')).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(section.locator('[data-testid="vix-pc-brush"]')).toBeVisible();
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, MISSING_VIX_PC);

    const failedApiResponses: string[] = [];
    page.on("response", (res) => {
      if (res.url().includes("/api/vix-pc") && res.status() >= 400) {
        failedApiResponses.push(`${res.status()} ${res.url()}`);
      }
    });

    await page.goto("/regime/vix-pc");

    await expect(page.getByText("No VIX put/call data yet")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/vix-pc refresh timer/i)).toBeVisible();
    expect(failedApiResponses).toEqual([]);
  });
});
