/**
 * E2E: Mobile Options Chain ladder at 393×852.
 *
 * Validates:
 * 1. Two-column ladder renders for AAPL with calls/strike/puts.
 * 2. Expiry chip bar lists at least the available expirations.
 * 3. Tapping a strike cell opens the detail bottom sheet.
 * 4. The desktop chain table is hidden on mobile.
 */

import { test, expect, type Page } from "@playwright/test";

const PORTFOLIO = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [],
};

const ORDERS = { last_sync: new Date().toISOString(), open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 };

function stubApis(page: Page) {
  page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS) }),
  );
  page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: false }) }),
  );
  page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
  page.route("**/api/cash-flows**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows: [], summary: {} }) }),
  );
  page.route("**/api/ticker/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ uw_info: { name: "Apple Inc.", sector: "Tech", description: "" }, stock_state: {}, profile: {}, stats: {} }) }),
  );
  page.route("**/api/options/expirations*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ symbol: "AAPL", expirations: ["20260320", "20260417"] }) }),
  );
  page.route("**/api/options/chain*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ symbol: "AAPL", expiry: "20260320", exchange: "SMART", strikes: [195, 200, 205, 210, 215], multiplier: "100" }) }),
  );
  page.route("**/api/prices**", (route) => route.abort());
}

test.describe("Mobile Options Chain ladder", () => {
  test("renders two-column ladder with strike column and CALLS/PUTS labels", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await page.goto("/AAPL?tab=chain");

    await expect(page.getByTestId("mobile-chain")).toBeVisible();
    await expect(page.getByTestId("mobile-chain-ladder")).toBeVisible();

    const head = page.locator(".mobile-chain__ladder-head");
    await expect(head).toContainText("CALLS");
    await expect(head).toContainText("STRIKE");
    await expect(head).toContainText("PUTS");

    // All five strikes from the mock should render
    for (const strike of [195, 200, 205, 210, 215]) {
      await expect(page.getByTestId(`mobile-chain-row-${strike}`)).toBeVisible();
    }
  });

  test("expiry chip bar lists each expiration", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await page.goto("/AAPL?tab=chain");

    await expect(page.getByTestId("mobile-chain-expiry-bar")).toBeVisible();
    await expect(page.getByTestId("mobile-chain-expiry-20260320")).toBeVisible();
    await expect(page.getByTestId("mobile-chain-expiry-20260417")).toBeVisible();
  });

  test("tapping a call cell opens the detail bottom sheet", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await page.goto("/AAPL?tab=chain");

    await page.getByTestId("mobile-chain-call-200").click({ force: true });
    await expect(page.getByTestId("mobile-chain-detail-sheet")).toBeVisible();
    const sheet = page.getByTestId("mobile-chain-detail-sheet");
    await expect(sheet).toContainText("AAPL 200 Call");

    await page.keyboard.press("Escape");
    await expect(page.getByTestId("mobile-chain-detail-sheet")).toBeHidden();
  });

  test("desktop chain table is not rendered on mobile", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await page.goto("/AAPL?tab=chain");

    await expect(page.getByTestId("mobile-chain")).toBeVisible();
    await expect(page.locator(".chain-grid")).toHaveCount(0);
  });
});
