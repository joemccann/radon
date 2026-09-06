import { test, expect } from "@playwright/test";
import { CLEAR_PORTFOLIO, installClearFixtures } from "./clear-fixtures";

test.use({ extraHTTPHeaders: { "x-radon-authless-test": process.env.RADON_AUTHLESS_TEST_TOKEN ?? "clear-local-verification-20260905" } });

for (const viewport of [{ name: "desktop", width: 1440, height: 1000 }, { name: "mobile", width: 390, height: 844 }]) {
  test(`Clear ${viewport.name}: single performance request and lazy assistant`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const requests = await installClearFixtures(page);
    await page.goto("/performance", { waitUntil: "domcontentloaded" });
    await expect(page.locator(".performance-chart-shell")).toBeVisible();
    expect(requests.filter((request) => request === "GET /api/performance")).toHaveLength(1);
    await expect(page.getByTestId("chat-launcher-ready")).toBeAttached();
    await page.keyboard.press("Control+j");
    const dialog = page.getByRole("dialog", { name: "Radon chat" });
    await expect(dialog.getByRole("textbox", { name: "Ask Radon" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    expect(requests.some((request) => /POST \/api\/orders\/(place|cancel|modify)/.test(request))).toBe(false);
  });

  test(`Clear overview ${viewport.name}: account, history, risk, positions and research`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    const requests = await installClearFixtures(page);
    await page.goto("/dashboard");
    const overview = page.getByTestId("clear-overview");
    await expect(overview).toBeVisible();
    await expect(page.getByTestId("clear-account-value")).toHaveText("$1,246,820.42");
    const chart = overview.getByRole("slider", { name: "Inspect account value history" });
    await expect(chart).toBeVisible();
    const maximum = Number(await chart.getAttribute("aria-valuemax"));
    expect(maximum).toBeGreaterThan(2);
    await chart.press("Home");
    await expect(chart).toHaveAttribute("aria-valuenow", "0");
    await chart.press("ArrowRight");
    await expect(chart).toHaveAttribute("aria-valuenow", "1");
    await chart.press("End");
    await expect(chart).toHaveAttribute("aria-valuenow", String(maximum));
    const week = overview.getByRole("button", { name: "1W", exact: true });
    await expect(week).toBeEnabled();
    await week.click();
    await expect(week).toHaveAttribute("aria-pressed", "true");
    expect(Number(await chart.getAttribute("aria-valuemax"))).toBeLessThan(maximum);
    await overview.getByRole("button", { name: "All", exact: true }).click();
    await expect(chart).toHaveAttribute("aria-valuemax", String(maximum));
    await expect(overview.getByRole("heading", { name: "Your positions 2" })).toBeVisible();
    await expect(overview.getByRole("link", { name: /AAPL.*100 shares/ })).toHaveAttribute("href", "/AAPL?posId=1");
    if (viewport.name === "mobile") {
      const risk = overview.locator('a[href="#clear-risk-details"]');
      await expect(risk).toBeVisible();
      const riskBox = await risk.boundingBox();
      const positionsBox = await overview.getByRole("heading", { name: "Your positions 2" }).boundingBox();
      expect(riskBox!.y + riskBox!.height).toBeLessThan(positionsBox!.y);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`clear-${viewport.name}.png`), fullPage: false });
    await overview.getByRole("link", { name: /News, signals/ }).click();
    await expect(page.getByRole("heading", { name: "Market intelligence", exact: true })).toBeInViewport();
    for (const section of ["feed", "signals", "catalysts", "engine"]) await expect(page.getByTestId(`dashboard-section-${section}`)).toBeVisible();
    expect(requests.filter((request) => request === "GET /api/performance")).toHaveLength(1);
    expect(requests.some((request) => /POST \/api\/orders\/place/.test(request))).toBe(false);
  });
}

for (const width of [360, 768, 1024]) {
  test(`Clear overview reflows without document overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await installClearFixtures(page);
    await page.goto("/dashboard");
    await expect(page.getByRole("slider", { name: "Inspect account value history" })).toBeVisible();
    const geometry = await page.getByTestId("clear-overview").evaluate((element) => ({
      width: element.getBoundingClientRect().width,
      scrollWidth: document.documentElement.scrollWidth,
      viewport: window.innerWidth,
      buttons: [...element.querySelectorAll("button")].filter((button) => !button.disabled).map((button) => ({ width: button.getBoundingClientRect().width, height: button.getBoundingClientRect().height })),
    }));
    expect(geometry.width).toBeLessThanOrEqual(width);
    expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.viewport);
    for (const button of geometry.buttons) { expect(button.width).toBeGreaterThanOrEqual(44); expect(button.height).toBeGreaterThanOrEqual(44); }
  });
}

test("Clear overview preserves negative daily P&L, critical risk and unavailable history on mobile", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installClearFixtures(page);
  await page.route("**/api/portfolio", (route) => route.fulfill({ json: { ...CLEAR_PORTFOLIO, account_summary: { ...CLEAR_PORTFOLIO.account_summary, daily_pnl: -28_400.25, excess_liquidity: -1_000 } } }));
  await page.route("**/api/performance", (route) => route.fulfill({ json: { schema_version: 2, status: "unavailable", series: [{ date: "2026-09-04", twr_index: 101 }] } }));
  await page.goto("/dashboard");
  const overview = page.getByTestId("clear-overview");
  await expect(overview.getByText("−$28,400", { exact: true })).toBeVisible();
  await expect(overview.getByText("Account history unavailable", { exact: true })).toBeVisible();
  await expect(overview.getByRole("slider")).toHaveCount(0);
  await expect(overview.locator('a[href="#clear-risk-details"]')).toHaveAttribute("data-tone", "critical");
  await expect(overview.getByRole("button", { name: "1W", exact: true })).toBeDisabled();
  await page.screenshot({ path: testInfo.outputPath("clear-mobile-unavailable-critical.png"), fullPage: false });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
