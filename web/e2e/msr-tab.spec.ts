import { test, expect } from "@playwright/test";

// ~300 weekday sessions ending in early November 2026. NDX starts later and
// RUT skips every fourth session, so the per-index chart paths must drop nulls.
type MsrRow = { date: string; spx: number | null; ndx: number | null; rut: number | null };

function buildSeries(): MsrRow[] {
  const series: MsrRow[] = [];
  const day = new Date(Date.UTC(2025, 8, 15));
  let i = 0;
  while (series.length < 300) {
    const dow = day.getUTCDay();
    if (dow !== 0 && dow !== 6) {
      series.push({
        date: day.toISOString().slice(0, 10),
        spx: 0.01 + (i % 9) * 0.008,
        ndx: i < 40 ? null : 0.012 + (i % 7) * 0.009,
        rut: i % 4 === 0 ? null : 0.015 + (i % 5) * 0.01,
      });
      i += 1;
    }
    day.setUTCDate(day.getUTCDate() + 1);
  }
  return series;
}

const SERIES = buildSeries();
const LAST_DATE = SERIES[SERIES.length - 1].date;

const MSR_MOCK = {
  scan_time: new Date().toISOString(),
  start_date: "2007-11-08",
  source_last_modified: {
    spx: "Sat, 07 Nov 2026 02:11:00 GMT",
    vix: "Sat, 07 Nov 2026 02:21:00 GMT",
    vxn: "Fri, 06 Nov 2026 22:31:00 GMT",
    rvx: "Fri, 06 Nov 2026 22:31:00 GMT",
  },
  close_sources: { SPX: ["cboe"], NDX: ["ib"], RUT: ["ib", "yahoo"] },
  indexes: {
    SPX: {
      iv_symbol: "VIX",
      current: { date: LAST_DATE, msr: 0.011162, move_pct: 0.314534, close: 7376.86, iv_prior: 28.18, rank: 3736 },
      week_max: { date: "2026-11-05", msr: 0.262231, move_pct: -4.355658, iv_prior: 16.61, rank: 1 },
      stats: { count: 4779, max: 0.262231, max_date: "2026-11-05", p50: 0.029424, p90: 0.081531, p99: 0.149252 },
      top: [
        { date: "2026-11-05", msr: 0.262231, move_pct: -4.355658 },
        { date: "2008-09-29", msr: 0.253577, move_pct: -8.809251 },
        { date: "2018-02-05", msr: 0.236737, move_pct: -4.097924 },
        { date: "2025-04-03", msr: 0.224991, move_pct: -4.840139 },
        { date: "2020-06-11", msr: 0.213798, move_pct: -5.894406 },
      ],
    },
    NDX: {
      iv_symbol: "VXN",
      current: { date: LAST_DATE, msr: 0.033333, move_pct: 1.134, close: 25500.12, iv_prior: 34.02, rank: 410 },
      week_max: { date: "2026-11-05", msr: 0.201, move_pct: -5.12, iv_prior: 25.47, rank: 2 },
      stats: { count: 4313, max: 0.244, max_date: "2020-03-16", p50: 0.03, p90: 0.08, p99: 0.15 },
      top: [{ date: "2020-03-16", msr: 0.244, move_pct: -12.32 }],
    },
    RUT: {
      iv_symbol: "RVX",
      current: { date: LAST_DATE, msr: 0.030719, move_pct: 0.887169, close: 2754.1054, iv_prior: 28.88, rank: 835 },
      week_max: { date: "2026-11-05", msr: 0.225762, move_pct: -4.447506, iv_prior: 19.7, rank: 3 },
      stats: { count: 1492, max: 0.263688, max_date: "2023-11-14", p50: 0.035047, p90: 0.08421, p99: 0.146056 },
      top: [
        { date: "2023-11-14", msr: 0.263688, move_pct: 5.442515 },
        { date: "2025-04-03", msr: 0.258181, move_pct: -6.591361 },
        { date: "2026-11-05", msr: 0.225762, move_pct: -4.447506 },
      ],
    },
  },
  series: SERIES,
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

async function setupMocks(
  page: import("@playwright/test").Page,
  msrPayload: Record<string, unknown> = MSR_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/msr", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(msrPayload) }),
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
}

test.describe("/regime/msr — move surprise ratio tab", () => {
  test("activates the MSR tab and renders the SPX strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/msr");

    await expect(page.locator('.regime-rail__item[data-tab="msr"]')).toHaveClass(/active/);

    const last = page.locator('[data-testid="msr-last-value"]');
    await last.waitFor({ timeout: 10_000 });
    await expect(last).toHaveText("0.011");
    await expect(page.locator('[data-testid="msr-index-SPX"]')).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator('[data-testid="msr-strip-rank"]')).toContainText("#3,736 of 4,779");
    await expect(page.locator('[data-testid="msr-week-max"]')).toContainText("0.262");
    await expect(page.locator('[data-testid="msr-week-max"]')).toContainText("#1");
    await expect(page.locator('[data-testid="msr-strip-iv"]')).toContainText("VIX PRIOR");
    await expect(page.locator('[data-testid="msr-strip-source"]')).toContainText("07 Nov 2026");
    await expect(page.locator('[data-testid="msr-freshness-rail"]')).toBeVisible();
    await expect(page.locator('[data-testid="msr-strip-asof"]')).toContainText(LAST_DATE);
  });

  test("index chips switch the strip, chart title and top table", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/msr");

    const section = page.locator('[data-testid="msr-chart-section"]');
    await section.waitFor({ timeout: 10_000 });
    await expect(section).toContainText("SPX MOVE SURPRISE RATIO");
    await expect(page.locator('[data-testid="msr-top-table"] tbody tr')).toHaveCount(5);

    await page.locator('[data-testid="msr-index-RUT"]').click();
    await expect(page.locator('[data-testid="msr-index-RUT"]')).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator('[data-testid="msr-index-SPX"]')).toHaveAttribute("aria-pressed", "false");
    await expect(page.locator('[data-testid="msr-last-value"]')).toHaveText("0.031");
    await expect(page.locator('[data-testid="msr-strip-rank"]')).toContainText("#835 of 1,492");
    await expect(page.locator('[data-testid="msr-strip-iv"]')).toContainText("RVX PRIOR");
    await expect(section).toContainText("RUT MOVE SURPRISE RATIO");

    const table = page.locator('[data-testid="msr-top-table"]');
    await expect(table.locator("tbody tr")).toHaveCount(3);
    await expect(table).toContainText("2023-11-14");
    await expect(table).not.toContainText("2008-09-29");

    await page.locator('[data-testid="msr-index-NDX"]').click();
    await expect(section).toContainText("NDX MOVE SURPRISE RATIO");
    await expect(page.locator('[data-testid="msr-strip-iv"]')).toContainText("VXN PRIOR");
  });

  test("draws a NaN-free series path with the brush and range chips", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/msr");

    const section = page.locator('[data-testid="msr-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    for (const index of ["SPX", "NDX", "RUT"]) {
      await page.locator(`[data-testid="msr-index-${index}"]`).click();
      await expect(section).toContainText(`${index} MOVE SURPRISE RATIO`);
      const paths = section.locator("svg path[stroke]");
      await expect(paths.first()).toBeVisible();
      const ds = await paths.evaluateAll((els) => els.map((el) => el.getAttribute("d") ?? ""));
      expect(ds.some((d) => d.length > 0)).toBe(true);
      for (const d of ds) expect(d).not.toContain("NaN");
    }

    await expect(section.locator('[data-testid="msr-brush"]')).toBeVisible();
    await expect(section.locator('[data-testid="msr-range-chips"]')).toBeVisible();

    await page.locator('[data-testid="msr-index-SPX"]').click();
    await page.screenshot({ path: "test-results/msr-tab.png", fullPage: true });
    await section.screenshot({ path: "test-results/msr-tab-chart.png" });
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, { missing: true, scan_time: null, start_date: null, indexes: null, series: [] });
    await page.goto("/regime/msr");

    await expect(page.getByText("No MSR data yet")).toBeVisible({ timeout: 10_000 });
  });
});
