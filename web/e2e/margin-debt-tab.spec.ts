import { test, expect } from "@playwright/test";

// 24 finra months ending 2026-05; levels chosen so every month with a
// 12-month lookback carries a YoY value. Current YoY +53.7% exercises the
// >= +50% froth (warning) tone.
function buildSeries() {
  const months: string[] = [];
  for (let y = 2024; y <= 2026; y++) {
    for (let m = 1; m <= 12; m++) {
      const key = `${y}-${String(m).padStart(2, "0")}`;
      if (key >= "2024-06" && key <= "2026-05") months.push(key);
    }
  }
  return months.map((date, i) => {
    const level = 900_000 + i * 22_000;
    const lookback = i - 12;
    const yoy = lookback >= 0 ? (level / (900_000 + lookback * 22_000) - 1) * 100 : null;
    return {
      date,
      level,
      free_credit_cash: 200_000,
      free_credit_margin: 210_000,
      source: "finra",
      level_yoy_pct: date === "2026-05" ? 53.7 : yoy,
      level_display: level,
      net_level: level - 410_000,
      level_real: level,
      level_pct_gdp: 4.6,
      spx_close: 5000 + i * 100,
    };
  });
}

const SERIES = buildSeries();

const MARGIN_DEBT_MOCK = {
  scan_time: new Date().toISOString(),
  source_last_modified: "Tue, 16 Jun 2026 14:52:10 GMT",
  count: SERIES.length,
  splice: { legacy_source: "nyse_legacy", ratio: 1.039, first_finra_month: "1997-01" },
  normalization: { available: true },
  current: { date: "2026-05", level: 1_415_557, level_yoy_pct: 53.7 },
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
  marginPayload: Record<string, unknown> = MARGIN_DEBT_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/margin-debt", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(marginPayload) }),
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

test.describe("/regime/margin — Margin Debt Acceleration tab", () => {
  test("activates the MARGIN tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/margin");

    await expect(page.locator(".ticker-tab", { hasText: "MARGIN" })).toHaveClass(/active/);

    const yoy = page.locator('[data-testid="margin-debt-yoy-value"]');
    await yoy.waitFor({ timeout: 10_000 });
    await expect(yoy).toHaveText("+53.7%");
    // >= +50% growth reads as froth -> var(--warning) (dark theme resolve).
    await expect(yoy).toHaveCSS("color", "rgb(212, 145, 10)");

    await expect(page.locator('[data-testid="margin-debt-strip-level"]')).toContainText("$1,415.6bn");
    await expect(page.locator('[data-testid="margin-debt-strip-month"]')).toContainText("2026-05");
  });

  test("renders the dual-axis chart with both series and the splice footnote", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/margin");

    const section = page.locator('[data-testid="margin-debt-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    // Two drawn series lines (S&P 500 log-left + margin debt right).
    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    expect(await section.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(2);

    await expect(section).toContainText("ratio-adjusted");
    await expect(section.locator('[data-testid="margin-debt-brush"]')).toBeVisible();
  });

  test("switches the right series through the view chips", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/margin");

    const chips = page.locator('[data-testid="margin-debt-view-chips"]');
    await chips.waitFor({ timeout: 10_000 });

    for (const label of ["NET $BN", "% OF GDP", "REAL $BN", "LEVEL $BN", "YOY %"]) {
      await chips.locator("button", { hasText: label }).click();
      await expect(chips.locator("button", { hasText: label })).toHaveClass(/is-active/);
      await expect(
        page.locator('[data-testid="margin-debt-chart-section"] svg path[stroke]').first(),
      ).toBeVisible();
    }

    const smooth = page.locator('[data-testid="margin-debt-smooth-toggle"]');
    await smooth.click();
    await expect(smooth).toHaveClass(/is-active/);
  });

  test("hides CPI/GDP views when normalization is unavailable", async ({ page }) => {
    await setupMocks(page, { ...MARGIN_DEBT_MOCK, normalization: { available: false } });
    await page.goto("/regime/margin");

    const chips = page.locator('[data-testid="margin-debt-view-chips"]');
    await chips.waitFor({ timeout: 10_000 });
    await expect(chips.locator("button", { hasText: "% OF GDP" })).toHaveCount(0);
    await expect(chips.locator("button", { hasText: "REAL $BN" })).toHaveCount(0);
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, {
      missing: true,
      scan_time: null,
      count: 0,
      series: [],
      current: null,
      splice: null,
      normalization: null,
    });
    await page.goto("/regime/margin");

    await expect(page.getByText("No margin debt data yet")).toBeVisible({ timeout: 10_000 });
  });
});
