import { test, expect } from "@playwright/test";

const CRI_MOCK = {
  scan_time: "2026-03-11T10:28:24",
  market_open: false,
  vix: 24.79,
  vvix: 125.29,
  spy: 676.39,
  vix_5d_roc: -0.56,
  vvix_vix_ratio: 5.05,
  realized_vol: 12.49,
  cor1m: 29.78,
  cor1m_5d_change: 7.42,
  spx_100d_ma: 682.19,
  spx_distance_pct: -0.85,
  spy_closes: Array.from({ length: 21 }, (_, index) => 650 + index),
  cri: {
    score: 21,
    level: "ELEVATED",
    components: {
      vix: 6.8,
      vvix: 12.3,
      correlation: 4.4,
      momentum: 2.1,
    },
  },
  crash_trigger: {
    triggered: false,
    conditions: {
      spx_below_100d_ma: false,
      realized_vol_gt_25: false,
      cor1m_gt_60: false,
    },
  },
  cta: {
    exposure_pct: 82,
    forced_reduction_pct: 0,
    est_selling_bn: 0,
  },
  history: Array.from({ length: 20 }, (_, index) => ({
    date: `Mar ${index + 1}`,
    vix: 18 + index * 0.35,
    vvix: 100 + index * 1.5,
    realized_vol: 11.5 + index * 0.08,
    cor1m: 12 + index * 0.9,
  })),
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

async function setupMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/regime", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(CRI_MOCK),
    }),
  );
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_EMPTY),
    }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_EMPTY),
    }),
  );
  await page.route("**/api/prices", (route) => route.abort());
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: false }),
    }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  await page.route("**/api/menthorq/cta", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ tables: [] }),
    }),
  );
}

test.describe("/regime page — 20-session history tooltip", () => {
  test("keeps the info icon beside the title and explains the charts in plain English", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    const header = page.locator('[data-testid="regime-history-header"]');
    const titleText = header.locator('[data-testid="regime-history-title-text"]');
    const trigger = header.locator('[data-testid="regime-history-tooltip-trigger"]');

    await expect(header).toBeVisible();
    await expect(titleText).toHaveText("20-SESSION HISTORY");
    await expect(trigger).toBeVisible();

    const [titleBox, triggerBox] = await Promise.all([
      titleText.boundingBox(),
      trigger.boundingBox(),
    ]);

    expect(titleBox).not.toBeNull();
    expect(triggerBox).not.toBeNull();
    expect(triggerBox!.x).toBeGreaterThan(titleBox!.x + titleBox!.width - 1);

    await trigger.hover();

    const tooltip = page.locator('[data-testid="regime-history-tooltip-bubble"]');
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toContainText("The VIX / VVIX chart tracks VIX and VVIX across the last 20 trading sessions");
    await expect(tooltip).toContainText("The realized vol / COR1M chart compares realized volatility with COR1M over the same window");
    await expect(tooltip).toContainText("VIX");
    await expect(tooltip).toContainText("VVIX");
    await expect(tooltip).toContainText("realized volatility");
    await expect(tooltip).toContainText("COR1M");
    await expect(tooltip).not.toContainText(/\bD3\b|\bWS\b|websocket/i);
  });
});
