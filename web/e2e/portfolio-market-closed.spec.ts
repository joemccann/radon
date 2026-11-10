import { expect, test } from "@playwright/test";

const PORTFOLIO_MOCK_CLOSED = {
  bankroll: 1_131_051.65,
  peak_value: 1_131_051.65,
  last_sync: "2026-03-22T09:00:00Z",
  total_deployed_pct: 5.78,
  total_deployed_dollars: 2_891.57,
  remaining_capacity_pct: 94.22,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [],
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 1_131_051.65,
    daily_pnl: -17_071.27,
    unrealized_pnl: -212_251.69,
    realized_pnl: -6_835.27,
    settled_cash: -14_654.04,
    maintenance_margin: 513_065.33,
    excess_liquidity: 185_943.44,
    buying_power: 743_773.78,
    dividends: 910.0,
  },
};

const ORDERS_EMPTY = {
  last_sync: "2026-03-22T09:00:00Z",
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

async function freezeToClosedWeekend(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    const fixedNow = new Date("2026-03-22T12:00:00Z").valueOf();
    const RealDate = Date;
    class MockDate extends RealDate {
      constructor(...args: ConstructorParameters<typeof Date>) {
        if (args.length === 0) {
          super(fixedNow);
          return;
        }
        super(...args);
      }
      static now() {
        return fixedNow;
      }
    }
    Object.defineProperty(window, "Date", {
      value: MockDate,
      configurable: true,
      writable: true,
    });
  });
}

async function setupMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await freezeToClosedWeekend(page);

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_MOCK_CLOSED),
    }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_EMPTY),
    }),
  );
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, days_until_expiry: 14 }),
    }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: false }),
    }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: "2026-03-22T09:00:00Z", summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
}

test.describe("/portfolio closed-market load", () => {
  test("renders cached account metrics instead of awaiting sync forever", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");

    await expect(page.locator(".metric-card", { hasText: "Net Liquidation" }).first()).toContainText("1,131,051.65");
    const dayPnl = page.locator(".metric-card", { hasText: "Day P&L" }).first();
    await expect(dayPnl).toContainText("MARKET CLOSED");
    await expect(dayPnl).not.toContainText("-$17,071.27");
    await expect(page.locator(".metric-card", { hasText: "Dividends" }).first()).toContainText("$910.00");
    await expect(page.getByText("AWAITING SYNC")).toHaveCount(0);
  });
});
