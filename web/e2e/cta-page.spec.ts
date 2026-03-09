/**
 * E2E: /cta page — vol-targeting model + sortable CTA tables
 *
 * Verifies:
 *  1. /cta page loads and renders the vol-targeting model
 *  2. MenthorQ CTA tables appear below the vol-targeting model
 *  3. CTA nav item is present in the sidebar
 *  4. Sortable table column headers are clickable
 *  5. /regime page no longer contains CTA Exposure Model
 *  6. /regime page shows CRI history chart (D3 SVG)
 */

import { test, expect } from "@playwright/test";

// ── Mock data ────────────────────────────────────────────────────────────────

const CRI_MOCK = {
  scan_time: "2026-03-09T16:30:00",
  market_open: false,
  date: "2026-03-09",
  vix: 29.49,
  vvix: 121.27,
  spy: 677.69,
  vix_5d_roc: 18.9,
  vvix_vix_ratio: 4.11,
  realized_vol: 11.72,
  avg_sector_correlation: 0.38,
  corr_5d_change: 0.01,
  spx_100d_ma: 682.05,
  spx_distance_pct: -0.64,
  spy_closes: Array.from({ length: 22 }, (_, i) => 660 + i),
  cri: { score: 24, level: "LOW", components: { vix: 6, vvix: 5, correlation: 7, momentum: 6 } },
  cta: { exposure_pct: 95, forced_reduction_pct: 0, est_selling_bn: 1.2, realized_vol: 11.72 },
  menthorq_cta: null,
  crash_trigger: {
    triggered: false,
    conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, avg_corr_gt_60: false },
  },
  history: [
    { date: "2026-03-03", vix: 22.1, vvix: 105.3, spy: 660.2, spx_vs_ma_pct: -1.2, vix_5d_roc: 5.1 },
    { date: "2026-03-04", vix: 24.5, vvix: 110.1, spy: 658.7, spx_vs_ma_pct: -1.5, vix_5d_roc: 8.3 },
    { date: "2026-03-05", vix: 26.0, vvix: 114.2, spy: 655.0, spx_vs_ma_pct: -2.0, vix_5d_roc: 12.1 },
    { date: "2026-03-06", vix: 27.8, vvix: 118.5, spy: 662.3, spx_vs_ma_pct: -1.4, vix_5d_roc: 15.4 },
    { date: "2026-03-07", vix: 29.49, vvix: 121.27, spy: 677.69, spx_vs_ma_pct: -0.64, vix_5d_roc: 18.9 },
  ],
};

const CTA_MOCK = {
  date: "2026-03-09",
  fetched_at: "2026-03-09T16:45:00Z",
  tables: {
    main: [
      { underlying: "SPX", position_today: 0.45, position_yesterday: 0.42, position_1m_ago: 0.60, percentile_1m: 13, percentile_3m: 18, percentile_1y: 22, z_score_3m: -1.56 },
      { underlying: "NQ", position_today: 0.38, position_yesterday: 0.40, position_1m_ago: 0.55, percentile_1m: 20, percentile_3m: 25, percentile_1y: 30, z_score_3m: -1.20 },
    ],
    index: [
      { underlying: "ES", position_today: 0.50, position_yesterday: 0.48, position_1m_ago: 0.65, percentile_1m: 15, percentile_3m: 20, percentile_1y: 28, z_score_3m: -1.40 },
    ],
    commodity: [],
    currency: [],
  },
};

const PORTFOLIO_EMPTY = {
  bankroll: 100_000, positions: [], account_summary: {}, exposure: {}, violations: [],
};
const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(), open_orders: [], executed_orders: [], open_count: 0, executed_count: 0,
};

async function setupMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CRI_MOCK) }),
  );
  await page.route("**/api/menthorq/cta", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CTA_MOCK) }),
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
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
}

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe("/cta page", () => {
  test("CTA nav item exists in sidebar", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/cta");
    // The nav should contain a CTA link
    const nav = page.locator("nav, aside").first();
    await expect(nav.locator("a[href='/cta'], a[href*='cta']")).toBeVisible({ timeout: 10_000 });
  });

  test("vol-targeting model renders with data-testid", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/cta");
    const model = page.locator('[data-testid="vol-targeting-model"]');
    await model.waitFor({ timeout: 10_000 });
    await expect(model).toBeVisible();
  });

  test("vol-targeting model shows exposure percentage", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/cta");
    const model = page.locator('[data-testid="vol-targeting-model"]');
    await model.waitFor({ timeout: 10_000 });
    // CRI_MOCK.cta.exposure_pct = 95
    await expect(model).toContainText("95");
  });

  test("MenthorQ CTA tables render below vol-targeting model", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/cta");
    const model = page.locator('[data-testid="vol-targeting-model"]');
    await model.waitFor({ timeout: 10_000 });

    // Tables container must exist
    const table = page.locator('[data-testid="sortable-cta-table"]').first();
    await expect(table).toBeVisible({ timeout: 10_000 });

    // Vol model must appear above (lower Y coordinate) than the table
    const modelBox = await model.boundingBox();
    const tableBox = await table.boundingBox();
    expect(modelBox).toBeTruthy();
    expect(tableBox).toBeTruthy();
    expect(modelBox!.y).toBeLessThan(tableBox!.y);
  });

  test("sortable table column headers are clickable", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/cta");

    const table = page.locator('[data-testid="sortable-cta-table"]').first();
    await table.waitFor({ timeout: 10_000 });

    // Click a numeric column header (TODAY) — should not throw
    const todayHeader = table.locator("th").filter({ hasText: "TODAY" });
    await expect(todayHeader).toBeVisible();
    await todayHeader.click();
    // After click, the clicked header should show a sort indicator (▲ or ▼)
    await expect(todayHeader).toContainText(/▲|▼/);
  });

  test("clicking column header again reverses sort direction", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/cta");

    const table = page.locator('[data-testid="sortable-cta-table"]').first();
    await table.waitFor({ timeout: 10_000 });

    const todayHeader = table.locator("th").filter({ hasText: "TODAY" });
    await todayHeader.click();
    const firstSort = await table.locator("th").filter({ hasText: /▲|▼/ }).innerText();

    await todayHeader.click();
    const secondSort = await table.locator("th").filter({ hasText: /▲|▼/ }).innerText();

    // Direction should have changed
    expect(firstSort).not.toEqual(secondSort);
  });
});

test.describe("/regime page — CTA section removed, D3 history chart present", () => {
  test("regime page does NOT show CTA Exposure Model section", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");
    await page.locator('[data-testid="strip-vix"]').waitFor({ timeout: 10_000 });

    await expect(page.locator("text=CTA EXPOSURE MODEL")).not.toBeVisible();
  });

  test("regime page shows CRI history chart SVG", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    const chart = page.locator('[data-testid="cri-history-chart"]');
    await chart.waitFor({ timeout: 10_000 });
    await expect(chart).toBeVisible();

    // Should contain an SVG
    await expect(chart.locator("svg")).toBeVisible();
  });

  test("CRI history chart renders VIX data points", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    const chart = page.locator('[data-testid="cri-history-chart"]');
    await chart.waitFor({ timeout: 10_000 });

    // D3 should render circle or path elements for VIX data
    const circles = chart.locator("circle");
    const paths = chart.locator("path");
    const hasContent = (await circles.count()) > 0 || (await paths.count()) > 0;
    expect(hasContent).toBe(true);
  });
});
