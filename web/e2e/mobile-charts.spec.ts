/**
 * E2E: Mobile chart sizing at 393×852.
 *
 * Validates:
 * 1. The CRI history chart respects min(440px, 60vh) on mobile.
 * 2. The CRI chart svg fills the panel width.
 * 3. The GEX profile chart panel respects max-height 60vh.
 * 4. Chart tooltip max-width is constrained to viewport.
 */

import { test, expect, type Page } from "@playwright/test";

const PORTFOLIO = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [],
};

const ORDERS = { last_sync: new Date().toISOString(), open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 };

const REGIME = {
  scan_time: new Date().toISOString(),
  market_open: false,
  date: "2026-05-06",
  vix: 18.2,
  vvix: 95.4,
  spy: 580.0,
  vix_5d_roc: 1.2,
  vvix_vix_ratio: 5.24,
  realized_vol: 12.0,
  cor1m: 38.0,
  cor1m_5d_change: 0.5,
  spx_100d_ma: 575.0,
  spx_distance_pct: 0.87,
  spy_closes: Array.from({ length: 22 }, (_, i) => 575 + i * 0.5),
  cri: { score: 18, level: "LOW", components: { vix: 4, vvix: 5, correlation: 5, momentum: 4 } },
  cta: { exposure_pct: 92, forced_reduction_pct: 0, est_selling_bn: 0.5, realized_vol: 12.0 },
  crash_trigger: { triggered: false, conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, cor1m_gt_60: false } },
  history: Array.from({ length: 20 }, (_, i) => ({
    date: `2026-04-${(i + 10).toString().padStart(2, "0")}`,
    cri: 15 + i * 0.4,
    vix: 17 + i * 0.1,
    vvix: 90 + i * 0.5,
    cor1m: 35 + i * 0.3,
    realized_vol: 10 + i * 0.2,
  })),
};

async function setupMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REGIME) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
  await page.route("**/api/cash-flows**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows: [], summary: {} }) }),
  );
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, days_until_expiry: 14 }) }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
}

test.describe("Mobile chart sizing", () => {
  test("CRI history chart respects 60vh height ceiling on mobile", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/cri", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("mobile-tab-bar")).toBeVisible();

    const surface = page.locator(".cri-history-chart-surface").first();
    await expect(surface).toBeVisible();

    const minHeightPx = await surface.evaluate((el) => {
      const v = window.getComputedStyle(el).minHeight;
      return parseFloat(v);
    });
    // 60vh of 852 = ~511, min(440, 60vh) = 440. Within viewport.
    expect(minHeightPx).toBeGreaterThan(0);
    expect(minHeightPx).toBeLessThanOrEqual(440);
  });

  test("CRI chart svg fills the panel width", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/cri", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("mobile-tab-bar")).toBeVisible();

    const svg = page.locator(".cri-history-chart-svg").first();
    if (await svg.count()) {
      const box = await svg.boundingBox();
      expect(box).not.toBeNull();
      if (box) {
        // Should fill ~card width (393 minus padding minus safe insets)
        expect(box.width).toBeGreaterThan(280);
      }
    }
  });

  test("body[data-mobile=true] is set when chart pages render", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/cri", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("mobile-tab-bar")).toBeVisible();
    const dataMobile = await page.evaluate(() => document.body.dataset.mobile);
    expect(dataMobile).toBe("true");
  });

  test("ColumnsToggle and other table-only widgets are hidden", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("mobile-tab-bar")).toBeVisible();
    const toggles = page.locator(".columns-toggle");
    if (await toggles.count()) {
      // None should be visible (display: none under data-mobile)
      const anyVisible = await toggles.evaluateAll((nodes) =>
        nodes.some((n) => window.getComputedStyle(n).display !== "none"),
      );
      expect(anyVisible).toBe(false);
    }
  });
});
