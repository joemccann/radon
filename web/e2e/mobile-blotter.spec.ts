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

const ORDERS_EMPTY = { open_count: 0, executed_count: 0, open_orders: [], executed_orders: [], last_sync: `${TODAY}T14:34:25Z` };

const BLOTTER_MOCK = {
  as_of: `${TODAY}T14:34:25Z`,
  summary: { closed_trades: 1, open_trades: 0, total_commissions: 1.0, realized_pnl: 250 },
  closed_trades: [
    {
      symbol: "AAPL",
      contract_desc: "AAPL 200C 2026-04-18",
      sec_type: "OPT",
      is_closed: true,
      net_quantity: 0,
      total_quantity: 5,
      total_commission: 1.0,
      realized_pnl: 250,
      realized_quantity: 5,
      realized_cost_basis: 1500,
      cost_basis: 1500,
      proceeds: 1750,
      total_cash_flow: 250,
      executions: [{ time: "2026-03-23T15:00:00Z", price: 3.5, quantity: 5, side: "SELL" }],
    },
  ],
  open_trades: [],
};

async function setupMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_EMPTY) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, days_until_expiry: 14 }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(BLOTTER_MOCK) }),
  );
  await page.route("**/api/cash-flows**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows: [], summary: {} }) }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
}

test.describe("Mobile Historical Trades blotter", () => {
  test("renders trade cards instead of the desktop table", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/orders");

    await expect(page.getByTestId("mobile-blotter-list")).toBeVisible();
    const card = page.getByTestId("mobile-blotter-AAPL-0");
    await expect(card).toBeVisible();
    await expect(card).toContainText("AAPL");
    await expect(card).toContainText("Closed");
    await expect(card).toContainText("AAPL 200C 2026-04-18");
    await expect(card).toContainText("+$250");
    await expect(card).toContainText("Cost");
    await expect(card).toContainText("Proceeds");
  });

  test("desktop blotter table is hidden on mobile", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/orders");
    await expect(page.getByTestId("mobile-blotter-list")).toBeVisible();
    // The historical trades section's <table> should not render on mobile
    const tables = page.locator("section").filter({ hasText: "Historical Trades" }).locator("table");
    await expect(tables).toHaveCount(0);
  });
});
