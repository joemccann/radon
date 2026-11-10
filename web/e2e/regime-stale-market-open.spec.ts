/**
 * E2E: /regime page — stale data should NOT show MARKET CLOSED during market hours
 *
 * Regression target:
 *   When cri_scan.py fails (IB timeouts) and the API serves yesterday's data
 *   with market_open: false, the page incorrectly showed "MARKET CLOSED" banner
 *   during live trading hours. The API now overrides market_open based on
 *   real-time clock when serving stale (previous-day) data.
 */

import { test, expect } from "@playwright/test";

/** Mock CRI data: yesterday's scan with market_open: false,
 *  but the API override should set market_open: true during market hours. */
const CRI_MOCK_STALE_OPEN = {
  scan_time: "2026-03-10T16:30:00",
  market_open: true,  // API overrides this when date !== today
  date: "2026-03-10",
  vix: 29.49,
  vvix: 121.27,
  spy: 677.69,
  vix_5d_roc: 18.9,
  vvix_vix_ratio: 4.11,
  realized_vol: 11.72,
  cor1m: 38.12,
  cor1m_5d_change: 1.0,
  spx_100d_ma: 682.05,
  spx_distance_pct: -0.64,
  spy_closes: Array.from({ length: 22 }, (_, i) => 660 + i),
  cri: { score: 24, level: "LOW", components: { vix: 6, vvix: 5, correlation: 7, momentum: 6 } },
  crash_trigger: {
    triggered: false,
    conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, cor1m_gt_60: false },
  },
  cta: { exposure_pct: 95, forced_reduction_pct: 0, est_selling_bn: 0 },
  menthorq_cta: null,
  history: [],
};

async function setupMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/regime", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(CRI_MOCK_STALE_OPEN),
    }),
  );
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ bankroll: 100_000, positions: [], account_summary: {}, exposure: {}, violations: [] }),
    }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ last_sync: new Date().toISOString(), open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 }),
    }),
  );
  await page.route("**/api/prices", (route) => route.abort());
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: true }),
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

test.describe("Regime /regime — stale data during market hours", () => {
  test("does NOT show MARKET CLOSED banner when market_open is true (API override)", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    // Wait for the regime strip to render
    await page.locator('[data-testid="strip-vix"]').waitFor({ timeout: 10_000 });

    // The MARKET CLOSED banner should NOT be visible
    const closedBanner = page.locator('[data-testid="market-closed-indicator"]');
    await expect(closedBanner).not.toBeVisible();
  });

  test("regime strip values render (not blank) with stale data", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    // Wait for the regime strip to render
    const vixCell = page.locator('[data-testid="strip-vix"]');
    await vixCell.waitFor({ timeout: 10_000 });

    // VIX value should be present and non-zero (from stale data or live WS)
    const vixValue = vixCell.locator(".regime-strip-value");
    await expect(vixValue).toBeVisible();
    const vixText = await vixValue.textContent();
    expect(vixText).not.toBe("0.00");
    expect(vixText).not.toBe("---");
  });
});
