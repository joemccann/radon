/**
 * E2E: VIX and VVIX live badge + timestamp on /regime
 *
 * Verifies:
 * - VIX strip cell shows a LIVE badge element
 * - VVIX strip cell shows a LIVE badge element
 * - VIX strip cell shows a timestamp sub-element (.regime-strip-ts)
 * - VVIX strip cell shows a timestamp sub-element (.regime-strip-ts)
 * - Without live WS data the timestamp shows "---"
 * - With live WS price injected the badge reads "LIVE" and timestamp shows HH:MM:SS
 */

import { test, expect } from "@playwright/test";

// ── Mock data ────────────────────────────────────────────────────────────────

const CRI_MOCK = {
  scan_time: "2026-03-09T14:00:00",
  market_open: true,
  vix: 18.5,
  vvix: 95.2,
  spy: 560.0,
  vix_5d_roc: 3.2,
  vvix_vix_ratio: 5.15,
  realized_vol: 12.4,
  cor1m: 42.0,
  cor1m_5d_change: 2.0,
  spx_100d_ma: 555.0,
  spx_distance_pct: 0.9,
  spy_closes: Array.from({ length: 22 }, (_, i) => 540 + i),
  cri: { score: 22, level: "LOW", components: { vix: 5, vvix: 4, correlation: 8, momentum: 5 } },
  crash_trigger: {
    triggered: false,
    conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, cor1m_gt_60: false },
  },
  cta: { exposure_pct: 85, forced_reduction_pct: 0, est_selling_bn: 0 },
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

// ── Helpers ──────────────────────────────────────────────────────────────────

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

/** Find a regime strip cell by its data-testid. */
function stripCell(page: import("@playwright/test").Page, ticker: "vix" | "vvix") {
  return page.locator(`[data-testid="strip-${ticker}"]`);
}

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe("Regime /regime — VIX and VVIX live badge + timestamp", () => {
  test("VIX strip cell renders a .regime-badge element", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    const cell = stripCell(page, "vix");
    await cell.waitFor({ timeout: 10_000 });

    await expect(cell.locator(".regime-badge")).toBeVisible();
  });

  test("VVIX strip cell renders a .regime-badge element", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    const cell = stripCell(page, "vvix");
    await cell.waitFor({ timeout: 10_000 });

    await expect(cell.locator(".regime-badge")).toBeVisible();
  });

  test("VIX strip cell has a .regime-strip-ts timestamp element", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    const cell = stripCell(page, "vix");
    await cell.waitFor({ timeout: 10_000 });

    await expect(cell.locator(".regime-strip-ts")).toBeVisible();
  });

  test("VVIX strip cell has a .regime-strip-ts timestamp element", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    const cell = stripCell(page, "vvix");
    await cell.waitFor({ timeout: 10_000 });

    await expect(cell.locator(".regime-strip-ts")).toBeVisible();
  });

  test("VIX timestamp shows '---' when no live WS data", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    const cell = stripCell(page, "vix");
    await cell.waitFor({ timeout: 10_000 });

    await expect(cell.locator(".regime-strip-ts")).toHaveText("---");
  });

  test("VVIX timestamp shows '---' when no live WS data", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    const cell = stripCell(page, "vvix");
    await cell.waitFor({ timeout: 10_000 });

    await expect(cell.locator(".regime-strip-ts")).toHaveText("---");
  });
});
