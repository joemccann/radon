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
  as_of: "2026-04-22T16:10:00Z",
  summary: { closed_trades: 0, open_trades: 1, total_commissions: 4.93, realized_pnl: 17275.97 },
  closed_trades: [],
  open_trades: [
    {
      symbol: "ALAB",
      contract_desc: "ALAB 20270115 120C",
      sec_type: "OPT",
      is_closed: false,
      net_quantity: 2,
      total_quantity: 5,
      total_commission: 4.93,
      realized_pnl: 17275.97,
      realized_quantity: 3,
      realized_cost_basis: 11071.34,
      cost_basis: 7380.90,
      proceeds: 28347.31,
      total_cash_flow: 9883.07,
      executions: [
        {
          exec_id: "alab-open",
          time: "2026-04-15T14:30:00.000Z",
          side: "BOT",
          quantity: 5,
          price: 36.90,
          commission: 4.93,
          notional_value: 18447.31,
          net_cash_flow: -18452.24,
        },
        {
          exec_id: "alab-close-partial",
          time: "2026-04-21T19:00:00.000Z",
          side: "SLD",
          quantity: 3,
          price: 94.51,
          commission: 0,
          notional_value: 28347.31,
          net_cash_flow: 28347.31,
        },
      ],
    },
  ],
};

async function stubOrdersPage(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_MOCK) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_MOCK) }),
  );
  await page.route("**/api/prices", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, level: "LOW", cri: { score: 15 } }) }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(BLOTTER_MOCK) }),
  );
}

test("orders historical trades show realized pnl for partially closed open positions", async ({ page }) => {
  await stubOrdersPage(page);
  await page.goto("/orders");

  await expect(page.getByText("ALAB 20270115 120C")).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("+$17,275.97 (+156.0%) · 3 sold")).toBeVisible();
  await expect(page.getByText("$7,380.90")).toBeVisible();
  await expect(page.getByText("$28,347.31")).toBeVisible();
});
