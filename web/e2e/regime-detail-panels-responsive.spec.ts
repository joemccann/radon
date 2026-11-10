import { expect, test } from "@playwright/test";

const CRI_MOCK = {
  scan_time: "2026-03-12T09:21:45",
  market_open: true,
  date: "2026-03-12",
  vix: 25.98,
  vvix: 124.49,
  spy: 669.83,
  vix_5d_roc: 9.5,
  vvix_vix_ratio: 4.87,
  realized_vol: 11.86,
  cor1m: 31.76,
  cor1m_previous_close: 31.93,
  cor1m_5d_change: 7.03,
  spx_100d_ma: 682.2,
  spx_distance_pct: -1.84,
  spy_closes: Array.from({ length: 40 }, (_, index) => 640 + index),
  cri: {
    score: 31,
    level: "ELEVATED",
    components: {
      vix: 8,
      vvix: 12.4,
      correlation: 5.4,
      momentum: 4.2,
    },
  },
  crash_trigger: {
    triggered: false,
    conditions: {
      spx_below_100d_ma: true,
      realized_vol_gt_25: false,
      cor1m_gt_60: false,
    },
  },
  cta: {
    exposure_pct: 74,
    forced_reduction_pct: 0,
    est_selling_bn: 0,
  },
  menthorq_cta: null,
  history: Array.from({ length: 20 }, (_, index) => ({
    date: `2026-02-${String(index + 1).padStart(2, "0")}`,
    vix: 20 + index * 0.3,
    vvix: 105 + index * 1.1,
    spy: 650 + index,
    realized_vol: 11.2 + index * 0.1,
    cor1m: 18 + index * 0.7,
    spx_vs_ma_pct: -1.8 + index * 0.1,
    vix_5d_roc: 0.4 + index * 0.2,
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

test.describe("/regime page — responsive detail-panel collapse", () => {
  test("keeps the CRI components and crash trigger panels side by side on wide screens", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 1500 });
    await setupMocks(page);
    await page.goto("/regime");

    const [componentsBox, triggersBox] = await Promise.all([
      page.locator(".regime-components").boundingBox(),
      page.locator(".regime-triggers").boundingBox(),
    ]);

    expect(componentsBox).not.toBeNull();
    expect(triggersBox).not.toBeNull();
    expect(Math.abs((componentsBox?.y ?? 0) - (triggersBox?.y ?? 0))).toBeLessThan(20);
    expect((triggersBox?.x ?? 0)).toBeGreaterThan((componentsBox?.x ?? 0) + 40);
  });

  test("stacks the CRI components and crash trigger panels vertically on narrower widths", async ({ page }) => {
    await page.setViewportSize({ width: 700, height: 1500 });
    await setupMocks(page);
    await page.goto("/regime");

    const grid = page.locator(".regime-detail-grid");
    const [componentsBox, triggersBox] = await Promise.all([
      page.locator(".regime-components").boundingBox(),
      page.locator(".regime-triggers").boundingBox(),
    ]);

    expect(componentsBox).not.toBeNull();
    expect(triggersBox).not.toBeNull();
    expect((triggersBox?.y ?? 0)).toBeGreaterThan((componentsBox?.y ?? 0) + (componentsBox?.height ?? 0) - 20);
    expect(Math.abs((componentsBox?.x ?? 0) - (triggersBox?.x ?? 0))).toBeLessThan(20);

    const gridMetrics = await grid.evaluate((node) => ({
      clientWidth: node.clientWidth,
      scrollWidth: node.scrollWidth,
    }));
    expect(gridMetrics.scrollWidth).toBeLessThanOrEqual(gridMetrics.clientWidth + 1);
  });
});
