/**
 * Bug regression — historical trades' date column rendered the day BEFORE
 * today's executed trades whenever the journal `time` was a date-only ISO
 * string ("YYYY-MM-DD"). `new Date("2026-05-08").toLocaleDateString()` in
 * any zone west of UTC produced "5/7/2026"; users reported "today's trades
 * are missing" because they searched for today's calendar day and saw
 * none.
 *
 * Force the page into America/Los_Angeles (UTC-7/-8) so the bug repros
 * deterministically on any CI runner. Mock /api/blotter with one trade
 * dated as a date-only string. Confirm the rendered row carries the SAME
 * day-of-month as the input, not the day before.
 */
import { expect, test, type Page } from "@playwright/test";

test.use({ timezoneId: "America/Los_Angeles" });

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

// Date-only "today" — exactly the shape journal_rehydrate.py persists for
// fills with no recorded intraday timestamp.
const TODAY_DATE_ONLY = "2026-05-08";

const TODAY_BLOTTER = {
  as_of: `${TODAY_DATE_ONLY}T16:30:00Z`,
  summary: { closed_trades: 1, open_trades: 0, total_commissions: 1.5, realized_pnl: 250 },
  closed_trades: [
    {
      symbol: "RDDT",
      contract_desc: "RDDT Closed Call $200 2026-05-08",
      sec_type: "OPT",
      is_closed: true,
      net_quantity: 0,
      total_commission: 1.5,
      realized_pnl: 250,
      cost_basis: 1500,
      proceeds: 1750,
      total_cash_flow: 250,
      executions: [
        {
          exec_id: "rddt-today-001",
          time: TODAY_DATE_ONLY,
          side: "SLD",
          quantity: 5,
          price: 3.5,
          commission: 1.5,
          notional_value: 1750,
          net_cash_flow: 1748.5,
        },
      ],
    },
  ],
  open_trades: [],
};

async function stubOrdersPage(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio**", (route) =>
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
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(TODAY_BLOTTER) }),
  );
}

test("date-only today's trade renders today's day, not yesterday's", async ({ page }) => {
  await stubOrdersPage(page);
  await page.goto("/orders");

  const section = page.getByTestId("historical-trades-section");
  await expect(section).toBeVisible({ timeout: 15_000 });

  const dateCell = section.locator("table tbody tr").first().locator("td").first();
  await expect(dateCell).toBeVisible({ timeout: 10_000 });

  const rendered = (await dateCell.innerText()).trim();

  // The rendered value must contain day 8 (the input day) and must NOT
  // be the day before — locale-agnostic check works whether the cell shows
  // "5/8/2026", "8/5/2026", or "May 8, 2026".
  expect(rendered).toMatch(/(^|\D)8(\D|$)/);
  expect(rendered).not.toMatch(/(^|\D)7(\D|$)/);
});
