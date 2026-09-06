import { test, expect } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";
import { installClearRealtimeFixtures } from "./clear-realtime-fixtures";

test.use({ serviceWorkers: "block", colorScheme: "light", reducedMotion: "reduce" });

const surfaces = [
  { name: "scanner", path: "/scanner", loaded: '[data-testid="flow-order-link-AAPL"], [data-testid="mobile-scanner-list"]' },
  { name: "options", path: "/options/net-gex?symbol=AAPL", loaded: '[data-testid="options-exposure-table-wrap"]' },
  { name: "risk", path: "/regime/cri", loaded: ".regime-hero, .m-regime-headline" },
  { name: "curve", path: "/regime/curve", loaded: '[data-testid="yield-curve-chart-section"]' },
  { name: "positions", path: "/portfolio", loaded: '[data-testid="position-table"], [data-testid="mobile-position-list"]' },
  { name: "chain", path: "/AAPL?deck=c", loaded: '.chain-strike, [data-testid^="mobile-chain-call-"]' },
  { name: "book", path: "/AAPL?tab=book", loaded: '.book-feed-pill' },
];

for (const width of [360, 390, 768, 1440]) {
  for (const surface of surfaces) {
    test(`Clear ${surface.name} at ${width}px`, async ({ page }, testInfo) => {
      test.setTimeout(90_000);
      await page.setViewportSize({ width, height: 900 });
      const requests = await installClearFixtures(page);
      await installClearRealtimeFixtures(page);
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(surface.path);
      await expect(page.locator(surface.loaded).filter({ visible: true }).first()).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      if (surface.name === "options") {
        const clippedValues = await page.locator('[data-testid^="exposure-value-"]').evaluateAll((values) => values.filter((value) => value.scrollWidth > value.clientWidth + 1).length);
        expect(clippedValues, "Financial exposure values must not be ellipsized on narrow screens").toBe(0);
        await page.getByLabel("Exposure metric").selectOption("open_interest");
        await expect(page.getByRole("columnheader", { name: "Open Interest" })).toBeVisible();
        await page.getByLabel("Strike range").selectOption("5");
        await expect(page.locator('[data-testid^="exposure-row-"]')).toHaveCount(11);
        await page.getByRole("button", { name: "Toggle HVL level", exact: true }).click();
        await expect(page.getByRole("button", { name: "Toggle HVL level", exact: true })).toHaveAttribute("aria-pressed", "false");
        const expiry = page.getByRole("group", { name: "Expiration filter" }).getByRole("button").nth(1);
        await expiry.click();
        await expect(expiry).toHaveAttribute("aria-pressed", "true");
        // Restore the full measured ladder for the screenshot and exercise
        // horizontal navigation without truncating the source numbers.
        await page.getByRole("button", { name: "All Expirations", exact: true }).click();
        await page.getByLabel("Exposure metric").selectOption("net_gex");
        await page.getByLabel("Strike range").selectOption("10");
        await page.getByRole("button", { name: "Toggle HVL level", exact: true }).click();
        const wrap = page.getByTestId("options-exposure-table-wrap");
        if (width <= 640) {
          await wrap.evaluate((element) => { element.scrollTop = 300; element.scrollLeft = 250; });
          const sticky = await wrap.evaluate((element) => ({ top: element.getBoundingClientRect().top, header: element.querySelector("thead th")!.getBoundingClientRect().top, scroll: element.scrollTop }));
          expect(sticky.scroll).toBeGreaterThan(0);
          expect(Math.abs(sticky.header - sticky.top)).toBeLessThanOrEqual(1);
          await wrap.evaluate((element) => { element.scrollTop = 0; element.scrollLeft = 0; });
        }
      }
      if (surface.name === "book" && width <= 640) {
        const trade = page.locator('.glyph').filter({ hasText: "Trade" });
        const bounds = await trade.boundingBox();
        expect(bounds).not.toBeNull();
        expect(bounds!.x + bounds!.width, "Trade must be directly reachable inside the mobile viewport").toBeLessThanOrEqual(width);
        expect(bounds!.width).toBeGreaterThanOrEqual(44);
        await trade.click();
        await expect(page.locator('.asset-deck.open .asset-deck-hd')).toContainText("Order Ticket");
        const more = page.getByRole("button", { name: "More instrument tools", exact: true });
        await more.click();
        for (const label of ["Ratings", "Seasonality", "Company", "13F holdings", "Filings"]) {
          await expect(page.getByRole("button", { name: label, exact: true })).toBeInViewport();
        }
        await page.keyboard.press("Escape");
        await expect(more).toBeFocused();
        await expect(page.locator('.asset-deck.open .asset-deck-hd')).toContainText("Order Ticket");
        await more.click();
        await page.getByRole("button", { name: "Company", exact: true }).click();
        await expect(page.locator('.asset-deck.open .asset-deck-hd')).toContainText("Info / Company");
        await page.locator('.asset-deck.open .asset-deck-x').click();
        await expect(page.locator('.asset-deck')).toHaveAttribute("aria-hidden", "true");
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
      expect(errors).toEqual([]);
      expect(requests.filter((request) => /\/api\/orders\/(place|cancel|modify)$/.test(request))).toEqual([]);
      const smallData = await page.locator(".content th, .content td").evaluateAll((cells) => cells
        .filter((cell) => cell.getBoundingClientRect().width > 0 && (cell.textContent ?? "").trim())
        .filter((cell) => Number.parseFloat(getComputedStyle(cell).fontSize) < 12)
        .slice(0, 12)
        .map((cell) => ({ text: cell.textContent?.trim().slice(0, 50), fontSize: getComputedStyle(cell).fontSize, className: cell.className })));
      await testInfo.attach("financial-typography", { body: JSON.stringify(smallData), contentType: "application/json" });
      await page.screenshot({ path: testInfo.outputPath(`${surface.name}-${width}.png`), fullPage: false, animations: "disabled" });
    });
  }
}
