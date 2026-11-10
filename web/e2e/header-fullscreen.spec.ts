import { test, expect } from "@playwright/test";

const PORTFOLIO = {
  bankroll: 100000,
  peak_value: 100000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [],
  exposure: {},
  violations: [],
};

const ORDERS = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

async function stubRoutes(page: import("@playwright/test").Page) {
  await page.route("**/api/portfolio**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS) }),
  );
  await page.route("**/api/prices", (route) => route.abort());
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 10, cri: { score: 10 } }) }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: false }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
  await page.route("**/api/ticker/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) }),
  );
}

test.describe("Header fullscreen control", () => {
  test("toggles fullscreen and exits on Escape", async ({ page }) => {
    await stubRoutes(page);
    await page.goto("/orders");

    const fullscreenButton = page.locator('[aria-label="Enter fullscreen"]').first();
    await expect(fullscreenButton).toBeVisible();

    await fullscreenButton.click();
    await expect
      .poll(async () => page.evaluate(() => Boolean(document.fullscreenElement)))
      .toBe(true);
    await expect(page.locator('[aria-label="Exit fullscreen"]')).toBeVisible();

    await page.keyboard.press("Escape");
    await expect
      .poll(async () => page.evaluate(() => Boolean(document.fullscreenElement)))
      .toBe(false);
    await expect(page.locator('[aria-label="Enter fullscreen"]')).toBeVisible();
  });
});
