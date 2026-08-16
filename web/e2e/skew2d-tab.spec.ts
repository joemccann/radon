import { test, expect } from "@playwright/test";

// ~300 daily sessions. First two rows carry null change (need t-2).
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
        change: i < 2 ? null : ((i % 7) - 3) * 0.01,
      });
      i += 1;
    }
    day.setUTCDate(day.getUTCDate() + 1);
  }
  return series;
}

const SERIES = buildSeries();

const SKEW2D_MOCK = {
  scan_time: new Date().toISOString(),
  source: "skew_history",
  market_status: "closed",
  count: SERIES.length,
  current: {
    date: SERIES[SERIES.length - 1].date,
    ratio: 1.23838134631528,
    change: 0.08,
    put_iv: 0.15,
    call_iv: 0.12,
    expiry: "2026-08-21",
    dte: 14,
  },
  stats: { high: 0.27, low: -0.21, avg: 0, stddev: 0.03 },
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
  skew2dPayload: Record<string, unknown> = SKEW2D_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/skew2d", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(skew2dPayload) }),
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

test.describe("/regime/skew2d — 2d change in SPX 1M put/call skew tab", () => {
  test("activates the SKEW 2D tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/skew2d");

    await expect(page.locator('.regime-rail__item[data-tab="skew2d"]')).toHaveClass(/active/);

    const change = page.locator('[data-testid="skew2d-change-value"]');
    await change.waitFor({ timeout: 10_000 });
    await expect(change).toHaveText("+0.08");

    await expect(page.locator('[data-testid="skew2d-strip-level"]')).toContainText("1.24");
    await expect(page.locator('[data-testid="skew2d-strip-z"]')).toContainText("+2.7");
    await expect(page.locator('[data-testid="skew2d-strip-tenor"]')).toContainText("2026-08-21");
  });

  test("renders the chart, the view chips, and the brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/skew2d");

    const section = page.locator('[data-testid="skew2d-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section).toContainText("2D CHANGE IN 1M SPX PUT/CALL SKEW");

    await expect(section.locator("svg path[stroke]").first()).toBeVisible();

    const changeChip = section.getByRole("button", { name: "CHANGE" });
    await expect(changeChip).toHaveAttribute("aria-pressed", "true");

    const levelChip = section.getByRole("button", { name: "LEVEL" });
    await levelChip.click();
    await expect(levelChip).toHaveAttribute("aria-pressed", "true");
    await expect(changeChip).toHaveAttribute("aria-pressed", "false");

    await expect(section.locator('[data-testid="skew2d-brush"]')).toBeVisible();
    await expect(section.locator('[data-testid="skew2d-range-chips"]')).toBeVisible();
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
    await page.goto("/regime/skew2d");

    await expect(page.getByText("No 2d skew data yet")).toBeVisible({ timeout: 10_000 });
  });
});
