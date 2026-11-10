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

const STALE_BLOTTER = {
  as_of: "2026-03-18T16:00:00Z",
  summary: { closed_trades: 1, open_trades: 0, total_commissions: 2.6, realized_pnl: 340 },
  closed_trades: [
    {
      symbol: "AAPL",
      contract_desc: "AAPL 20260320 200C",
      sec_type: "OPT",
      is_closed: true,
      net_quantity: 0,
      total_commission: 2.6,
      realized_pnl: 340,
      cost_basis: 1200,
      proceeds: 1540,
      total_cash_flow: 340,
      executions: [],
    },
  ],
  open_trades: [],
};

const FRESH_BLOTTER = {
  as_of: "2026-03-19T16:10:00Z",
  summary: { closed_trades: 2, open_trades: 0, total_commissions: 5.2, realized_pnl: 725 },
  closed_trades: [
    {
      symbol: "AAPL",
      contract_desc: "AAPL 20260320 200C",
      sec_type: "OPT",
      is_closed: true,
      net_quantity: 0,
      total_commission: 2.6,
      realized_pnl: 340,
      cost_basis: 1200,
      proceeds: 1540,
      total_cash_flow: 340,
      executions: [],
    },
    {
      symbol: "GOOG",
      contract_desc: "GOOG 20260320 180C",
      sec_type: "OPT",
      is_closed: true,
      net_quantity: 0,
      total_commission: 2.6,
      realized_pnl: 385,
      cost_basis: 1540,
      proceeds: 1925,
      total_cash_flow: 385,
      executions: [],
    },
  ],
  open_trades: [],
};

type BlotterStub = {
  /** Flip to serve FRESH_BLOTTER on every subsequent GET. */
  serveFresh: () => void;
  /** Every POST the page issued to /api/blotter. Must stay empty. */
  posts: string[];
};

async function stubOrdersPage(page: import("@playwright/test").Page): Promise<BlotterStub> {
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

  // GET-only, exactly like production since 26668ef8: POST /api/blotter is a
  // 404 ("Blotter rehydrate is file-ingest only. GET reads journal_sync") and
  // useBlotter sets hasPost: false, so the Refresh button re-GETs. This stub
  // therefore has NO POST branch — a POST is recorded and answered with the
  // real 404 so the assertion below, not a timeout, reports the regression.
  let fresh = false;
  const posts: string[] = [];

  await page.route("**/api/blotter", (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      posts.push(request.url());
      return route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({
          error: "Blotter rehydrate is file-ingest only. GET reads journal_sync.",
        }),
      });
    }

    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(fresh ? FRESH_BLOTTER : STALE_BLOTTER),
    });
  });

  return {
    serveFresh: () => {
      fresh = true;
    },
    posts,
  };
}

test("historical trades refresh re-reads the journal-derived blotter on /orders", async ({
  page,
}) => {
  const blotter = await stubOrdersPage(page);

  await page.goto("/orders");

  const section = page.getByTestId("historical-trades-section");
  await expect(section.locator("text=Historical Trades (30 Days)")).toBeVisible({
    timeout: 15_000,
  });

  await expect(section.locator("text=1 TRADES")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("text=AAPL 20260320 200C")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("text=GOOG 20260320 180C")).toHaveCount(0);

  // The journal picked up the second fill; Refresh must surface it via GET.
  blotter.serveFresh();
  await section.locator("button.sync-button").click();

  await expect(section.locator("text=2 TRADES")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("text=GOOG 20260320 180C")).toBeVisible({ timeout: 10_000 });

  expect(blotter.posts).toEqual([]);
});
