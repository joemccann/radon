import { test, expect } from "@playwright/test";

const CRI_MOCK = {
  scan_time: "2026-03-24T06:42:00Z",
  market_open: true,
  date: "2026-03-24",
  vix: 27.63,
  vvix: 122.82,
  spy: 677.69,
  vix_5d_roc: 5.66,
  vvix_vix_ratio: 4.44,
  realized_vol: 11.72,
  cor1m: 38.0,
  cor1m_5d_change: 1.0,
  spx_100d_ma: 682.05,
  spx_distance_pct: -0.64,
  spy_closes: Array.from({ length: 22 }, (_, i) => 660 + i),
  cri: { score: 24, level: "LOW", components: { vix: 6, vvix: 5, correlation: 7, momentum: 6 } },
  cta: { exposure_pct: 95, forced_reduction_pct: 0, est_selling_bn: 1.2, realized_vol: 11.72 },
  menthorq_cta: null,
  crash_trigger: {
    triggered: false,
    conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, cor1m_gt_60: false },
  },
  history: [
    { date: "2026-03-20", vix: 22.1, vvix: 105.3, spy: 660.2, spx_vs_ma_pct: -1.2, vix_5d_roc: 5.1 },
    { date: "2026-03-21", vix: 24.5, vvix: 110.1, spy: 658.7, spx_vs_ma_pct: -1.5, vix_5d_roc: 8.3 },
    { date: "2026-03-22", vix: 26.0, vvix: 114.2, spy: 655.0, spx_vs_ma_pct: -2.0, vix_5d_roc: 12.1 },
    { date: "2026-03-23", vix: 27.8, vvix: 118.5, spy: 662.3, spx_vs_ma_pct: -1.4, vix_5d_roc: 15.4 },
    { date: "2026-03-24", vix: 27.63, vvix: 122.82, spy: 677.69, spx_vs_ma_pct: -0.64, vix_5d_roc: 18.9 },
  ],
};

const VCG_MOCK = {
  scan_time: "2026-03-24T06:42:00Z",
  market_open: true,
  credit_proxy: "HYG",
  signal: {
    vcg: 3.15,
    vcg_adj: 3.15,
    residual: 0.006132,
    beta1_vvix: -0.013941,
    beta2_vix: -0.023025,
    alpha: 0,
    vix: 26.15,
    vvix: 122.82,
    credit_price: 79.44,
    credit_5d_return_pct: -0.01,
    ro: 0,
    edr: 1,
    tier: 3,
    bounce: 0,
    vvix_severity: "extreme",
    sign_ok: true,
    sign_suppressed: false,
    pi_panic: 0,
    regime: "DIVERGENCE",
    interpretation: "EDR",
    attribution: {
      vvix_pct: 41,
      vix_pct: 59,
      vvix_component: 0,
      vix_component: 0,
      model_implied: 0,
    },
  },
  history: Array.from({ length: 5 }, (_, index) => ({
    date: `2026-03-${String(index + 20).padStart(2, "0")}`,
    residual: 0.0005 * (index + 1),
    vcg: 1 + index * 0.4,
    vcg_adj: 1 + index * 0.4,
    beta1: -0.013 + index * 0.001,
    beta2: -0.023 + index * 0.001,
    vix: 24 + index,
    vvix: 110 + index * 2,
    credit: 79.5 - index * 0.1,
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
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CRI_MOCK) }),
  );
  await page.route("**/api/vcg", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(VCG_MOCK) }),
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
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  await page.route("**/api/menthorq/cta", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ tables: [] }) }),
  );
}

test.describe("/regime page — VCG EDR badge", () => {
  test("renders the EDR pill with a visible warning background", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    // Retry the click until the route changes — a click landing mid-hydration
    // (while the rail re-renders on its first status payload) can be lost.
    const vcgItem = page.locator('.regime-rail__item[data-tab="vcg"]');
    await expect(async () => {
      await vcgItem.click();
      await expect(page).toHaveURL(/\/regime\/vcg/, { timeout: 2_000 });
    }).toPass({ timeout: 20_000 });

    const edrBadge = page.locator(".section-header .pill", { hasText: "EDR" }).first();
    await expect(edrBadge).toBeVisible({ timeout: 10_000 });

    const backgroundColor = await edrBadge.evaluate((node) => getComputedStyle(node).backgroundColor);
    expect(backgroundColor).not.toBe("rgba(0, 0, 0, 0)");
  });
});
