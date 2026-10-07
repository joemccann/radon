import { expect, test } from "@playwright/test";

const CRI_MOCK = {
  scan_time: "2026-03-12T16:30:00",
  market_open: false,
  date: "2026-03-12",
  vix: 29.49,
  vvix: 121.27,
  spy: 677.69,
  vix_5d_roc: 18.9,
  vvix_vix_ratio: 4.11,
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
  history: [],
};

const CTA_STALE_MOCK = {
  date: "2026-03-11",
  fetched_at: "2026-03-12T01:45:00Z",
  tables: {
    main: [
      { underlying: "SPX", position_today: 0.45, position_yesterday: 0.42, position_1m_ago: 0.60, percentile_1m: 13, percentile_3m: 18, percentile_1y: 22, z_score_3m: -1.56 },
    ],
    index: [],
    commodity: [],
    currency: [],
  },
  cache_meta: {
    is_stale: true,
    expected_date: "2026-03-12",
    latest_available_date: "2026-03-11",
    stale_reason: "missing_latest_closed_session",
  },
  sync_health: {
    state: "degraded",
    target_date: "2026-03-12",
    last_error: {
      type: "auth_rejected",
      message: "Your username or password was incorrect",
    },
  },
};

async function setupMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CRI_MOCK) }),
  );
  await page.route("**/api/menthorq/cta", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CTA_STALE_MOCK) }),
  );
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ bankroll: 100_000, positions: [], account_summary: {}, exposure: {}, violations: [] }) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ last_sync: new Date().toISOString(), open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 }) }),
  );
  await page.route("**/api/prices", (route) => route.abort());
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: false }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
}

test("CTA page shows a stale-data warning while still rendering the last available table", async ({ page }) => {
  await setupMocks(page);
  await page.goto("/cta");

  const banner = page.locator('[data-testid="cta-stale-banner"]');
  await expect(banner).toBeVisible({ timeout: 10000 });
  await expect(banner).toContainText("CTA positioning is stale");
  await expect(banner).toContainText("Expected 2026-03-12");
  await expect(banner).toContainText("Latest available 2026-03-11");
  await expect(page.getByRole("alert").filter({ hasText: "Your username or password was incorrect" })).toBeVisible();

  await expect(page.locator('[data-testid="sortable-cta-table"]').first()).toBeVisible();
});
