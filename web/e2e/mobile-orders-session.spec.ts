/**
 * E2E: mobile /orders session capsule at 393x852.
 */
import { test, expect, type Page } from "@playwright/test";

const OPEN_NOW = new Date("2026-07-09T15:00:00.000Z");

test.use({ viewport: { width: 393, height: 852 } });

const PORTFOLIO_EMPTY = {
  bankroll: 1_000_000,
  peak_value: 1_000_000,
  last_sync: OPEN_NOW.toISOString(),
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

function makeOpenOrder(overrides: Record<string, unknown> = {}) {
  const totalQuantity = (overrides.totalQuantity as number | undefined) ?? 10;
  return {
    orderId: 1,
    permId: 1001,
    symbol: "AAPL",
    contract: {
      conId: 1,
      symbol: "AAPL",
      secType: "OPT",
      strike: 200,
      right: "C",
      expiry: "2026-08-21",
    },
    action: "BUY",
    orderType: "LMT",
    totalQuantity,
    limitPrice: 1.5,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: totalQuantity,
    avgFillPrice: null,
    tif: "DAY",
    ...overrides,
  };
}

async function stubOrdersPage(page: Page, orders: unknown) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_EMPTY) }),
  );
  await page.route("**/api/orders**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(orders) }),
  );
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, days_until_expiry: 14 }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: OPEN_NOW.toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  await page.route("**/api/cash-flows**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows: [], summary: {} }) }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
}

test.describe("Mobile orders session window", () => {
  test("card capsule and sheet Session metric", async ({ page }) => {
    await page.clock.install({ time: OPEN_NOW });
    await stubOrdersPage(page, {
      last_sync: OPEN_NOW.toISOString(),
      open_count: 2,
      executed_count: 0,
      executed_orders: [],
      open_orders: [
        makeOpenOrder({
          orderId: 1,
          permId: 1001,
          tif: "DAY",
        }),
        makeOpenOrder({
          orderId: 2,
          permId: 1002,
          symbol: "TQQQ",
          tif: "GTC",
          outsideRth: true,
          limitPrice: 50,
          contract: {
            conId: 20,
            symbol: "TQQQ",
            secType: "STK",
            strike: null,
            right: null,
            expiry: null,
          },
        }),
      ],
    });

    await page.goto("/orders");
    await expect(page.getByTestId("mobile-order-list")).toBeVisible();

    const optionCard = page.getByTestId("mobile-order-single-1001");
    const stkCard = page.getByTestId("mobile-order-single-1002");
    await expect(optionCard.getByTestId("mobile-order-session")).toHaveText("RTH");
    await expect(stkCard.getByTestId("mobile-order-session")).toHaveText("EXT");

    await optionCard.click({ force: true });
    const summary = page.getByTestId("mobile-order-sheet-summary");
    await expect(summary).toContainText("Session");
    await expect(summary).toContainText(/will not fill after 16:00 ET/i);
  });
});
