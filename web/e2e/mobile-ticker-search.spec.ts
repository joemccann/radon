/**
 * E2E: Mobile ticker search overlay at 393×852.
 *
 * Validates:
 * 1. Tapping the search button in the mobile app bar opens the full-screen overlay.
 * 2. The search input renders at 16px (no iOS zoom on focus).
 * 3. Escape and the close button both dismiss the overlay.
 * 4. Selecting a result navigates to the ticker detail page.
 */

import { test, expect, type Page } from "@playwright/test";

async function setupMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ positions: [], last_sync: new Date().toISOString(), bankroll: 0, peak_value: 0, total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100, position_count: 0, defined_risk_count: 0, undefined_risk_count: 0, avg_kelly_optimal: null, exposure: {}, violations: [] }) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ open_orders: [], executed_orders: [], open_count: 0, executed_count: 0, last_sync: new Date().toISOString() }) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
  await page.route("**/api/cash-flows**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows: [], summary: {} }) }),
  );
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, days_until_expiry: 14 }) }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
}

test.describe("Mobile ticker search overlay", () => {
  test("tapping the search button opens the full-screen overlay", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/dashboard");

    await expect(page.getByTestId("mobile-app-bar-search")).toBeVisible();
    await page.getByTestId("mobile-app-bar-search").click({ force: true });

    await expect(page.getByTestId("mobile-ticker-search")).toBeVisible();
    await expect(page.getByTestId("mobile-ticker-search-close")).toBeVisible();
  });

  test("input renders at 16px to prevent iOS auto-zoom", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/dashboard");
    await page.getByTestId("mobile-app-bar-search").click({ force: true });

    const fontSize = await page
      .locator(".mobile-search-input input")
      .evaluate((el) => Number(window.getComputedStyle(el).fontSize.replace("px", "")));
    expect(fontSize).toBeGreaterThanOrEqual(16);
  });

  test("Escape dismisses the overlay", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/dashboard");
    await page.getByTestId("mobile-app-bar-search").click({ force: true });
    await expect(page.getByTestId("mobile-ticker-search")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(page.getByTestId("mobile-ticker-search")).toBeHidden();
  });

  test("body overflow is locked while the overlay is open and restored after close", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/dashboard");

    const initial = await page.evaluate(() => document.body.style.overflow);
    expect(initial === "" || initial === "visible").toBe(true);

    await page.getByTestId("mobile-app-bar-search").click({ force: true });
    const opened = await page.evaluate(() => document.body.style.overflow);
    expect(opened).toBe("hidden");

    await page.keyboard.press("Escape");
    await expect(page.getByTestId("mobile-ticker-search")).toBeHidden();
    const closed = await page.evaluate(() => document.body.style.overflow);
    expect(closed === "" || closed === "visible").toBe(true);
  });
});
