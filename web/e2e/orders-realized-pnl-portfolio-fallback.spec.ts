/**
 * E2E: Today's Executed Orders correctly classifies a buy-to-close on a
 * short put even when IB's commission report (which carries realizedPNL)
 * hasn't arrived yet.
 *
 * REGRESSION:
 *   User sold to close a short TSLA $400 put at $10.00. IB returned the
 *   fill but the commission report was still in flight, so realizedPNL was
 *   null on the executedOrders payload. The position grouper then treated
 *   the BOT fill as opening a new long put — pill said "OPEN", description
 *   said "Long $400 Put", and Realized P&L showed "—".
 *
 * EXPECTED:
 *   - Pill: "CLOSE" (not "OPEN")
 *   - Description includes "Short" + "Put" (we closed a short put)
 *   - Realized P&L = (avg_cost − close_price × 100) × qty
 *     entry_premium=$15/contract → avg_cost $1500
 *     close=$10/contract → close per-contract = $1000
 *     P&L per contract = $1500 − $1000 = $500
 *     Total = $500 × 10 = $5,000
 */

import { test, expect, type Page } from "@playwright/test";

// Window-relative, not a fixed calendar date: "Today's Executed Orders" filters
// fills to the real wall-clock ET calendar day (lib/orders/executedToday.ts:
// filterExecutedToEtToday / isFillOnEtCalendarDay), so a hardcoded past ISO
// timestamp here rots the moment the suite runs on a later date (it did: this
// spec pinned 2026-05-06 and, run today, every fixture fill silently fell
// outside "today" and the table rendered its empty state instead).
const TODAY_ISO = new Date().toISOString();

const PORTFOLIO_WITH_SHORT_PUT = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: TODAY_ISO,
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 1,
  defined_risk_count: 0,
  undefined_risk_count: 1,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 100_000, daily_pnl: 0, unrealized_pnl: 0, realized_pnl: 0,
    settled_cash: 100_000, maintenance_margin: 0, excess_liquidity: 100_000,
    buying_power: 400_000, dividends: 0,
  },
  positions: [
    {
      id: 1,
      ticker: "TSLA",
      structure: "Short Put ($400)",
      structure_type: "Short Put",
      risk_profile: "undefined",
      expiry: "2026-05-15",
      contracts: 10,
      direction: "SHORT",
      entry_cost: -15_000, // received $15,000 premium
      max_risk: null,
      market_value: -10_000,
      market_price_is_calculated: false,
      ib_daily_pnl: 5_000,
      entry_date: "2026-05-01",
      kelly_optimal: null,
      target: null,
      stop: null,
      legs: [
        {
          direction: "SHORT",
          contracts: 10,
          type: "Put",
          strike: 400,
          entry_cost: -15_000,
          avg_cost: 1_500, // IB convention: per-contract × 100; positive even for short
          market_price: 10.0,
          market_value: -10_000,
        },
      ],
    },
  ],
};

const ORDERS_BUY_TO_CLOSE_NO_REALIZED_PNL = {
  open_count: 0,
  executed_count: 1,
  open_orders: [],
  executed_orders: [
    {
      execId: "exec-tsla-close-1",
      symbol: "TSLA",
      contract: {
        conId: 999_001,
        symbol: "TSLA",
        secType: "OPT",
        strike: 400,
        right: "P",
        expiry: "20260515",
      },
      side: "BOT",
      quantity: 10,
      avgPrice: 10.0,
      commission: -6.98,
      realizedPNL: null, // ← the actual bug: IB hasn't sent the commission report yet
      time: TODAY_ISO,
      exchange: "SMART",
    },
  ],
  last_sync: TODAY_ISO,
};

async function setup(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_WITH_SHORT_PUT) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_BUY_TO_CLOSE_NO_REALIZED_PNL) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: TODAY_ISO, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
  await page.route("**/api/cash-flows**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows: [], summary: {} }) }),
  );
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, days_until_expiry: 14 }) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
}

test.describe("Today's Executed Orders — buy-to-close P&L fallback", () => {
  test("classifies BOT against existing SHORT leg as CLOSE + computes P&L from portfolio basis", async ({ page }) => {
    await setup(page);
    await page.goto("/orders");

    // The TSLA fill row should appear in Today's Executed Orders
    const tslaRow = page.locator("tr", { hasText: "TSLA" }).first();
    await expect(tslaRow).toBeVisible();

    // Pill: "CLOSE" not "OPEN"
    await expect(tslaRow).toContainText("CLOSE");
    await expect(tslaRow).not.toContainText(/\bOPEN\b/);

    // Description: should reflect the short put we closed
    await expect(tslaRow).toContainText("Short");
    await expect(tslaRow).toContainText("Put");
    await expect(tslaRow).toContainText("$400");

    // Realized P&L = (1500 − 1000) × 10 = $5,000 on a short put closed below entry
    await expect(tslaRow).toContainText(/\+\$5,?000(\.00)?/);
  });
});
