import { expect, test } from "@playwright/test";

const CRI_MOCK = {
  scan_time: "2026-03-11T11:52:24",
  market_open: false,
  date: "2026-03-10",
  vix: 24.88,
  vvix: 124.71,
  spy: 675.31,
  vix_5d_roc: -0.2,
  vvix_vix_ratio: 5.01,
  realized_vol: 12.51,
  cor1m: 29.47,
  cor1m_previous_close: 28.97,
  cor1m_5d_change: 7.03,
  spx_100d_ma: 682.19,
  spx_distance_pct: -1.01,
  spy_closes: Array.from({ length: 40 }, (_, index) => 650 + index),
  cri: {
    score: 26,
    level: "ELEVATED",
    components: {
      vix: 6.9,
      vvix: 11.8,
      correlation: 4.5,
      momentum: 2.5,
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
    exposure_pct: 69.9,
    forced_reduction_pct: 30.1,
    est_selling_bn: 120.4,
  },
  history: [
    { date: "2026-02-10", vix: 17.8, vvix: 100.8, spy: 692.1, realized_vol: 15.3, cor1m: 12.6, spx_vs_ma_pct: 1.9, vix_5d_roc: -1.2 },
    { date: "2026-02-11", vix: 20.9, vvix: 108.6, spy: 691.2, realized_vol: 15.0, cor1m: 12.4, spx_vs_ma_pct: 1.7, vix_5d_roc: -0.4 },
    { date: "2026-02-12", vix: 22.0, vvix: 112.0, spy: 688.8, realized_vol: 14.4, cor1m: 12.8, spx_vs_ma_pct: 1.4, vix_5d_roc: 0.3 },
    { date: "2026-02-13", vix: 21.4, vvix: 111.1, spy: 690.5, realized_vol: 13.8, cor1m: 14.1, spx_vs_ma_pct: 1.6, vix_5d_roc: 0.9 },
    { date: "2026-02-14", vix: 20.7, vvix: 109.4, spy: 693.0, realized_vol: 13.4, cor1m: 15.2, spx_vs_ma_pct: 2.0, vix_5d_roc: 0.1 },
    { date: "2026-02-18", vix: 19.2, vvix: 106.0, spy: 695.3, realized_vol: 12.6, cor1m: 16.3, spx_vs_ma_pct: 2.3, vix_5d_roc: -0.6 },
    { date: "2026-02-19", vix: 20.1, vvix: 107.8, spy: 694.0, realized_vol: 12.1, cor1m: 16.0, spx_vs_ma_pct: 2.0, vix_5d_roc: -0.2 },
    { date: "2026-02-20", vix: 21.2, vvix: 109.5, spy: 692.6, realized_vol: 11.7, cor1m: 15.4, spx_vs_ma_pct: 1.7, vix_5d_roc: 0.4 },
    { date: "2026-02-21", vix: 22.1, vvix: 111.0, spy: 690.9, realized_vol: 11.9, cor1m: 16.8, spx_vs_ma_pct: 1.4, vix_5d_roc: 0.8 },
    { date: "2026-02-24", vix: 18.5, vvix: 104.7, spy: 698.1, realized_vol: 12.4, cor1m: 15.9, spx_vs_ma_pct: 2.6, vix_5d_roc: -1.1 },
    { date: "2026-02-25", vix: 18.7, vvix: 105.1, spy: 697.8, realized_vol: 12.0, cor1m: 14.8, spx_vs_ma_pct: 2.5, vix_5d_roc: -0.9 },
    { date: "2026-02-26", vix: 20.7, vvix: 109.2, spy: 695.2, realized_vol: 12.3, cor1m: 13.6, spx_vs_ma_pct: 2.1, vix_5d_roc: 0.4 },
    { date: "2026-02-27", vix: 21.0, vvix: 110.4, spy: 694.0, realized_vol: 13.0, cor1m: 15.0, spx_vs_ma_pct: 1.9, vix_5d_roc: 0.8 },
    { date: "2026-02-28", vix: 21.3, vvix: 111.2, spy: 693.5, realized_vol: 13.1, cor1m: 16.5, spx_vs_ma_pct: 1.8, vix_5d_roc: 0.9 },
    { date: "2026-03-03", vix: 22.2, vvix: 113.0, spy: 691.9, realized_vol: 13.1, cor1m: 17.1, spx_vs_ma_pct: 1.5, vix_5d_roc: 1.2 },
    { date: "2026-03-04", vix: 23.1, vvix: 116.0, spy: 688.8, realized_vol: 13.2, cor1m: 21.2, spx_vs_ma_pct: 0.9, vix_5d_roc: 2.0 },
    { date: "2026-03-05", vix: 29.4, vvix: 140.0, spy: 680.3, realized_vol: 13.3, cor1m: 18.5, spx_vs_ma_pct: -0.6, vix_5d_roc: 5.1 },
    { date: "2026-03-06", vix: 27.1, vvix: 132.4, spy: 683.2, realized_vol: 13.4, cor1m: 28.7, spx_vs_ma_pct: -0.2, vix_5d_roc: 4.0 },
    { date: "2026-03-07", vix: 24.9, vvix: 124.7, spy: 675.3, realized_vol: 11.7, cor1m: 27.9, spx_vs_ma_pct: -1.0, vix_5d_roc: 1.6 },
    { date: "2026-03-10", vix: 24.8, vvix: 124.7, spy: 675.3, realized_vol: 12.5, cor1m: 29.5, spx_vs_ma_pct: -1.0, vix_5d_roc: -0.2 },
  ],
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

test.describe("/regime page — RVOL/COR1M relationship view", () => {
  test("renders spread, quadrant, and normalized divergence panels without removing the raw history charts", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1800 });
    await setupMocks(page);
    await page.goto("/regime");

    await expect(page.locator('[data-testid="regime-history-grid"] [data-testid="cri-history-chart"]')).toHaveCount(2);

    const relationshipView = page.locator('[data-testid="regime-relationship-view"]');
    await expect(relationshipView).toBeVisible();
    await expect(relationshipView).toHaveAttribute("data-chart-family", "Analytical Time Series");
    await expect(relationshipView).toHaveAttribute("data-chart-renderer", "svg");

    const spreadCard = page.locator('[data-testid="regime-spread-card"]');
    await expect(spreadCard).toContainText("CORRELATION RISK PREMIUM");
    await expect(spreadCard.locator('[data-testid="regime-current-spread"]')).toContainText("+16.96 pts");
    await expect(spreadCard).toContainText("IMPLIED PREMIUM");

    const quadrantCard = page.locator('[data-testid="regime-quadrant-card"]');
    await expect(quadrantCard).toContainText("REGIME QUADRANTS");
    await expect(quadrantCard.locator('[data-testid="regime-current-quadrant"]')).toHaveText("FRAGILE CALM");

    const zScoreCard = page.locator('[data-testid="regime-zscore-card"]');
    await expect(zScoreCard).toContainText("NORMALIZED DIVERGENCE");
    await expect(zScoreCard.locator('[data-testid="regime-current-zgap"]')).toContainText("σ");
    await expect(zScoreCard.locator(".chart-legend")).toContainText("RVOL z-score");
    await expect(zScoreCard.locator(".chart-legend")).toContainText("COR1M z-score");
  });

  test("shows tooltip definitions for all four relationship states", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1800 });
    await setupMocks(page);
    await page.goto("/regime");

    const stateKey = page.locator('[data-testid="regime-state-key"]');
    await expect(stateKey).toBeVisible();
    await expect(stateKey.locator('[data-testid^="regime-state-item-"]')).toHaveCount(4);

    const fragileCalmTrigger = page.locator('[data-testid="regime-state-tooltip-trigger-fragile-calm"]');
    await fragileCalmTrigger.hover();

    const fragileCalmBubble = page.locator('[data-testid="regime-state-tooltip-bubble-fragile-calm"]');
    await expect(fragileCalmBubble).toBeVisible();
    await expect(fragileCalmBubble).toContainText("RVOL is below its 20-session mean");
    await expect(fragileCalmBubble).toContainText("COR1M is at or above its 20-session mean");
  });

  test("uses the classified quadrant tone for the latest scatter marker", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1800 });
    await setupMocks(page);
    await page.goto("/regime");

    const quadrantCard = page.locator('[data-testid="regime-quadrant-card"]');
    await expect(quadrantCard.locator('[data-testid="regime-current-quadrant"]')).toHaveText("FRAGILE CALM");

    const latestMarker = quadrantCard.locator('[data-testid="regime-quadrant-chart"] .regime-relationship-marker');
    await expect(latestMarker).toHaveAttribute("fill", "var(--dislocation)");
    await expect(latestMarker).toHaveAttribute("stroke", "var(--dislocation)");
  });

  test("shows a tooltip for the normalized divergence panel", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1800 });
    await setupMocks(page);
    await page.goto("/regime");

    const trigger = page.locator('[data-testid="regime-zscore-tooltip-trigger"]');
    await trigger.hover();

    const bubble = page.locator('[data-testid="regime-zscore-tooltip-bubble"]');
    await expect(bubble).toBeVisible();
    await expect(bubble).toContainText("z-score");
    await expect(bubble).toContainText("COR1M");
    await expect(bubble).toContainText("RVOL");
    await expect(bubble).toContainText("reduce gross exposure");
    await expect(bubble).toContainText("keep or add index hedges");
    await expect(bubble).toContainText("harvest crash hedges");
    await expect(bubble).toContainText("single-name");
  });

  test("shows a hover readout for the normalized divergence chart", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1800 });
    await setupMocks(page);
    await page.goto("/regime");

    const chartShell = page.locator('[data-testid="regime-zscore-chart-shell"]');
    await expect(chartShell).toBeVisible();

    const shellBox = await chartShell.boundingBox();
    expect(shellBox).not.toBeNull();
    if (!shellBox) {
      throw new Error("Expected z-score chart shell to have a bounding box");
    }

    await chartShell.dispatchEvent("mousemove", {
      bubbles: true,
      clientX: shellBox.x + shellBox.width * 0.74,
      clientY: shellBox.y + shellBox.height * 0.5,
    });

    const tooltip = page.locator('[data-testid="regime-zscore-hover-tooltip"]');
    await expect(tooltip).toBeVisible();
    await expect(tooltip.locator('[data-testid="regime-zscore-hover-date"]')).toContainText("Mar");
    await expect(tooltip).toContainText("RVOL z-score");
    await expect(tooltip).toContainText("COR1M z-score");
    await expect(tooltip).toContainText("Divergence");
  });
});
