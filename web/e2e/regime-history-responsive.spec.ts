import { test, expect } from "@playwright/test";

const CRI_MOCK = {
  scan_time: "2026-03-11T10:28:24",
  market_open: false,
  date: "2026-03-10",
  vix: 24.79,
  vvix: 125.29,
  spy: 676.39,
  vix_5d_roc: -0.56,
  vvix_vix_ratio: 5.05,
  realized_vol: 12.49,
  cor1m: 29.78,
  cor1m_previous_close: 28.97,
  cor1m_5d_change: 7.42,
  spx_100d_ma: 682.19,
  spx_distance_pct: -0.85,
  spy_closes: Array.from({ length: 40 }, (_, index) => 650 + index),
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
    date: `2026-02-${String(index + 1).padStart(2, "0")}`,
    vix: 18 + index * 0.35,
    vvix: 100 + index * 1.5,
    spy: 650 + index,
    realized_vol: 11.5 + index * 0.08,
    cor1m: 12 + index * 0.9,
    spx_vs_ma_pct: -1.1 + index * 0.08,
    vix_5d_roc: index * 0.3,
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

test.describe("/regime page — responsive history chart stack", () => {
  test("stacks the charts full width on wide screens", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 1400 });
    await setupMocks(page);
    await page.goto("/regime");

    const charts = page.locator('[data-testid="cri-history-chart"]');
    await expect(charts.nth(1)).toBeVisible();

    const [firstBox, secondBox] = await Promise.all([
      charts.nth(0).boundingBox(),
      charts.nth(1).boundingBox(),
    ]);

    expect(firstBox).not.toBeNull();
    expect(secondBox).not.toBeNull();
    expect((secondBox?.y ?? 0)).toBeGreaterThan((firstBox?.y ?? 0) + (firstBox?.height ?? 0) - 20);
    expect(Math.abs((firstBox?.x ?? 0) - (secondBox?.x ?? 0))).toBeLessThan(20);
    expect(Math.abs((firstBox?.width ?? 0) - (secondBox?.width ?? 0))).toBeLessThan(24);
  });

  test("stacks the charts vertically on narrow screens", async ({ page }) => {
    await page.setViewportSize({ width: 820, height: 1600 });
    await setupMocks(page);
    await page.goto("/regime");

    const charts = page.locator('[data-testid="cri-history-chart"]');
    await expect(charts.nth(1)).toBeVisible();

    const [firstBox, secondBox] = await Promise.all([
      charts.nth(0).boundingBox(),
      charts.nth(1).boundingBox(),
    ]);

    expect(firstBox).not.toBeNull();
    expect(secondBox).not.toBeNull();
    expect((secondBox?.y ?? 0)).toBeGreaterThan((firstBox?.y ?? 0) + (firstBox?.height ?? 0) - 20);
    expect(Math.abs((firstBox?.x ?? 0) - (secondBox?.x ?? 0))).toBeLessThan(20);
  });
});
