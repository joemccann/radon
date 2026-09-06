import { expect, test } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";
import { installClearRealtimeFixtures } from "./clear-realtime-fixtures";

test.use({ serviceWorkers: "block", reducedMotion: "reduce" });

for (const viewport of [{ label: "desktop", width: 1440, height: 1000 }, { label: "mobile", width: 390, height: 844 }]) {
  test.describe(`Clear workspace interactions ${viewport.label}`, () => {
    test.use({ viewport });
    test.beforeEach(async ({ page }) => { await installClearRealtimeFixtures(page); });

    test("options ticker entry, measurement tabs and browser history preserve the symbol", async ({ page }) => {
      const requests = await installClearFixtures(page);
      await page.goto("/options");
      await expect(page.getByRole("button", { name: "Load exposure", exact: true })).toBeDisabled();
      await page.getByLabel("Ticker symbol", { exact: true }).fill("aapl");
      await page.getByRole("button", { name: "Load exposure", exact: true }).click();
      await expect(page).toHaveURL(/\/options\/net-gex\?symbol=AAPL$/);
      await expect(page.getByTestId("options-exposure-table-wrap")).toBeVisible();
      await page.getByRole("tab", { name: "Rel Vol", exact: true }).click();
      await expect(page).toHaveURL(/\/options\/rv-ratio\?symbol=AAPL$/);
      await expect(page.getByTestId("rv-ratio-stats")).toBeVisible();
      await expect(page.getByRole("tab", { name: "Rel Vol", exact: true })).toHaveAttribute("aria-selected", "true");
      await page.goBack();
      await expect(page).toHaveURL(/\/options\/net-gex\?symbol=AAPL$/);
      await expect(page.getByTestId("options-exposure-table-wrap")).toBeVisible();
      expect(requests.filter((request) => !request.startsWith("GET "))).toEqual([]);
    });

    test("journal date controls filter actual trades and preserve realized totals", async ({ page }) => {
      await installClearFixtures(page);
      await page.goto("/journal");
      await page.getByTestId("journal-range-all").click();
      await expect(page.getByTestId("journal-trade-count")).toHaveText("2 TRADES");
      await expect(page.getByTestId("journal-range-realized")).toContainText("380");
      await page.getByTestId("journal-range-custom").click();
      await page.getByTestId("journal-range-from").fill("2026-04-01");
      await page.getByTestId("journal-range-to").fill("2026-04-30");
      await expect(page.getByTestId("journal-trade-count")).toHaveText("1 TRADES");
      await expect(page.getByTestId("journal-range-realized")).toContainText("380");
      await page.getByTestId("journal-range-mtd").click();
      await expect(page.getByTestId("journal-trade-count")).toHaveText("0 TRADES");
      await expect(page.getByTestId("journal-range-empty")).toBeVisible();
    });

    test("identity-scoped watchlist opens the selected instrument", async ({ page }) => {
      const requests = await installClearFixtures(page);
      await page.goto("/watchlist");
      await expect(page.getByTestId("watchlist-row-AAPL")).toBeVisible();
      await expect(page.getByTestId("watchlist-row-MSFT")).toBeVisible();
      if (viewport.width > 640) {
        await page.getByRole("button", { name: "Sort by Symbol", exact: true }).click();
        await page.getByRole("button", { name: "Sort by Symbol", exact: true }).click();
        await expect(page.getByTestId("watchlist-page").getByRole("columnheader", { name: "Symbol", exact: true })).toHaveAttribute("aria-sort", "descending");
        await expect(page.locator('[data-testid^="watchlist-row-"]').first()).toHaveAttribute("data-testid", "watchlist-row-MSFT");
      }
      await page.getByRole("button", { name: "Open MSFT instrument cockpit", exact: true }).click();
      await expect(page).toHaveURL(/\/MSFT$/);
      await expect(page.getByText("MSFT", { exact: true }).filter({ visible: true }).first()).toBeVisible();
      expect(requests.filter((request) => /\/api\/orders\/(place|cancel|modify)$/.test(request))).toEqual([]);
    });
  });
}
