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

import { reconcileCtaTables } from "../lib/ctaPercentiles";

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
    { date: "2026-03-03", vix: 22.1, vvix: 105.3, spy: 660.2, realized_vol: 12.5, spx_vs_ma_pct: -1.2, vix_5d_roc: 5.1 },
    { date: "2026-03-04", vix: 24.5, vvix: 110.1, spy: 658.7, realized_vol: 12.8, spx_vs_ma_pct: -1.5, vix_5d_roc: 8.3 },
    { date: "2026-03-05", vix: 26.0, vvix: 114.2, spy: 655.0, realized_vol: 13.1, spx_vs_ma_pct: -2.0, vix_5d_roc: 12.1 },
    { date: "2026-03-06", vix: 27.8, vvix: 118.5, spy: 662.3, realized_vol: 12.2, spx_vs_ma_pct: -1.4, vix_5d_roc: 15.4 },
    { date: "2026-03-07", vix: 29.49, vvix: 121.27, spy: 677.69, realized_vol: 12.0, spx_vs_ma_pct: -0.64, vix_5d_roc: 18.9 },
    { date: "2026-03-08", vix: 28.0, vvix: 120.0, spy: 675.0, realized_vol: 13.0, spx_vs_ma_pct: -0.5, vix_5d_roc: 16.0 },
  ],
};

const CTA_MOCK = {
  date: "2026-03-09",
  fetched_at: "2026-03-09T16:45:00Z",
  source: "menthorq_s3_vision",
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
  cache_meta: {
    last_refresh: "2026-03-09T16:45:00Z",
    age_seconds: 120,
    is_stale: false,
    stale_threshold_seconds: null,
    target_date: "2026-03-09",
    latest_cache_date: "2026-03-09",
    stale_reason: "fresh",
  },
  sync_status: {
    service: "cta-sync",
    status: "success",
    trigger: "launchd",
    target_date: "2026-03-09",
    started_at: "2026-03-09T16:40:00Z",
    finished_at: "2026-03-09T16:45:00Z",
    duration_ms: 30_000,
    attempt_count: 1,
    cache_path: "data/menthorq_cache/cta_2026-03-09.json",
    error_type: null,
    error_excerpt: null,
    artifact_log_path: null,
  },
};

const CTA_STALE_MOCK = {
  ...CTA_MOCK,
  date: "2026-03-08",
  fetched_at: "2026-03-08T16:45:00Z",
  cache_meta: {
    last_refresh: "2026-03-08T16:45:00Z",
    age_seconds: 86_400,
    is_stale: true,
    stale_threshold_seconds: null,
    target_date: "2026-03-09",
    latest_cache_date: "2026-03-08",
    stale_reason: "behind_target",
  },
  sync_status: {
    service: "cta-sync",
    status: "error",
    trigger: "launchd",
    target_date: "2026-03-09",
    started_at: "2026-03-09T16:40:00Z",
    finished_at: "2026-03-09T16:45:00Z",
    duration_ms: 30_000,
    attempt_count: 2,
    cache_path: null,
    error_type: "auth_rejected",
    error_excerpt: "Your username or password was incorrect",
    artifact_log_path: "logs/cta-sync-artifacts/cta-sync-20260309T164500.log",
  },
};

const CTA_CURRENCY_DECIMAL_MOCK = {
  ...CTA_MOCK,
  tables: {
    ...CTA_MOCK.tables,
    currency: [
      { underlying: "Euro", position_today: -0.3, position_yesterday: -0.34, position_1m_ago: 0.35, percentile_1m: 0.33, percentile_3m: 0.13, percentile_1y: 0.09, z_score_3m: -1.13 },
      { underlying: "Canadian Dollar", position_today: 1.36, position_yesterday: 1.2, position_1m_ago: 1.03, percentile_1m: 0.57, percentile_3m: 0.84, percentile_1y: 0.65, z_score_3m: 1.01 },
    ],
  },
};

// The 2026-08-25 payload exactly as Turso stored it: the main table's SPX and
// NQ percentiles were rounded to 0 by the vision extractor while the index
// table holds the same two rows on the fractional scale. Piped through the
// same reconciliation the CTA route applies, so the page sees what the server
// would really serve for that session.
const CTA_ROUNDED_PERCENTILE_MOCK = {
  ...CTA_MOCK,
  date: "2026-08-25",
  tables: reconcileCtaTables({
    main: [
      { underlying: "E-Mini S&P 500 Index", position_today: 3.66, position_yesterday: 3.85, position_1m_ago: 1.69, percentile_1m: 0, percentile_3m: 0, percentile_1y: 0, z_score_3m: 1.48 },
      { underlying: "CME Nasdaq 100 Index", position_today: 2.52, position_yesterday: 2.69, position_1m_ago: 1.59, percentile_1m: 0, percentile_3m: 0, percentile_1y: 0, z_score_3m: 0.26 },
      { underlying: "Brent", position_today: -1.33, position_yesterday: -0.83, position_1m_ago: 0.99, percentile_1m: 5, percentile_3m: 2, percentile_1y: 5, z_score_3m: -1.89 },
    ],
    index: [
      { underlying: "E-Mini S&P 500 Index", position_today: 3.66, position_yesterday: 3.85, position_1m_ago: 1.69, percentile_1m: 0.43, percentile_3m: 0.81, percentile_1y: 0.88, z_score_3m: 1.48 },
      { underlying: "CME Nasdaq 100 Index", position_today: 2.52, position_yesterday: 2.69, position_1m_ago: 1.59, percentile_1m: 0.38, percentile_3m: 0.52, percentile_1y: 0.68, z_score_3m: 0.26 },
    ],
    commodity: [],
    currency: [],
  }),
};

const PORTFOLIO_EMPTY = {
  bankroll: 100_000, positions: [], account_summary: {}, exposure: {}, violations: [],
};
const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(), open_orders: [], executed_orders: [], open_count: 0, executed_count: 0,
};

async function setupMocks(
  page: import("@playwright/test").Page,
  overrides?: { cta?: Record<string, unknown> },
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CRI_MOCK) }),
  );
  await page.route("**/api/menthorq/cta", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(overrides?.cta ?? CTA_MOCK),
    }),
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

  test("vol-targeting model shows inputs stamp, implied cut, and day change", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/cta");
    const model = page.locator('[data-testid="vol-targeting-model"]');
    await model.waitFor({ timeout: 10_000 });
    await expect(page.locator('[data-testid="cta-model-inputs"]')).toContainText("RVOL");
    await expect(page.locator('[data-testid="cta-model-inputs"]')).toContainText("AUM");
    await expect(model).toContainText("Implied cut vs AUM");
    await expect(page.locator('[data-testid="cta-implied-cut"]')).toContainText("$1.2B");
    // prior session rvol 13.0 → est ~$92.3B; current 1.2 → large negative day change
    await expect(page.locator('[data-testid="cta-day-change"]')).toContainText("$");
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
    // After click, the clicked header becomes the active sort column. The shared
    // SortTh renders a chevron icon + sets aria-sort (asc/desc); assert the
    // semantic attribute rather than the legacy ▲/▼ glyph.
    await expect(todayHeader).toHaveAttribute("aria-sort", /ascending|descending/);
  });

  test("clicking column header again reverses sort direction", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/cta");

    const table = page.locator('[data-testid="sortable-cta-table"]').first();
    await table.waitFor({ timeout: 10_000 });

    const todayHeader = table.locator("th").filter({ hasText: "TODAY" });
    await todayHeader.click();
    const firstSort = await todayHeader.getAttribute("aria-sort");

    await todayHeader.click();
    const secondSort = await todayHeader.getAttribute("aria-sort");

    // Direction should have flipped (ascending <-> descending).
    expect(firstSort).not.toEqual(secondSort);
  });

  test("shows a stale CTA banner when the cache is behind the latest closed trading day", async ({ page }) => {
    await setupMocks(page, { cta: CTA_STALE_MOCK });
    await page.goto("/cta");

    const banner = page.locator('[data-testid="cta-stale-banner"]');
    await expect(banner).toBeVisible({ timeout: 10_000 });
    await expect(banner).toContainText("CTA CACHE STALE");
    await expect(banner).toContainText("2026-03-08");
    await expect(banner).toContainText("2026-03-09");
    await expect(banner).toContainText("Your username or password was incorrect");
  });

  test("renders currency analysis when currency percentiles arrive as decimals", async ({ page }) => {
    await setupMocks(page, { cta: CTA_CURRENCY_DECIMAL_MOCK });
    await page.goto("/cta");

    const ctaPage = page.locator('[data-testid="cta-page"]');
    await expect(ctaPage).toContainText("Euro at 13th pctile short while Canadian Dollar sits 84th pctile long.");
    await expect(ctaPage).toContainText("FX DISPERSION");
    await expect(ctaPage).toContainText("84");
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

    const chart = page.locator('[data-testid="regime-history-chart-vix-vvix"] [data-testid="cri-history-chart"]');
    await chart.waitFor({ timeout: 10_000 });
    await expect(chart).toBeVisible();

    // Should contain an SVG
    await expect(chart.locator("svg")).toBeVisible();
  });

  test("CRI history chart renders VIX data points", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    const chart = page.locator('[data-testid="regime-history-chart-vix-vvix"] [data-testid="cri-history-chart"]');
    await chart.waitFor({ timeout: 10_000 });

    // D3 should render circle or path elements for VIX data
    const circles = chart.locator("circle");
    const paths = chart.locator("path");
    const hasContent = (await circles.count()) > 0 || (await paths.count()) > 0;
    expect(hasContent).toBe(true);
  });
});

test.describe("/cta rounded-percentile repair", () => {
  test("a +1.48 z-score long reads as high percentile, not 0th MAX SHORT", async ({ page }) => {
    await setupMocks(page, { cta: CTA_ROUNDED_PERCENTILE_MOCK });
    await page.goto("/cta");

    const table = page.locator('[data-testid="sortable-cta-table"]').first();
    await table.waitFor({ timeout: 10_000 });

    const spxRow = table.locator("tbody tr", { hasText: "E-Mini S&P 500 Index" }).first();
    await expect(spxRow).toContainText("81");
    await expect(spxRow).toContainText("1.48");

    // The verdict banner reads off the same percentile — a heavy long must
    // never publish as MAX SHORT.
    await expect(page.locator("body")).not.toContainText("MAX SHORT");
    await expect(page.locator("body")).not.toContainText("0th pctile");
  });
});
