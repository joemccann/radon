import { expect, test } from "@playwright/test";

const PORTFOLIO_MOCK = {
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
  positions: [],
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 100_000,
    daily_pnl: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 200_000,
    dividends: 0,
  },
};

const ORDERS_MOCK = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const BLOTTER_MOCK = {
  as_of: new Date("2026-03-25T16:00:00Z").toISOString(),
  summary: {
    closed_trades: 2,
    open_trades: 1,
    total_commissions: 7.8,
    realized_pnl: 180,
  },
  closed_trades: [
    {
      symbol: "AAPL",
      contract_desc: "AAPL 20260320 180C",
      sec_type: "OPT",
      is_closed: true,
      net_quantity: 0,
      total_commission: 2.5,
      realized_pnl: 120,
      cost_basis: 1200,
      proceeds: 1320,
      total_cash_flow: 120,
      executions: [
        {
          exec_id: "e1",
          time: "2026-03-24T10:10:00.000Z",
          side: "SLD",
          quantity: 1,
          price: 13.2,
          commission: 1.25,
          notional_value: 1320,
          net_cash_flow: -1318.75,
        },
      ],
    },
    {
      symbol: "MSFT",
      contract_desc: "MSFT 20260320 350P",
      sec_type: "OPT",
      is_closed: true,
      net_quantity: 0,
      total_commission: 2.6,
      realized_pnl: 60,
      cost_basis: 900,
      proceeds: 960,
      total_cash_flow: 60,
      executions: [
        {
          exec_id: "e2",
          time: "2026-03-23T11:15:00.000Z",
          side: "SLD",
          quantity: 1,
          price: 9.6,
          commission: 1.3,
          notional_value: 960,
          net_cash_flow: -961.3,
        },
      ],
    },
  ],
  open_trades: [
    {
      symbol: "TSLA",
      contract_desc: "TSLA 20260320 250C",
      sec_type: "OPT",
      is_closed: false,
      net_quantity: 2,
      total_commission: 2.7,
      realized_pnl: 0,
      cost_basis: 0,
      proceeds: 0,
      total_cash_flow: 0,
      executions: [
        {
          exec_id: "e3",
          time: "2026-03-22T09:40:00.000Z",
          side: "BOT",
          quantity: 2,
          price: 18,
          commission: 1.4,
          notional_value: 3600,
          net_cash_flow: 3598.6,
        },
      ],
    },
  ],
};

async function stubOrdersPage(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_MOCK),
    }),
  );

  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_MOCK),
    }),
  );

  await page.route("**/api/prices", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({}),
    }),
  );

  await page.route("**/api/regime", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ score: 15, level: "LOW", cri: { score: 15 } }),
    }),
  );

  await page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: true }),
    }),
  );

  await page.route("**/api/blotter", (route) => {
    const method = route.request().method();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(BLOTTER_MOCK),
    });
  });
}

test("historical trades table supports client-side filtering", async ({ page }) => {
  await stubOrdersPage(page);

  await page.goto("/orders");

  const section = page.locator("text=Historical Trades (30 Days)");
  await expect(section).toBeVisible({ timeout: 15_000 });

  await expect(page.getByPlaceholder("Filter historical trades...")).toBeVisible();
  await expect(page.getByText("AAPL 20260320 180C")).toBeVisible();
  await expect(page.getByText("MSFT 20260320 350P")).toBeVisible();
  await expect(page.getByText("TSLA 20260320 250C")).toBeVisible();

  const filter = page.getByPlaceholder("Filter historical trades...");
  await filter.fill("AAPL");

  await expect(page.getByText("AAPL 20260320 180C")).toBeVisible();
  await expect(page.getByText("MSFT 20260320 350P")).toHaveCount(0);
  await expect(page.getByText("TSLA 20260320 250C")).toHaveCount(0);
  await expect(page.getByText("1/3")).toBeVisible();
});
