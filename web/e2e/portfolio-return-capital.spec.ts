import { expect, test, type Page } from "@playwright/test";

const PORTFOLIO_MOCK = {
  bankroll: 1_300_000,
  peak_value: 1_300_000,
  last_sync: "2026-08-07T18:30:00Z",
  total_deployed_pct: 4.2,
  total_deployed_dollars: 54_600,
  remaining_capacity_pct: 95.8,
  position_count: 2,
  defined_risk_count: 1,
  undefined_risk_count: 1,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  trade_log_dates: {},
  account_summary: {
    net_liquidation: 1_300_000,
    daily_pnl: 0,
    unrealized_pnl: 29_364,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 400_000,
    dividends: 0,
  },
  positions: [
    {
      id: 1,
      ticker: "SPCX",
      structure: "Ratio Risk Reversal 60x30 (P$120.0/C$135.0)",
      structure_type: "Ratio Risk Reversal",
      risk_profile: "undefined",
      expiry: "2026-08-21",
      contracts: 60,
      direction: "COMBO",
      entry_cost: -1_514,
      max_risk: null,
      init_margin_at_entry: 40_000,
      market_value: 26_850,
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-07-01",
      legs: [
        { direction: "LONG", contracts: 60, type: "Call", strike: 135, entry_cost: 48_429, avg_cost: 807.15, market_price: 7, market_value: 42_000 },
        { direction: "SHORT", contracts: 30, type: "Put", strike: 120, entry_cost: 49_943, avg_cost: 1_664.77, market_price: 5.05, market_value: 15_150 },
      ],
    },
    {
      id: 2,
      ticker: "GLD",
      structure: "Long Call $450",
      structure_type: "Long Call",
      risk_profile: "defined",
      expiry: "2027-01-15",
      contracts: 10,
      direction: "LONG",
      entry_cost: 5_000,
      max_risk: 10_000,
      market_value: 6_000,
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-08-07",
      legs: [
        { direction: "LONG", contracts: 10, type: "Call", strike: 450, entry_cost: 5_000, avg_cost: 500, market_price: 6, market_value: 6_000 },
      ],
    },
  ],
};

async function setupMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(PORTFOLIO_MOCK),
  }));
  await page.route("**/api/orders", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ open_orders: [], executed_orders: [], last_sync: PORTFOLIO_MOCK.last_sync }),
  }));
  await page.route("**/api/flex-token", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ ok: true, days_until_expiry: 14 }),
  }));
  await page.route("**/api/ib-status", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ connected: true }),
  }));
  await page.route("**/api/blotter", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ as_of: PORTFOLIO_MOCK.last_sync, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
  }));
  await page.route("**/api/prices**", (route) => route.abort());
}

test("portfolio renders verified Return % and suppresses premium return", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await setupMocks(page);
  await page.goto("/portfolio", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("columnheader", { name: /Return %/ }).first()).toBeVisible();

  const spcxRow = page.locator("tr").filter({ hasText: "SPCX" });
  await expect(spcxRow).toHaveCount(1);
  await expect(spcxRow.locator('[title="Return unavailable: no verified capital basis"]')).toHaveText("—");
  await expect(spcxRow).not.toContainText("1873");

  const gldRow = page.locator("tr").filter({ hasText: "GLD" });
  await expect(gldRow).toHaveCount(1);
  await expect(gldRow.locator('[title*="Return on max risk"]')).toHaveText("+10.0%");

  await testInfo.attach("portfolio-return", {
    body: await page.screenshot({ fullPage: true }),
    contentType: "image/png",
  });
});
