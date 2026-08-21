import { test, expect } from "@playwright/test";

function buildSeries() {
  const rows = [];
  for (let i = 0; i < 24; i++) {
    const day = new Date(Date.UTC(2026, 6, 28 + i));
    rows.push({
      date: day.toISOString().slice(0, 10),
      hyg_close: Number((80.6 - i * 0.04).toFixed(2)),
      spx_close: Number((6816 + i * 35).toFixed(2)),
    });
  }
  const last = rows[rows.length - 1];
  last.date = "2026-08-20";
  last.hyg_close = 79.56;
  last.spx_close = 7641.16;
  return rows;
}

const SERIES = buildSeries();

const CREDIT_MOCK = {
  scan_time: new Date().toISOString(),
  source: "ib",
  count: SERIES.length,
  current: {
    date: "2026-08-20",
    hyg_close: 79.55999755859375,
    spx_close: 7641.16015625,
    hyg_ret: -0.013025716955806343,
    spx_ret: 0.12097839201868865,
    regime: "divergent",
    near_high: true,
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
  creditPayload: Record<string, unknown> = CREDIT_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/credit-spread", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(creditPayload) }),
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

test.describe("/regime/credit — HYG vs S&P 500 tab", () => {
  test("activates the CREDIT tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/credit");

    await expect(page.locator('.regime-rail__item[data-tab="credit"]')).toHaveClass(/active/);

    const regime = page.locator('[data-testid="credit-spread-regime-value"]');
    await regime.waitFor({ timeout: 10_000 });
    await expect(regime).toHaveText("DIVERGENT");
    await expect(page.locator('[data-testid="credit-spread-hyg-ret"]')).toHaveText("-1.30%");
    await expect(page.locator('[data-testid="credit-spread-spx-ret"]')).toHaveText("+12.10%");
    await expect(page.locator('[data-testid="credit-spread-session"]')).toContainText("20 Aug 2026");
  });

  test("renders the dual-axis chart with both series and the brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/credit");

    const section = page.locator('[data-testid="credit-spread-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    expect(await section.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(2);

    await expect(section).toContainText("Interactive Brokers daily closes");
    await expect(section.locator('[data-testid="credit-spread-brush"]')).toBeVisible();
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, {
      missing: true,
      scan_time: null,
      count: 0,
      series: [],
      current: null,
    });

    const failedApiResponses: string[] = [];
    page.on("response", (res) => {
      if (res.url().includes("/api/credit-spread") && res.status() >= 400) {
        failedApiResponses.push(`${res.status()} ${res.url()}`);
      }
    });

    await page.goto("/regime/credit");

    await expect(page.getByText("No credit series yet")).toBeVisible({ timeout: 10_000 });
    expect(failedApiResponses).toEqual([]);
  });
});
