import { test, expect } from "@playwright/test";

// ~300 daily sessions ending 2026-08-07. Every 11th COR3M row is null
// (Cboe's real COR3M history has scattered gaps the other tenors fill).
function buildSeries() {
  const series: Array<{
    date: string;
    cor1m: number | null;
    cor3m: number | null;
    cor6m: number | null;
    cor1y: number | null;
  }> = [];
  const day = new Date(Date.UTC(2025, 5, 2));
  let i = 0;
  while (series.length < 300) {
    const dow = day.getUTCDay();
    if (dow !== 0 && dow !== 6) {
      series.push({
        date: day.toISOString().slice(0, 10),
        cor1m: 20 + (i % 9),
        cor3m: i % 11 === 0 ? null : 25 + (i % 7),
        cor6m: 30 + (i % 5),
        cor1y: 35 + (i % 3),
      });
      i += 1;
    }
    day.setUTCDate(day.getUTCDate() + 1);
  }
  return series;
}

const SERIES = buildSeries();

const COR_MOCK = {
  scan_time: new Date().toISOString(),
  source_last_modified: {
    cor1m: "Fri, 07 Aug 2026 22:31:04 GMT",
    cor3m: "Fri, 07 Aug 2026 22:31:10 GMT",
    cor6m: "Fri, 07 Aug 2026 22:31:16 GMT",
    cor1y: "Fri, 07 Aug 2026 22:31:22 GMT",
  },
  count: SERIES.length,
  current: {
    date: SERIES[SERIES.length - 1].date,
    cor1m: 7.38,
    cor3m: 10.48,
    cor6m: 12.16,
    cor1y: 14.19,
    term_spread: 6.81,
    change_1d: { cor1m: 0.76, cor3m: 1.07, cor6m: 0.98, cor1y: 0.74 },
  },
  stats: {
    cor1m: { high: 96.59, low: 2.93, avg: 37.198579, stddev: 17.867378, percentile: 0.010037 },
    cor3m: { high: 90.79, low: 7.19, avg: 41.240333, stddev: 16.35408, percentile: 0.012582 },
    cor6m: { high: 86.35, low: 9.67, avg: 44.342579, stddev: 15.781472, percentile: 0.0083 },
    cor1y: { high: 79.77, low: 11.9, avg: 46.133582, stddev: 14.713988, percentile: 0.007721 },
  },
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
  corPayload: Record<string, unknown> = COR_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/cor", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(corPayload) }),
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

test.describe("/regime/cor — SPX implied correlation tab", () => {
  test("activates the COR tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/cor");

    await expect(page.locator('.regime-rail__item[data-tab="cor"]')).toHaveClass(/active/);

    const regime = page.locator('[data-testid="cor-regime-value"]');
    await regime.waitFor({ timeout: 10_000 });
    await expect(regime).toContainText("0.8%");
    await expect(regime).toContainText("COMPRESSED");

    await expect(page.locator('[data-testid="cor-strip-1m"]')).toContainText("7.38");
    await expect(page.locator('[data-testid="cor-strip-3m"]')).toContainText("10.48");
    await expect(page.locator('[data-testid="cor-strip-6m"]')).toContainText("12.16");
    await expect(page.locator('[data-testid="cor-strip-1y"]')).toContainText("14.19");
    await expect(page.locator('[data-testid="cor-strip-spread"]')).toContainText("+6.81");
    await expect(page.locator('[data-testid="cor-strip-source"]')).toContainText("07 Aug 2026");
  });

  test("renders the chart with the tenor chips and the brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/cor");

    const section = page.locator('[data-testid="cor-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section).toContainText("SPX IMPLIED CORRELATION");

    await expect(section.locator("svg path[stroke]").first()).toBeVisible();

    const tenorChips = section.locator('[data-testid="cor-tenor-chips"]');
    await expect(tenorChips).toBeVisible();
    for (const label of ["1M", "3M", "6M", "1Y"]) {
      await expect(tenorChips.locator("button", { hasText: label })).toBeVisible();
    }
    await expect(tenorChips.locator("button", { hasText: "6M" })).toHaveAttribute("aria-pressed", "true");

    await expect(section.locator('[data-testid="cor-brush"]')).toBeVisible();
    await expect(section.locator('[data-testid="cor-range-chips"]')).toBeVisible();
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
    await page.goto("/regime/cor");

    await expect(page.getByText("No implied correlation data yet")).toBeVisible({ timeout: 10_000 });
  });
});
