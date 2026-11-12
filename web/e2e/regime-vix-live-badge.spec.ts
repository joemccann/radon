/**
 * E2E: VIX and VVIX live badge + timestamp on /regime
 *
 * Verifies:
 * - VIX strip cell shows a LIVE badge element
 * - VVIX strip cell shows a LIVE badge element
 * - VIX strip cell shows a timestamp sub-element (.regime-strip-ts)
 * - VVIX strip cell shows a timestamp sub-element (.regime-strip-ts)
 * - Without live data the timestamp is omitted (no "---" placeholder since #1041)
 * - With a live quote the badge reads "LIVE" and the timestamp shows HH:MM:SS
 *
 * /api/index-quote is always mocked: unmocked, the route asks Yahoo for a live
 * VIX and the result depends on the network and the hour.
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

function indexQuote(symbol: string, last: number) {
  return { symbol, last, lastIsCalculated: true, bid: null, ask: null, bidSize: null, askSize: null, volume: null, high: null, low: null, open: null, close: null, week52High: null, week52Low: null };
}

async function setupMocks(page: import("@playwright/test").Page, liveIndexes: Record<string, number> = {}) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/index-quote**", (route) => {
    const symbol = new URL(route.request().url()).searchParams.get("symbol") ?? "";
    const last = liveIndexes[symbol];
    const body = last == null ? { price: null, source: "none" } : { price: indexQuote(symbol, last), source: "yahoo" };
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

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

  test("VIX strip cell shows a LIVE badge and HH:MM:SS timestamp with a live quote", async ({ page }) => {
    await setupMocks(page, { VIX: 18.9 });
    await page.goto("/regime");

    const cell = stripCell(page, "vix");
    await cell.waitFor({ timeout: 10_000 });

    await expect(cell.locator(".regime-badge")).toHaveText("LIVE");
    await expect(cell.locator(".regime-strip-ts")).toHaveText(/^\d{1,2}:\d{2}:\d{2}/);
  });

  test("VVIX strip cell shows a LIVE badge and HH:MM:SS timestamp with a live quote", async ({ page }) => {
    await setupMocks(page, { VVIX: 96.4 });
    await page.goto("/regime");

    const cell = stripCell(page, "vvix");
    await cell.waitFor({ timeout: 10_000 });

    await expect(cell.locator(".regime-badge")).toHaveText("LIVE");
    await expect(cell.locator(".regime-strip-ts")).toHaveText(/^\d{1,2}:\d{2}:\d{2}/);
  });

  test("VIX omits the timestamp and reads DAILY when no live data", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    const cell = stripCell(page, "vix");
    await cell.waitFor({ timeout: 10_000 });

    await expect(cell.locator(".regime-badge")).toHaveText("DAILY");
    await expect(cell.locator(".regime-strip-ts")).toHaveCount(0);
  });

  test("VVIX omits the timestamp and reads DAILY when no live data", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    const cell = stripCell(page, "vvix");
    await cell.waitFor({ timeout: 10_000 });

    await expect(cell.locator(".regime-badge")).toHaveText("DAILY");
    await expect(cell.locator(".regime-strip-ts")).toHaveCount(0);
  });
});
