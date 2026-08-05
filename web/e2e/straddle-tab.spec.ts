import { test, expect } from "@playwright/test";

// ~300 daily sessions ending 2026-08-04. Ratios cycle through the band and
// beyond it; the first row carries a null ratio (no prior session).
function buildSeries() {
  const series: Array<{
    date: string;
    spx_close: number;
    vix1d_close: number;
    ratio: number | null;
  }> = [];
  const day = new Date(Date.UTC(2025, 5, 2));
  let i = 0;
  while (series.length < 300) {
    const dow = day.getUTCDay();
    if (dow !== 0 && dow !== 6) {
      series.push({
        date: day.toISOString().slice(0, 10),
        spx_close: 6000 + i * 5,
        vix1d_close: 12 + (i % 5),
        ratio: i === 0 ? null : ((i % 7) - 3) * 0.9,
      });
      i += 1;
    }
    day.setUTCDate(day.getUTCDate() + 1);
  }
  return series;
}

const SERIES = buildSeries();

const STRADDLE_MOCK = {
  scan_time: new Date().toISOString(),
  source_last_modified: {
    spx: "Wed, 05 Aug 2026 17:01:43 GMT",
    vix1d: "Wed, 05 Aug 2026 18:31:25 GMT",
  },
  count: SERIES.length,
  current: {
    date: SERIES[SERIES.length - 1].date,
    ratio: 3.775801,
    move_pct: 1.789619,
    implied_straddle_pct: 0.473971,
    spx_close: SERIES[SERIES.length - 1].spx_close,
    vix1d_prior: 9.43,
  },
  stats: { high: 3.775801, low: -4.968372, avg: 0.063255, stddev: 1.157428, hit_rate: 0.367675 },
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
  straddlePayload: Record<string, unknown> = STRADDLE_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/straddle", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(straddlePayload) }),
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

test.describe("/regime/straddle — SPX 1-day straddle ratio tab", () => {
  test("activates the STRADDLE tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/straddle");

    await expect(page.locator(".ticker-tab", { hasText: "STRADDLE" })).toHaveClass(/active/);

    const last = page.locator('[data-testid="straddle-last-value"]');
    await last.waitFor({ timeout: 10_000 });
    await expect(last).toHaveText("+3.78");

    await expect(page.locator('[data-testid="straddle-strip-low"]')).toContainText("-4.97");
    await expect(page.locator('[data-testid="straddle-strip-hit-rate"]')).toContainText("36.8%");
    await expect(page.locator('[data-testid="straddle-strip-implied"]')).toContainText("0.47%");
    await expect(page.locator('[data-testid="straddle-strip-source"]')).toContainText("05 Aug 2026");
  });

  test("renders the dual-axis chart with both series and the brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/straddle");

    const section = page.locator('[data-testid="straddle-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section).toContainText("SPX VS 1-DAY STRADDLE RATIO");

    // Two drawn series lines (SPX left + ratio right).
    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    expect(await section.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(2);

    await expect(section.locator('[data-testid="straddle-brush"]')).toBeVisible();
    await expect(section.locator('[data-testid="straddle-range-chips"]')).toBeVisible();
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, {
      missing: true,
      scan_time: null,
      count: 0,
      series: [],
      current: null,
      stats: null,
    });
    await page.goto("/regime/straddle");

    await expect(page.getByText("No straddle data yet")).toBeVisible({ timeout: 10_000 });
  });
});
