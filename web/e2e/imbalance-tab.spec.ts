import { test, expect } from "@playwright/test";

const series = Array.from({ length: 80 }, (_, i) => {
  const imbalance = i % 7 === 0 ? -(i * 1000) : i * 1200;
  return {
    date: new Date(Date.UTC(2026, 6, 16 + i)).toISOString().slice(0, 10),
    ask: 10_000_000 + i, bid: 10_000_000 + i - imbalance, batd: imbalance,
    imbalance, ema10: imbalance * 0.4, spx_close: 7400 + i,
  };
});
series[series.length - 1] = {
  ...series.at(-1)!,
  date: "2026-11-06",
  ask: 13_124_065, bid: 12_215_551, batd: 908_514,
  imbalance: 908_514, ema10: 200_000, spx_close: 7376.86,
};

const IMBALANCE_MOCK = {
  scan_time: new Date().toISOString(),
  data_date: series.at(-1)!.date,
  current: series.at(-1),
  series,
  source: {
    provider: "tradingview",
    units: "trade_count",
    units_verified: true,
    history_complete: false,
    source_finality: "unverified",
    universe_verified: false,
  },
};

const PORTFOLIO_EMPTY = {
  bankroll: 100_000, positions: [], account_summary: {}, exposure: {}, violations: [],
};
const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(), open_orders: [], executed_orders: [],
  open_count: 0, executed_count: 0,
};

async function setupMocks(
  page: import("@playwright/test").Page,
  payload: Record<string, unknown> = IMBALANCE_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/imbalance", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) }),
  );
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_EMPTY) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
}

test.describe("ASK-BID trade-count imbalance (labeled fixture, not production)", () => {
  test("renders signed histogram, SPX pane, and trade-count provenance on desktop", async ({ page }, testInfo) => {
    await setupMocks(page);
    await page.goto("/regime/imbalance");
    await expect(page.getByTestId("imbalance-net")).toHaveText("+908,514");
    await expect(page.getByTestId("imbalance-spx")).toHaveText("7376.86");
    const section = page.getByTestId("imbalance-chart-section");
    await expect(section.getByTestId("cri-history-chart")).toHaveCount(2);
    await expect(page.getByTestId("imbalance-source")).toContainText("not share volume");
    await page.evaluate(async () => { await document.fonts.ready; });
    await page.getByTestId("imbalance-panel").scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("imbalance-desktop.png"), fullPage: true });
    await section.scrollIntoViewIfNeeded();
    await section.screenshot({ path: testInfo.outputPath("imbalance-desktop-charts.png") });
    await page.getByText("Sources and units", { exact: true }).click();
    await expect(page.getByTestId("imbalance-methodology")).toContainText("parity check only");
    const before = await section.locator(".history-bar").count();
    await section.getByRole("button", { name: "1M", exact: true }).click();
    await expect(section.getByRole("button", { name: "1M", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect.poll(() => section.locator(".history-bar").count()).toBeLessThan(before);
  });

  test("keeps units visible on mobile without overflow", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await setupMocks(page);
    await page.goto("/regime/imbalance");
    await expect(page.getByTestId("imbalance-net")).toHaveText("+908,514");
    await expect(page.getByTestId("imbalance-source")).toContainText("Universe, session coverage, and tick-rule classification are unverified");
    await page.evaluate(async () => { await document.fonts.ready; });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("imbalance-mobile.png"), fullPage: true });
  });

  test("shows missing measurements rather than zeros", async ({ page }) => {
    await setupMocks(page, { missing: true, current: null, series: [], scan_time: null, data_date: null });
    await page.goto("/regime/imbalance");
    await expect(page.getByText("No aligned ASK-BID trade-count session yet")).toBeVisible();
    await expect(page.getByTestId("imbalance-net")).toHaveCount(0);
  });
});
