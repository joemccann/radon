import path from "path";
import { mkdir, rm, writeFile } from "fs/promises";
import { test, expect } from "@playwright/test";

const DATA_DIR = path.join(process.cwd(), "..", "data");
const SCHEDULED_DIR = path.join(DATA_DIR, "cri_scheduled");
const TEST_CACHE_PATH = path.join(SCHEDULED_DIR, "cri-9999-12-31T23-59.json");

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

function todayEt(): string {
  return new Date().toLocaleDateString("sv", { timeZone: "America/New_York" });
}

function buildHistory() {
  return Array.from({ length: 20 }, (_, index) => ({
    date: `2026-02-${String(index + 1).padStart(2, "0")}`,
    vix: 20 + index * 0.4,
    vvix: 92 + index,
    spy: 570 + index * 1.3,
    cor1m: 27 + index * 0.5,
    realized_vol: null,
    spx_vs_ma_pct: -1.8 + index * 0.1,
    vix_5d_roc: index * 0.4,
  }));
}

async function setupNonRegimeMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

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

test.describe("/regime page — RVOL history backfill from cache", () => {
  test.beforeEach(async () => {
    await mkdir(SCHEDULED_DIR, { recursive: true });
    await rm(TEST_CACHE_PATH, { force: true });
  });

  test.afterEach(async () => {
    await rm(TEST_CACHE_PATH, { force: true });
  });

  test("renders twenty RVOL history points when the cache only stores SPY closes", async ({ page }) => {
    const payload = {
      scan_time: new Date().toISOString(),
      market_open: false,
      date: todayEt(),
      vix: 24.9,
      vvix: 112.4,
      spy: 594.2,
      vix_5d_roc: 4.8,
      vvix_vix_ratio: 4.51,
      realized_vol: 11.7,
      cor1m: 29.4,
      cor1m_5d_change: 2.8,
      spx_100d_ma: 600.5,
      spx_distance_pct: -1.05,
      spy_closes: Array.from({ length: 40 }, (_, index) => 545 + index * 1.2),
      cri: { score: 26, level: "ELEVATED", components: { vix: 7, vvix: 6, correlation: 8, momentum: 5 } },
      cta: { realized_vol: 11.7, exposure_pct: 85.5, forced_reduction_pct: 14.5, est_selling_bn: 58.1 },
      menthorq_cta: null,
      crash_trigger: {
        triggered: false,
        conditions: { spx_below_100d_ma: true, realized_vol_gt_25: false, cor1m_gt_60: false },
        values: { realized_vol: 11.7, cor1m: 29.4 },
      },
      history: buildHistory(),
    };

    await writeFile(TEST_CACHE_PATH, JSON.stringify(payload, null, 2));
    await setupNonRegimeMocks(page);
    await page.goto("/regime");

    const charts = page.locator('[data-testid="cri-history-chart"]');
    await expect(charts.nth(1)).toBeVisible();
    await expect(page.locator(".dot-realized_vol")).toHaveCount(20);
    await expect(page.locator(".dot-cor1m")).toHaveCount(20);
  });
});
