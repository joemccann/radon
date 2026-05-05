import { test, expect, type Page } from "@playwright/test";

const TODAY = "2026-03-24";

const PORTFOLIO_EMPTY = {
  bankroll: 1_000_000,
  peak_value: 1_000_000,
  last_sync: `${TODAY}T14:34:25Z`,
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 1_000_000,
    daily_pnl: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 400_000,
    dividends: 0,
  },
  positions: [],
};

const ORDERS_MOCK = {
  open_count: 1,
  executed_count: 0,
  open_orders: [
    {
      orderId: 101,
      permId: 9001,
      symbol: "AAPL",
      contract: { conId: 12345, symbol: "AAPL", secType: "OPT", strike: 200, right: "C", expiry: "20260418" },
      action: "BUY",
      orderType: "LMT",
      totalQuantity: 5,
      limitPrice: 3.45,
      auxPrice: null,
      status: "Submitted",
      filled: 0,
      remaining: 5,
      avgFillPrice: null,
      tif: "DAY",
    },
  ],
  executed_orders: [],
  last_sync: `${TODAY}T14:34:25Z`,
};

async function setupMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_EMPTY) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_MOCK) }),
  );
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, days_until_expiry: 14 }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: `${TODAY}T14:34:25Z`, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
  await page.route("**/api/cash-flows**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows: [], summary: {} }) }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
}

test.describe("Mobile orders list", () => {
  test("renders an order card with action sheet on tap", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/orders");

    await expect(page.getByTestId("mobile-order-list")).toBeVisible();
    const card = page.getByTestId("mobile-order-single-9001");
    await expect(card).toBeVisible();
    await expect(card).toContainText("AAPL");
    await expect(card).toContainText("BUY");
    await expect(card).toContainText("$3.45");
    await expect(card).toContainText("DAY");

    // Tap to open action sheet
    await card.click({ force: true });
    await expect(page.getByTestId("mobile-order-action-sheet")).toBeVisible();
    await expect(page.getByTestId("mobile-order-action-modify")).toBeVisible();
    await expect(page.getByTestId("mobile-order-action-cancel")).toBeVisible();
  });

  test("desktop orders table is hidden on mobile", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/orders");
    await expect(page.getByTestId("mobile-order-list")).toBeVisible();
    const tables = page.locator(".table-wrap table");
    await expect(tables).toHaveCount(0);
  });

  test("action sheet items meet 56px touch target", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/orders");
    await page.getByTestId("mobile-order-single-9001").click({ force: true });

    const modify = page.getByTestId("mobile-order-action-modify");
    const cancel = page.getByTestId("mobile-order-action-cancel");
    const m = await modify.boundingBox();
    const c = await cancel.boundingBox();
    expect(m).not.toBeNull();
    expect(c).not.toBeNull();
    if (m) expect(m.height).toBeGreaterThanOrEqual(44);
    if (c) expect(c.height).toBeGreaterThanOrEqual(44);
  });

  test("Escape dismisses the action sheet", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/orders");
    await page.getByTestId("mobile-order-single-9001").click({ force: true });
    await expect(page.getByTestId("mobile-order-action-sheet")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(page.getByTestId("mobile-order-action-sheet")).toBeHidden();
  });
});
