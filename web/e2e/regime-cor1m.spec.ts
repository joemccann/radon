import { test, expect } from "@playwright/test";

const CRI_MOCK = {
  scan_time: "2026-03-10T10:30:00",
  market_open: false,
  date: "2026-03-10",
  vix: 22.4,
  vvix: 104.8,
  spy: 572.1,
  vix_5d_roc: 6.2,
  vvix_vix_ratio: 4.68,
  realized_vol: 14.3,
  cor1m: 28.97,
  cor1m_5d_change: 3.97,
  spx_100d_ma: 579.0,
  spx_distance_pct: -1.19,
  spy_closes: Array.from({ length: 22 }, (_, i) => 550 + i),
  cri: { score: 29, level: "ELEVATED", components: { vix: 7, vvix: 5, correlation: 10, momentum: 7 } },
  crash_trigger: {
    triggered: false,
    conditions: { spx_below_100d_ma: true, realized_vol_gt_25: false, cor1m_gt_60: false },
  },
  cta: { exposure_pct: 69.9, forced_reduction_pct: 30.1, est_selling_bn: 120.4, realized_vol: 14.3 },
  menthorq_cta: null,
  history: [],
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

test.describe("/regime page — COR1M implied correlation", () => {
  test("shows a COR1M strip cell with the current value and 5d change subline", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    const cor1mCell = page.locator('[data-testid="strip-cor1m"]');
    await cor1mCell.waitFor({ timeout: 10_000 });

    await expect(cor1mCell).toContainText("COR1M");
    await expect(cor1mCell.locator(".regime-strip-value")).toHaveText("28.97");
    await expect(cor1mCell.locator(".regime-strip-sub")).toHaveText("5d chg: +3.97 pts");
  });

  test("references the COR1M threshold in the crash trigger section", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    await page.locator('[data-testid="strip-vix"]').waitFor({ timeout: 10_000 });
    await expect(page.locator(".regime-triggers")).toContainText("COR1M > 60");
  });
});
