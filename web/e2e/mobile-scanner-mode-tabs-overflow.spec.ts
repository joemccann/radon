import { expect, test, type Page } from "@playwright/test";

/**
 * Scanner mode strip on a phone: seven modes overflow a 393px viewport, so
 * the shell must SAY so — edge fades driven by data-overflow-left/right.
 * Before this shipped the strip could end flush at the viewport edge and
 * read as three-tabs-total.
 */

async function stubApis(page: Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;

    if (path === "/api/scanner") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          scan_time: "2026-07-01T15:00:00Z",
          tickers_scanned: 0,
          signals_found: 0,
          top_signals: [],
        }),
      });
      return;
    }

    if (path === "/api/portfolio") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ positions: [], account_summary: {}, exposure: {}, violations: [] }),
      });
      return;
    }

    if (path === "/api/orders") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 }),
      });
      return;
    }

    if (path === "/api/service-health") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ services: [] }) });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
}

test("overflowing mode strip shows a trailing fade, and the fade follows the scroll", async ({ page }) => {
  await stubApis(page);
  await page.goto("/scanner");

  const shell = page.locator(".scanner-mode-tabs-shell");
  const strip = page.locator(".scanner-mode-tabs");
  await expect(shell).toBeVisible();
  await expect(strip.getByRole("tab")).toHaveCount(7);

  // At rest the strip is scrolled to the start: content off-screen to the
  // right only.
  await expect(shell).toHaveAttribute("data-overflow-left", "false");
  await expect(shell).toHaveAttribute("data-overflow-right", "true");
  await page.screenshot({ path: "test-results/scanner-tabs-overflow-start.png" });

  // Scroll to the end: the affordance flips sides.
  await strip.evaluate((el) => {
    el.scrollLeft = el.scrollWidth - el.clientWidth;
  });
  await expect(shell).toHaveAttribute("data-overflow-left", "true");
  await expect(shell).toHaveAttribute("data-overflow-right", "false");
  await page.screenshot({ path: "test-results/scanner-tabs-overflow-end.png" });
});
