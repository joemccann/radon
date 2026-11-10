import path from "path";
import { mkdir, readFile, rm, writeFile } from "fs/promises";
import { test, expect } from "@playwright/test";

const DATA_DIR = path.join(process.cwd(), "..", "data");
const CACHE_PATH = path.join(DATA_DIR, "cri.json");
const SCHEDULED_DIR = path.join(DATA_DIR, "cri_scheduled");
const TEST_SCHEDULED_PATH = path.join(SCHEDULED_DIR, "cri-2099-12-31T23-59.json");
const CACHE_BACKUP_PATH = `${CACHE_PATH}.rvol-backup`;
const SCHEDULED_BACKUP_PATH = `${TEST_SCHEDULED_PATH}.rvol-backup`;

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

function makeHistory(length: number, withRvol: boolean) {
  return Array.from({ length }, (_, index) => ({
    date: `2026-02-${String(index + 1).padStart(2, "0")}`,
    vix: 18 + index * 0.4,
    vvix: 90 + index,
    spy: 610 - index,
    cor1m: 25 + index * 0.5,
    realized_vol: withRvol ? Number((11 + index * 0.3).toFixed(2)) : null,
    spx_vs_ma_pct: Number((-1.2 + index * 0.05).toFixed(2)),
    vix_5d_roc: Number((1.5 + index * 0.2).toFixed(1)),
  }));
}

const LEGACY_CACHE = {
  scan_time: "2026-03-11T16:05:00-04:00",
  market_open: false,
  date: "2026-03-11",
  vix: 21.7,
  vvix: 101.4,
  spy: 575.3,
  vix_5d_roc: 4.5,
  vvix_vix_ratio: 4.67,
  realized_vol: 16.7,
  cor1m: 32.4,
  cor1m_5d_change: 2.8,
  spx_100d_ma: 580.1,
  spx_distance_pct: -0.83,
  spy_closes: Array.from({ length: 21 }, (_, i) => 555 + i),
  cri: { score: 31, level: "ELEVATED", components: { vix: 8, vvix: 6, correlation: 10, momentum: 7 } },
  crash_trigger: {
    triggered: false,
    conditions: { spx_below_100d_ma: true, realized_vol_gt_25: false, cor1m_gt_60: false },
    values: { realized_vol: 16.7, cor1m: 32.4 },
  },
  cta: { exposure_pct: 59.9, forced_reduction_pct: 40.1, est_selling_bn: 160.4, realized_vol: 16.7 },
  menthorq_cta: null,
  history: makeHistory(20, true),
};

const STALE_SCHEDULED_CACHE = {
  ...LEGACY_CACHE,
  scan_time: "2026-03-11T16:00:00-04:00",
  realized_vol: 9.5,
  history: makeHistory(10, false),
};

async function backupIfPresent(filePath: string, backupPath: string) {
  try {
    const content = await readFile(filePath);
    await writeFile(backupPath, content);
  } catch {
    // no-op
  }
}

async function restoreBackup(filePath: string, backupPath: string) {
  try {
    const content = await readFile(backupPath);
    await writeFile(filePath, content);
    await rm(backupPath, { force: true });
  } catch {
    await rm(filePath, { force: true });
  }
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

test.describe("/regime page — RVOL history live route cache selection", () => {
  test.beforeEach(async ({ page }) => {
    await mkdir(SCHEDULED_DIR, { recursive: true });
    await backupIfPresent(CACHE_PATH, CACHE_BACKUP_PATH);
    await backupIfPresent(TEST_SCHEDULED_PATH, SCHEDULED_BACKUP_PATH);

    await writeFile(CACHE_PATH, JSON.stringify(LEGACY_CACHE, null, 2));
    await writeFile(TEST_SCHEDULED_PATH, JSON.stringify(STALE_SCHEDULED_CACHE, null, 2));

    await setupNonRegimeMocks(page);
  });

  test.afterEach(async () => {
    await restoreBackup(CACHE_PATH, CACHE_BACKUP_PATH);
    await restoreBackup(TEST_SCHEDULED_PATH, SCHEDULED_BACKUP_PATH);
  });

  test("prefers the richer cache so the RVOL/COR1M chart renders 20 historical RVOL points", async ({ page }) => {
    await page.goto("/regime");

    const charts = page.locator('[data-testid="cri-history-chart"]');
    await expect(charts).toHaveCount(2);

    const rvolChart = charts.nth(1);
    await expect(rvolChart.locator("svg")).toBeVisible();
    await expect(rvolChart.locator("svg .dot-realized_vol")).toHaveCount(20);
    await expect(page.locator('[data-testid="strip-rvol"] .regime-strip-value')).toHaveText("16.70%");
  });
});
