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

const SECTORS = ["XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC"];

const CURRENT = {
  date: DATA_DATE,
  z_vix: -0.31,
  z_stock: 2.38,
  z_sector: 2.41,
  vix: 14.43,
  stock_spread: 0.0712,
  sector_spread: 0.0241,
  m60_vix: 15.9,
  m60_stock: 0.0834,
  m60_sector: 0.0302,
  n_stocks: 501,
  n_sectors: 11,
  regime: "BELOW THE SURFACE",
  surface_gap: 2.72,
};

function buildSeries() {
  const points = [];
  for (let i = 0; i < SERIES_LENGTH; i++) {
    points.push({
      date: daysAgo(SERIES_LENGTH - i),
      // Smooth z-scores inside a plausible -2..+3 band, never NaN.
      z_vix: 0.4 + 1.2 * Math.sin(i / 20),
      z_stock: 0.8 + 1.5 * Math.sin(i / 25),
      z_sector: 0.6 + 1.4 * Math.sin(i / 30),
      vix: 16 + 4 * Math.sin(i / 20),
      stock_spread: 0.06 + 0.02 * Math.sin(i / 25),
      sector_spread: 0.02 + 0.008 * Math.sin(i / 30),
    });
  }
  points[SERIES_LENGTH - 1] = {
    date: DATA_DATE,
    z_vix: CURRENT.z_vix,
    z_stock: CURRENT.z_stock,
    z_sector: CURRENT.z_sector,
    vix: CURRENT.vix,
    stock_spread: CURRENT.stock_spread,
    sector_spread: CURRENT.sector_spread,
  };
  return points;
}

// Mirrors the spec payload (docs/indicators/dispersion.md section D).
const DISPERSION_MOCK = {
  scan_time: new Date().toISOString(),
  status: "ok",
  source: { prices: "ib", vix: "ib" },
  data_date: DATA_DATE,
  universe: { index: "SPX", n_constituents: 503, sectors: SECTORS },
  fetch: { ib_ok: 512, yahoo_ok: 2, failed: 1, failed_symbols: ["FOO"] },
  count: SERIES_LENGTH,
  current: CURRENT,
  stats: {
    base: { start: "2017-01-03", end: DATA_DATE, n: SERIES_LENGTH },
    vix: { mean_60d: 18.9, stdev_60d: 6.1, z_min: -1.2, z_max: 5.3 },
    stock: { mean_60d: 0.061, stdev_60d: 0.014, z_min: -1.4, z_max: 4.1 },
    sector: { mean_60d: 0.019, stdev_60d: 0.006, z_min: -1.3, z_max: 3.9 },
    days_below_surface: 214,
    last_below_surface_date: DATA_DATE,
  },
  series: buildSeries(),
};

const MISSING_DISPERSION = {
  missing: true,
  scan_time: null,
  status: null,
  source: null,
  data_date: null,
  universe: null,
  fetch: null,
  count: 0,
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
  payload: Record<string, unknown> = DISPERSION_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/dispersion", (route) =>
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

test.describe("/regime/dispersion - volatility dispersion tab", () => {
  test("activates the DISPERSION tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/dispersion");

    await expect(page.locator('.regime-rail__item[data-tab="dispersion"]')).toHaveClass(/active/);

    const regime = page.locator('[data-testid="dispersion-regime"]');
    await regime.waitFor({ timeout: 10_000 });
    await expect(regime).toHaveText("BELOW THE SURFACE");
    await expect(page.locator('[data-testid="dispersion-z-stock"]')).toHaveText("+2.38");
    await expect(page.locator('[data-testid="dispersion-z-sector"]')).toHaveText("+2.41");
    await expect(page.locator('[data-testid="dispersion-z-vix"]')).toHaveText("-0.31");
    await expect(page.locator('[data-testid="dispersion-gap"]')).toHaveText("+2.72");
    await expect(page.locator('[data-testid="dispersion-source-updated"]')).toHaveText(DATA_DATE);
  });

  test("renders the three z-score lines with the range chips and brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/dispersion");

    const section = page.locator('[data-testid="dispersion-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section).toContainText("VOLATILITY DISPERSION - Z-SCORE SINCE 2017");
    await expect(section.locator('[data-testid="dispersion-line-z_vix"]')).toBeVisible();
    await expect(section.locator('[data-testid="dispersion-line-z_stock"]')).toBeVisible();
    await expect(section.locator('[data-testid="dispersion-line-z_sector"]')).toBeVisible();
    await expect(section.locator(".chart-legend-item")).toHaveCount(3);
    await expect(section.locator('[data-testid="dispersion-range-chips-all"]')).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(section.locator('[data-testid="dispersion-brush"]')).toBeVisible();
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, MISSING_DISPERSION);

    const failedApiResponses: string[] = [];
    page.on("response", (res) => {
      if (res.url().includes("/api/dispersion") && res.status() >= 400) {
        failedApiResponses.push(`${res.status()} ${res.url()}`);
      }
    });

    await page.goto("/regime/dispersion");

    await expect(page.getByText("No dispersion data yet")).toBeVisible({ timeout: 10_000 });
    expect(failedApiResponses).toEqual([]);
  });
});
