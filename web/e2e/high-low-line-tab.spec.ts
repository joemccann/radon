import { test, expect, type Page } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";
import { installClearRealtimeFixtures } from "./clear-realtime-fixtures";

// Synthetic source-shaped observations isolate display behavior from ingestion.
const daily = Array.from({ length: 1800 }, (_, i) => ({
  date: new Date(Date.UTC(2026, 9, 20) - (1799 - i) * 86400000).toISOString().slice(0, 10),
  period_start: "2026-10-19", period_end: "2026-10-23", period_complete: true,
  highs: 40 + i % 20, lows: 12, net: 28 + i % 20,
  cumulative: 200000 + i * 20 + Math.sin(i / 25) * 1000,
  rsi14: i < 14 ? null : 50 + Math.sin(i / 25) * 20,
  stoch_rsi14: i < 27 ? null : (Math.sin(i / 25) + 1) / 2,
  sar: i === 0 ? null : 200500 + i * 20 + Math.sin(i / 25) * 1000,
  sar_direction: "falling", reset_to_zero: false, source_fractional: false,
}));
const weekly = daily.filter((_, i) => i % 5 === 0).map(row => ({ ...row, net: row.net * 5 }));
const payload = {
  missing: false, degraded: false, scan_time: "2026-10-21T00:30:00Z", source_last_modified: null,
  data_date: daily.at(-1)!.date, expected_session_date: daily.at(-1)!.date,
  count: daily.length, weekly_count: weekly.length, series: daily, weekly_series: weekly,
  current: { ...daily.at(-1)!, cumulative: 250000.1256, net: 40, zone: "neutral" },
  weekly_current: { ...weekly.at(-1)!, cumulative: 250000.1256, period_complete: false, zone: "neutral" },
  source_hash: "a".repeat(64), source: { name: "StockCharts DecisionPoint", url: "https://stockcharts.com", symbols: { highs: "!NEWHINYC", lows: "!NEWLONYC" }, fractional_policy: "preserved", fractional_observations: 275, history_start: "2003-01-02", history_end: daily.at(-1)!.date, excluded_non_sessions: [] },
  baseline: { date: "2003-01-02", value: 0, convention: "before_first_observation" },
  settings: { rsi_period: 14, stoch_period: 14, smoothing: "Wilder", sar_start: 1, sar_increment: 1, sar_max: 1, daily_ohlc: "close_only", weekly_ohlc: "daily_cumulative_range", default_interval: "weekly" },
};
async function setup(page: Page, theme: "light" | "dark", data: object = payload) {
  await installClearFixtures(page);
  await installClearRealtimeFixtures(page);
  await page.clock.setFixedTime(new Date("2026-10-21T02:00:00Z"));
  await page.addInitScript(value => localStorage.setItem("theme", value), theme);
  await page.route("**/api/high-low-line", route => route.fulfill({ status: 200, json: data }));
}
for (const theme of ["light", "dark"] as const) for (const width of [1440, 393]) {
  test(`${theme} ${width}px: common-stock charts, intervals, shared brush and finite ticks`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await setup(page, theme);
    await page.goto("/regime/high-low");
    await expect(page.getByTestId("high-low-line-strip-cumulative")).toContainText("250,000.1256");
    await expect(page.getByRole("button", { name: "Weekly", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("high-low-line-week-progress")).toBeVisible();
    await expect(page.getByTestId("high-low-line-strip-asof")).toContainText(payload.data_date);
    await expect(page.getByTestId("high-low-line-freshness-rail")).toContainText("Next sample");
    await expect(page.getByTestId("high-low-line-sample")).toContainText("2026-10-21 00:30:00 UTC");
    await expect(page.getByTestId("high-low-line-cumulative-chart").locator("circle.dot-sar").first()).toBeVisible();
    await expect(page.getByTestId("high-low-line-cumulative-chart").locator("path[stroke]").first()).toBeVisible();
    await expect(page.getByTestId("high-low-line-stoch-chart").locator(".reference-level")).toHaveCount(3);
    await expect(page.getByTestId("high-low-line-range-chips-5y")).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "Daily", exact: true }).click();
    await expect(page.getByTestId("high-low-line-week-progress")).toHaveCount(0);
    await expect(page.getByTestId("high-low-line-strip-net")).toContainText("+40");
    await page.getByRole("button", { name: "1Y", exact: true }).click();
    const chartSliders = page.getByRole("slider", { name: /Inspect .* history/ });
    expect(await chartSliders.nth(0).getAttribute("aria-valuemax")).toBe(await chartSliders.nth(1).getAttribute("aria-valuemax"));
    await page.getByRole("slider", { name: "Start of visible range" }).focus();
    await page.keyboard.press("ArrowLeft");
    await expect(page.getByTestId("high-low-line-range-chips-custom")).toBeVisible();
    expect(await chartSliders.nth(0).getAttribute("aria-valuemax")).toBe(await chartSliders.nth(1).getAttribute("aria-valuemax"));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const finite = await page.locator('[data-testid="high-low-line-chart-section"] svg').evaluateAll(svgs => svgs.every(svg => !/NaN|Infinity/.test(svg.outerHTML)));
    expect(finite).toBe(true);
    const ticks = page.getByTestId("high-low-line-stoch-chart").getByTestId("chart-x-axis").locator(".tick text");
    expect(await ticks.count()).toBeLessThanOrEqual(width < 640 ? 3 : 12);
    await page.screenshot({ path: `test-results/high-low-${theme}-${width}.png`, fullPage: true });
  });
}
test("missing common-stock observations show the measurement condition", async ({ page }) => {
  await installClearFixtures(page);
  await installClearRealtimeFixtures(page);
  await page.goto("/regime/high-low");
  await expect(page.getByText("Awaiting NYSE common-stock High-Low Line", { exact: true })).toBeVisible();
  await expect(page.getByTestId("high-low-line-chart-section")).toHaveCount(0);
  await expect(page.locator(".toast-error")).toHaveCount(0);
});
