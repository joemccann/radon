import { test, expect } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";
import { aiFixture } from "../tests/fixtures/aiInfrastructure";
test.describe("AI infrastructure", () => {
  test.setTimeout(90_000);
  test.beforeEach(async ({ page }) => {
    await installClearFixtures(page);
    await page.route("**/api/ai-cycle", route => route.fulfill({ json: aiFixture }));
    await page.route("**/api/llm-token-index**", route => route.fulfill({ json: { rows: [], count: 0, days: 180 } }));
  });
  test("compatible route, four panes, chart inspection, modal keyboard focus and legacy", async ({ page }) => {
    await page.goto("/regime/llm");
    await expect(page.getByRole("button", { name: "Refresh snapshot" })).toBeEnabled({ timeout: 45000 });
    await expect(page.getByRole("heading", { name: "AI infrastructure", exact: true })).toBeVisible();
    await expect(page.getByTestId("ai-indicator-D1").getByTestId("ai-source-fixture")).toContainText("Fixture publisher");
    await expect(page.getByTestId("ai-indicator-D5")).toContainText("Ramp business AI spend");
    await expect(page.getByTestId("ai-indicator-D5").getByTestId("ai-source-ramp")).toContainText("Ramp AI Index");
    await expect(page.getByTestId("ai-source-coverage")).toContainText("Ramp AI Index");
    await expect(page.getByTestId("ai-coverage-fixture")).toContainText("Fixture publisher");
    for (const name of ["Demand", "Compute", "Delivery", "Finance"]) {
      await page.getByRole("tab", { name, exact: true }).click();
      await expect(page.getByRole("tab", { name, exact: true })).toHaveAttribute("aria-selected", "true");
      const sliders = page.getByRole("slider", { name: /Inspect .* history/ });
      // Demand hosts D1 plus D5 (Ramp), so two inspect sliders share the pane.
      await expect(sliders).toHaveCount(name === "Demand" ? 2 : 1);
      await expect(sliders.first()).toBeVisible();
    }
    const opener = page.getByRole("button", { name: "Sources and method: Cash funding coverage" }); await opener.click();
    await expect(page.getByRole("dialog")).toBeVisible(); await expect(page.getByText("Not disclosed; first-seen history only")).toBeVisible(); await expect(page.getByText("coverage_numerator", { exact: false })).toBeVisible();
    await page.keyboard.press("Escape"); await expect(page.getByRole("dialog")).toHaveCount(0); await expect(opener).toBeFocused();
    for (const theme of ["light", "dark"]) {
      await page.evaluate(theme => document.documentElement.setAttribute("data-theme", theme), theme);
      await page.screenshot({ path: `test-results/ai-infrastructure-${theme}-desktop.png`, fullPage: true });
      await page.getByTestId("ai-indicator-F1").getByTestId("ai-source-fixture").screenshot({ path: `test-results/ai-infrastructure-source-${theme}-desktop.png` });
    }
    await page.getByText("Legacy inference price series · methodology v1").click(); await expect(page.getByTestId("llm-token-index-card")).toBeVisible();
  });
  test("dashboard research rail opens the existing infrastructure route", async ({ page }) => {
    const mutations: string[] = [];
    page.on("request", request => { if (/\/api\/orders\/(place|cancel|modify)/.test(request.url())) mutations.push(request.url()); });
    await page.goto("/dashboard");
    const handoff = page.getByTestId("clear-overview").getByRole("link", { name: "AI infrastructure evidence" });
    await expect(handoff).toHaveAttribute("href", "/regime/llm");
    await handoff.click();
    await expect(page).toHaveURL(/\/regime\/llm$/);
    await expect(page.getByRole("tab", { name: "Demand", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("button", { name: "Refresh snapshot" })).toBeEnabled();
    expect(mutations).toEqual([]);
  });
  test("ticker info handoff preserves the dated mapping and opens its finance pane", async ({ page }) => {
    const mutations: string[] = [];
    page.on("request", request => { if (/\/api\/orders\/(place|cancel|modify)/.test(request.url())) mutations.push(request.url()); });
    await page.goto("/regime/llm");
    const tickerLink = page.getByTestId("ai-indicator-D1").getByRole("link", { name: "NVDA", exact: true });
    await expect(tickerLink).toHaveAttribute("href", "/NVDA?deck=i");
    await tickerLink.click();
    await expect(page).toHaveURL(/\/NVDA\?deck=i$/);
    await expect(page.getByRole("complementary", { name: "AI infrastructure research" })).toContainText("Compute supply");
    await page.goto("/MSFT?deck=i");
    const mapping = page.getByRole("complementary", { name: "AI infrastructure research" });
    await expect(mapping).toContainText("Cloud monetization");
    await expect(mapping).toContainText("September 7, 2026");
    await expect(mapping).toContainText("unverified");
    const handoff = mapping.getByRole("link", { name: "Review infrastructure evidence" });
    await expect(handoff).toHaveAttribute("href", "/regime/llm?pane=finance");
    await handoff.click();
    await expect(page).toHaveURL(/\/regime\/llm\?pane=finance$/);
    await expect(page.getByRole("tab", { name: "Finance", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("ai-indicator-F1")).toBeVisible();
    expect(mutations).toEqual([]);
  });
  test("stale measurements and failed refresh stay explicit", async ({ page }) => {
    await page.route("**/api/ai-cycle", route => route.fulfill({ json: { ...aiFixture, indicators: aiFixture.indicators.map(i => ({ ...i, status: "stale" })) } }));
    await page.goto("/regime/llm");
    await expect(page.getByTestId("ai-indicator-D5").getByText("stale", { exact: true })).toBeVisible();
    await page.route("**/api/ai-cycle", route => route.fulfill({ status: 503, json: { detail: "Unavailable" } }));
    await page.getByRole("button", { name: "Refresh snapshot" }).click(); await expect(page.getByTestId("ai-infrastructure-panel").getByRole("alert")).toContainText("HTTP 503"); await expect(page.getByText("Insufficient evidence", { exact: true })).toBeVisible();
  });
  for (const theme of ["light", "dark"]) test(`mobile ${theme} viewport and source evidence`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 }); await page.goto("/regime/llm"); await expect(page.getByTestId("ai-infrastructure-panel")).toBeVisible(); await page.evaluate(theme => document.documentElement.setAttribute("data-theme", theme), theme);
    await page.getByRole("tab", { name: "Compute", exact: true }).click(); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/ai-infrastructure-${theme}-mobile.png`, fullPage: true });
    await page.getByRole("button", { name: "Sources and method: Matched GPU asking prices" }).click(); await expect(page.getByRole("dialog")).toBeVisible(); expect(await page.getByRole("dialog").evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  });
});
