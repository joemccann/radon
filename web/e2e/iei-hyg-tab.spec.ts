import { test, expect } from "@playwright/test";

function buildSeries() {
  const rows = [];
  for (let i = 0; i < 62; i++) {
    const day = new Date(Date.UTC(2026, 4, 26 + i));
    const iei = Number((117 - i * 0.01).toFixed(4));
    const hyg = Number((80 + i * 0.005).toFixed(4));
    rows.push({
      date: day.toISOString().slice(0, 10),
      iei_close: iei,
      hyg_close: hyg,
      dxy_close: i % 7 === 3 ? null : Number((99 - i * 0.005).toFixed(4)),
      ratio: iei / hyg,
    });
  }
  const last = rows[rows.length - 1];
  last.date = "2026-08-21";
  last.iei_close = 116.41;
  last.hyg_close = 79.61;
  last.dxy_close = 98.8;
  last.ratio = 1.462253520532856;
  return rows;
}

const SERIES = buildSeries();

const IEI_HYG_MOCK = {
  scan_time: new Date().toISOString(),
  source: "ib",
  count: SERIES.length,
  current: {
    date: "2026-08-21",
    iei_close: 116.41,
    hyg_close: 79.61,
    dxy_close: 98.8,
    ratio: 1.462253520532856,
    ratio_52w_low: 1.462253520532856,
    low_date: "2026-08-21",
    ratio_52w_high: 1.475760927676247,
    high_date: "2026-06-26",
    ratio_pct_rank: 0,
    window_sessions: 62,
    state: "new_low",
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
  payload: Record<string, unknown> = IEI_HYG_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/iei-hyg", (route) =>
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

test.describe("/regime/iei-hyg - IEI/HYG ratio tab", () => {
  test("activates the IEI/HYG tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/iei-hyg");

    await expect(page.locator('.regime-rail__item[data-tab="iei-hyg"]')).toHaveClass(/active/);

    const ratio = page.locator('[data-testid="iei-hyg-ratio"]');
    await ratio.waitFor({ timeout: 10_000 });
    await expect(ratio).toHaveText("1.4623");
    await expect(page.locator('[data-testid="iei-hyg-state"]')).toHaveText("NEW 52W LOW");
    await expect(page.locator('[data-testid="iei-hyg-low"]')).toHaveText("1.4623");
    await expect(page.locator('[data-testid="iei-hyg-high"]')).toHaveText("1.4758");
    await expect(page.locator('[data-testid="iei-hyg-rank"]')).toHaveText("0%");
    await expect(page.locator('[data-testid="iei-hyg-dxy"]')).toHaveText("98.80");
    await expect(page.locator('[data-testid="iei-hyg-source"]')).toContainText("21 Aug 2026");
  });

  test("renders the dual-axis chart with both series and the brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/iei-hyg");

    const section = page.locator('[data-testid="iei-hyg-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    expect(await section.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(2);

    await expect(section).toContainText("3-7Y TREASURIES VS HIGH YIELD (IEI / HYG RATIO)");
    await expect(section.locator('[data-testid="iei-hyg-brush"]')).toBeVisible();
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, {
      missing: true,
      scan_time: null,
      source: null,
      count: 0,
      series: [],
      current: null,
    });

    const failedApiResponses: string[] = [];
    page.on("response", (res) => {
      if (res.url().includes("/api/iei-hyg") && res.status() >= 400) {
        failedApiResponses.push(`${res.status()} ${res.url()}`);
      }
    });

    await page.goto("/regime/iei-hyg");

    await expect(page.getByText("No IEI/HYG snapshot")).toBeVisible({ timeout: 10_000 });
    expect(failedApiResponses).toEqual([]);
  });
});
