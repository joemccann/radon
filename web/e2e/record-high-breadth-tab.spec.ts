import { test, expect } from "@playwright/test";
const series = Array.from({ length: 240 }, (_, i) => ({
  date: new Date(Date.UTC(2026, 0, i + 1)).toISOString().slice(0, 10),
  spx_close: 6800 + i * 3, above_200_pct: 65 - i / 15,
  new_highs: i % 30, new_lows: i % 45,
  net_high_low_per_500: (i % 30 - i % 45) / 5,
}));
series[series.length - 1] = { ...series.at(-1)!, spx_close: 7650.5, above_200_pct: 49.5, new_highs: 5, new_lows: 29, net_high_low_per_500: -4.8 };
const BREADTH_MOCK = { scan_time: new Date().toISOString(), data_date: series.at(-1)!.date,
  current: series.at(-1), series,
  source: { provider: "tradingview", history_complete: false, denominator: 500, denominator_verified: false, source_finality: "unverified", parity: { status: "mismatch", reference_date: "2026-09-18", reference: { spx_close: 7650.5, above_200_pct: 52, net_high_low_pct: -4.4 }, tradingview: { spx_close: 7650.5, above_200_pct: 49.5, net_high_low_per_500: -4.8 } } } };
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
  payload: Record<string, unknown> = BREADTH_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/record-high-breadth", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) }),
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

async function expectVisibleAxisLabels(section: import("@playwright/test").Locator) {
  await expect(section.locator("svg")).toHaveCount(3);
  for (let pane = 0; pane < 3; pane++) {
    await expect.poll(() => section.locator("svg").nth(pane).locator(".tick text").count()).toBeGreaterThan(0);
  }
  await expect.poll(async () => section.locator("svg").evaluateAll((svgs) => {
    const clipped: string[] = [];
    for (const [pane, svg] of svgs.entries()) {
      const bounds = svg.getBoundingClientRect();
      for (const label of svg.querySelectorAll(".tick text")) {
        const rect = label.getBoundingClientRect();
        if (rect.left < bounds.left - 1 || rect.right > bounds.right + 1) {
          clipped.push(`Pane ${pane + 1}: ${label.textContent}`);
        }
      }
    }
    return clipped;
  })).toEqual([]);
}

test.describe("S&P 500 breadth source variant", () => {
  test("renders three aligned panes and shared ranges on desktop", async ({ page }, testInfo) => {
    await setupMocks(page); await page.goto("/regime/record-high-breadth");
    await expect(page.getByTestId("record-high-breadth-spx")).toHaveText("7650.50");
    await expect(page.getByTestId("record-high-breadth-above")).toHaveText("49.50%");
    await expect(page.getByTestId("record-high-breadth-net")).toHaveText("-4.80%");
    const section = page.getByTestId("record-high-breadth-chart-section");
    await expect(section.getByTestId("cri-history-chart")).toHaveCount(3);
    await expect(section.locator("svg")).toHaveCount(3);
    await expect(page.getByTestId("record-high-breadth-source")).toContainText("actual issue denominator is unverified");
    await page.evaluate(async () => { await document.fonts.ready; });
    await expectVisibleAxisLabels(section);
    await page.getByTestId("record-high-breadth-panel").scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("record-high-breadth-desktop.png"), fullPage: true });
    for (let index = 0; index < 3; index++) {
      const pane = section.getByTestId("cri-history-chart").nth(index);
      await pane.scrollIntoViewIfNeeded();
      await pane.screenshot({ path: testInfo.outputPath(`record-high-breadth-desktop-pane-${index + 1}.png`) });
    }
    await page.getByText("Sources and reference parity", { exact: true }).click();
    await expect(page.getByTestId("record-high-breadth-methodology")).toContainText("Source parity: mismatch");
    await expect(page.getByTestId("record-high-breadth-methodology")).toContainText("52.00%");
    const before = await section.locator(".history-bar").count();
    await section.getByRole("button", { name: "1M", exact: true }).click();
    await expect(section.getByRole("button", { name: "1M", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect.poll(() => section.locator(".history-bar").count()).toBeLessThan(before);
  });
  test("keeps source limitations visible on mobile without overflow", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 }); await setupMocks(page);
    await page.goto("/regime/record-high-breadth");
    await expect(page.getByTestId("record-high-breadth-above")).toHaveText("49.50%");
    await expect(page.getByTestId("record-high-breadth-source")).toContainText("1973 and 1999 are unverified");
    const section = page.getByTestId("record-high-breadth-chart-section");
    await page.evaluate(async () => { await document.fonts.ready; });
    await expectVisibleAxisLabels(section);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("record-high-breadth-mobile.png"), fullPage: true });
  });
  test("shows missing measurements rather than zeros", async ({ page }) => {
    await setupMocks(page, { missing: true, current: null, series: [], scan_time: null, data_date: null });
    await page.goto("/regime/record-high-breadth");
    await expect(page.getByText("No aligned S&P 500 breadth session yet")).toBeVisible();
    await expect(page.getByTestId("record-high-breadth-net")).toHaveCount(0);
  });
});
