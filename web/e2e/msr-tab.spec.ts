import { test, expect } from "@playwright/test";
import { MsrFixture } from "./clear-indicator-fixtures";

const MSR_MOCK = MsrFixture();
const LAST_DATE = MSR_MOCK.indexes.SPX.current.date;

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
