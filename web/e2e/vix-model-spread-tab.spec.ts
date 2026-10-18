import { test, expect, type Page } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";

// Synthetic values only. Upstream Cboe bulk history is never a test fixture.
function payload() {
  const series = Array.from({ length: 300 }, (_, i) => ({
    date: new Date(Date.UTC(2026, 8, 4) - (299 - i) * 86_400_000).toISOString().slice(0, 10),
    vix: 22, vixeq: 40, cor1m: 25, model_vix: 20, spread: i % 9 === 0 ? -1 : 2,
    mean_20: 1, std_20: 0.25, upper_20: 1.5, lower_20: 0.5, z_20: 4,
    mean_252: 2, std_252: 0.5, upper_252: 3, lower_252: 1, z_252: 0,
  }));
  return { scan_time: "2026-09-05T01:00:00Z", status: "ok", source: "cboe", data_date: "2026-09-04", as_of: "2026-09-04", current: series.at(-1), count: series.length, series };
}
async function setup(page: Page, body: unknown = payload()) {
  await installClearFixtures(page);
  await page.clock.setFixedTime(new Date("2026-09-05T01:20:00Z"));
  await page.route("**/api/vix-model-spread", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) }));
}
for (const viewport of [{ name: "desktop", width: 1440, height: 1000 }, { name: "mobile", width: 393, height: 852 }]) {
  test.describe(`VIX model spread ${viewport.name}`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });
    test("switches statistical windows independently of range, shows source date and timer", async ({ page }) => {
      await setup(page); await page.goto("/regime/vix-model-spread");
      await expect(page).toHaveTitle(/VIX MODEL/);
      await expect(page.getByTestId("vix-model-spread-hero")).toHaveText("2.00 pts");
      await expect(page.getByTestId("vix-model-spread-state")).toHaveText("ABOVE BAND");
      await expect(page.getByTestId("vix-model-spread-strip-asof")).toHaveText("2026-09-04");
      await expect(page.getByTestId("vix-model-spread-freshness-rail-countdown")).toHaveText("1h 30m");
      await expect(page.getByTestId("vix-model-spread-bar")).toHaveCount(252);
      await page.getByRole("button", { name: "252 sessions" }).click();
      await expect(page.getByTestId("vix-model-spread-state")).toHaveText("WITHIN BAND");
      await page.getByTestId("vix-model-spread-range-chips-1m").click();
      await expect(page.getByTestId("vix-model-spread-bar")).toHaveCount(21);
      await expect(page.getByTestId("vix-model-spread-state")).toHaveText("WITHIN BAND");
      await expect(page.getByTestId("vix-model-spread-brush")).toBeVisible();
      expect(await page.locator("svg").evaluateAll((svgs) => svgs.every((svg) => !/NaN|Infinity/.test(svg.innerHTML)))).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      if (viewport.name === "mobile") await expect(page.getByTestId("vix-model-spread-mobile-grid")).toBeVisible();
      else await expect(page.locator('.regime-rail__item[data-tab="vix-model-spread"]')).toHaveClass(/active/);
    });
    test("renders both themes and resolves chart hover to the source date", async ({ page }) => {
      await setup(page); await page.goto("/regime/vix-model-spread");
      await expect(page.getByTestId("vix-model-spread-chart")).toBeVisible();
      for (const theme of ["light", "dark"]) {
        await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
        const target = page.getByTestId("vix-model-spread-hover");
        await target.scrollIntoViewIfNeeded();
        const bounds = await target.boundingBox();
        expect(bounds).toBeTruthy();
        await page.mouse.move(bounds!.x + bounds!.width / 2, bounds!.y + 30);
        await expect(page.getByTestId("vix-model-spread-tooltip")).toContainText("Mean (20 sessions)");
        await expect(page.getByTestId("vix-model-spread-tooltip")).toContainText("Model VIX");
      }
    });
    test("shows the aligned-source empty state", async ({ page }) => {
      await setup(page, { missing: true, current: null, series: [], data_date: null });
      await page.goto("/regime/vix-model-spread");
      await expect(page.getByText("No aligned VIX model data")).toBeVisible();
      await expect(page.getByTestId("vix-model-spread-chart")).toHaveCount(0);
    });
  });
}
