import { test, expect } from "@playwright/test";

// ~300 daily sessions ending 2026-08-04. Ratios walk around 1.3; the first
// row carries a null change (no prior stored session to difference).
function buildSeries() {
  const series: Array<{
    date: string;
    ratio: number;
    change: number | null;
  }> = [];
  const day = new Date(Date.UTC(2025, 5, 2));
  let i = 0;
  while (series.length < 300) {
    const dow = day.getUTCDay();
    if (dow !== 0 && dow !== 6) {
      series.push({
        date: day.toISOString().slice(0, 10),
        ratio: 1.3 + (i % 9) * 0.01,
        change: i === 0 ? null : ((i % 7) - 3) * 0.01,
      });
      i += 1;
    }
    day.setUTCDate(day.getUTCDate() + 1);
  }
  return series;
}

const SERIES = buildSeries();

const SKEW_MOCK = {
  scan_time: new Date().toISOString(),
  source: "unusual_whales",
  market_status: "open",
  count: SERIES.length,
  current: {
    date: SERIES[SERIES.length - 1].date,
    ratio: 1.292999,
    change: -0.12,
    put_iv: 0.159567,
    call_iv: 0.123408,
    expiry: "2026-09-18",
    dte: 44,
    is_intraday: true,
    as_of: new Date().toISOString(),
  },
  stats: { high: 0.13, low: -0.16, avg: 0.0004, stddev: 0.04 },
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
  skewPayload: Record<string, unknown> = SKEW_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/skew", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(skewPayload) }),
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

test.describe("/regime/skew — SPX 1M 25-delta put/call skew tab", () => {
  test("activates the SKEW tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/skew");

    await expect(page.locator('.regime-rail__item[data-tab="skew"]')).toHaveClass(/active/);

    const change = page.locator('[data-testid="skew-change-value"]');
    await change.waitFor({ timeout: 10_000 });
    await expect(change).toHaveText("-0.12");

    await expect(page.locator('[data-testid="skew-strip-level"]')).toContainText("1.29");
    await expect(page.locator('[data-testid="skew-strip-z"]')).toContainText("-3.0");
    await expect(page.locator('[data-testid="skew-strip-put-iv"]')).toContainText("16.0%");
    await expect(page.locator('[data-testid="skew-strip-call-iv"]')).toContainText("12.3%");
    await expect(page.locator('[data-testid="skew-strip-tenor"]')).toContainText("2026-09-18");
    await expect(page.locator('[data-testid="skew-live-status"]')).toContainText("LIVE");
    await expect(page.locator('[data-testid="skew-strip-date"]')).toContainText("INTRADAY");
  });

  test("renders the chart, the view chips, and the brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/skew");

    const section = page.locator('[data-testid="skew-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section).toContainText("SPX 1M 25D PUT/CALL SKEW");

    // Drawn series line for the default CHANGE view.
    await expect(section.locator("svg path[stroke]").first()).toBeVisible();

    const changeChip = section.getByRole("button", { name: "CHANGE" });
    await expect(changeChip).toHaveAttribute("aria-pressed", "true");

    const levelChip = section.getByRole("button", { name: "LEVEL" });
    await levelChip.click();
    await expect(levelChip).toHaveAttribute("aria-pressed", "true");
    await expect(changeChip).toHaveAttribute("aria-pressed", "false");

    await expect(section.locator('[data-testid="skew-brush"]')).toBeVisible();
    await expect(section.locator('[data-testid="skew-range-chips"]')).toBeVisible();
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
    await page.goto("/regime/skew");

    await expect(page.getByText("No skew data yet")).toBeVisible({ timeout: 10_000 });
  });
});
