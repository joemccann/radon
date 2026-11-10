import { test, expect } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";

function buildSeries() {
  const rows = [];
  for (let i = 0; i < 88; i++) {
    const day = new Date(Date.UTC(2026, 4, 26 + i));
    const shy = Number((80 + i * 0.01).toFixed(4));
    const hyg = Number((77.5 - i * 0.005).toFixed(4));
    rows.push({
      date: day.toISOString().slice(0, 10),
      shy_close: shy,
      hyg_close: hyg,
      vix_close: Number((16 + (i % 5) * 0.2).toFixed(4)),
      spread: shy - hyg,
    });
  }
  const last = rows[rows.length - 1];
  last.date = "2026-09-29";
  last.shy_close = 81.16;
  last.hyg_close = 77.36;
  last.vix_close = 16.04;
  last.spread = 3.8;
  return rows;
}

const SERIES = buildSeries();

const CREDIT_VIX_MOCK = {
  scan_time: new Date().toISOString(),
  source: "ib",
  count: SERIES.length,
  current: {
    date: "2026-09-29",
    shy_close: 81.16,
    hyg_close: 77.36,
    vix_close: 16.04,
    spread: 3.8,
    rank_spread: 1,
    rank_vix: 0.146,
    gap: 0.854,
    state: "CREDIT WIDE",
    widest_since: "2015-08-17",
    window_sessions: 88,
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
  payload: Record<string, unknown> = CREDIT_VIX_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/credit-vix", (route) =>
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

test.describe("/regime/credit-vix - CREDIT/VIX tab", () => {
  test("activates the CREDIT/VIX tab and renders the summary strip", async ({ page }, testInfo) => {
    await setupMocks(page);
    await page.goto("/regime/credit-vix");

    await expect(page.locator('.regime-rail__item[data-tab="credit-vix"]')).toHaveClass(/active/);

    const spread = page.locator('[data-testid="credit-vix-spread"]');
    await spread.waitFor({ timeout: 10_000 });
    await expect(spread).toHaveText("3.80");
    await expect(page.locator('[data-testid="credit-vix-state"]')).toHaveText("CREDIT WIDE");
    await expect(page.locator('[data-testid="credit-vix-vix"]')).toHaveText("16.04");
    await expect(page.locator('[data-testid="credit-vix-gap"]')).toHaveText("0.85");
    await page.screenshot({ path: testInfo.outputPath("credit-vix-integration-desktop.png"), fullPage: true });
  });

  test("mobile navigation preserves both RSI OVERSOLD and CREDIT/VIX", async ({ page }, testInfo) => {
    test.slow();
    await page.setViewportSize({ width: 393, height: 852 });
    const requests = await installClearFixtures(page);
    await page.goto("/regime/rsi-oversold");
    await expect(page.getByTestId("rsi-oversold-mobile-grid")).toBeVisible();
    await expect(page.getByRole("tab", { name: "RSI < 30", exact: true })).toHaveAttribute("aria-selected", "true");
    await page.screenshot({ path: testInfo.outputPath("rsi-oversold-integration-mobile.png"), fullPage: true });
    await page.getByRole("tab", { name: "CREDIT/VIX", exact: true }).click();
    await expect(page).toHaveURL(/\/regime\/credit-vix$/);
    await expect(page.getByTestId("credit-vix-mobile-grid")).toBeVisible();
    await expect(page.getByRole("tab", { name: "CREDIT/VIX", exact: true })).toHaveAttribute("aria-selected", "true");
    await page.screenshot({ path: testInfo.outputPath("credit-vix-integration-mobile.png"), fullPage: true });
    await page.getByRole("tab", { name: "RSI < 30", exact: true }).click();
    await expect(page).toHaveURL(/\/regime\/rsi-oversold$/);
    await expect(page.getByTestId("rsi-oversold-mobile-grid")).toBeVisible();
    expect(requests.filter(request => /^(POST|PUT|DELETE) \/api\/orders/.test(request))).toEqual([]);
  });

  test("renders the dual-axis chart with both series and the brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/credit-vix");

    const section = page.locator('[data-testid="credit-vix-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    expect(await section.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(2);

    await expect(section).toContainText("SHY MINUS HYG VS VIX");
    await expect(section.locator('[data-testid="credit-vix-brush"]')).toBeVisible();
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
      if (res.url().includes("/api/credit-vix") && res.status() >= 400) {
        failedApiResponses.push(`${res.status()} ${res.url()}`);
      }
    });

    await page.goto("/regime/credit-vix");

    await expect(page.getByText("No SHY minus HYG vs VIX snapshot")).toBeVisible({ timeout: 10_000 });
    expect(failedApiResponses).toEqual([]);
  });
});
