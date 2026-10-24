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
    daily_pnl: null,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 200_000,
    dividends: 0,
  },
};

const ORDERS_RISK_REVERSAL = {
  last_sync: new Date().toISOString(),
  open_orders: [
    {
      orderId: 1001,
      permId: 9001,
      orderRef: "radon-risk-reversal-aapl-1",
      symbol: "AAPL",
      contract: {
        conId: 12001,
        symbol: "AAPL",
        secType: "OPT",
        strike: 150,
        right: "P",
        expiry: "2026-04-17",
      },
      action: "SELL",
      orderType: "LMT",
      totalQuantity: 10,
      limitPrice: 0.95,
      auxPrice: null,
      status: "Submitted",
      filled: 0,
      remaining: 10,
      avgFillPrice: null,
      tif: "DAY",
    },
    {
      orderId: 1002,
      permId: 9002,
      orderRef: "radon-risk-reversal-aapl-1",
      symbol: "AAPL",
      contract: {
        conId: 12002,
        symbol: "AAPL",
        secType: "OPT",
        strike: 165,
        right: "C",
        expiry: "2026-04-17",
      },
      action: "BUY",
      orderType: "LMT",
      totalQuantity: 10,
      limitPrice: 1.15,
      auxPrice: null,
      status: "Submitted",
      filled: 0,
      remaining: 10,
      avgFillPrice: null,
      tif: "DAY",
    },
  ],
  executed_orders: [],
  open_count: 2,
  executed_count: 0,
};

const SMH_PORTFOLIO = {
  ...PORTFOLIO_MOCK,
  position_count: 1,
  defined_risk_count: 1,
  positions: [
    {
      id: 18,
      ticker: "SMH",
      structure: "Bull Put Spread $545.0/$550.0",
      structure_type: "Bull Put Spread",
      risk_profile: "defined",
      expiry: "2026-09-18",
      contracts: 150,
      direction: "COMBO",
      entry_cost: -34_200,
      max_risk: 40_800,
      market_value: -25_950,
      legs: [
        {
          con_id: 545,
          direction: "LONG",
          contracts: 150,
          type: "Put",
          strike: 545,
          entry_cost: 187_050,
          avg_cost: 1_247,
          market_price: 9.6,
          market_value: 144_000,
        },
        {
          con_id: 550,
          direction: "SHORT",
          contracts: 150,
          type: "Put",
          strike: 550,
          entry_cost: 221_250,
          avg_cost: 1_475,
          market_price: 11.33,
          market_price_is_calculated: true,
          market_value: 169_950,
        },
      ],
      ib_daily_pnl: 289,
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-08-01",
    },
  ],
};

const SMH_BAG_ORDER = {
  last_sync: new Date().toISOString(),
  open_orders: [
    {
      orderId: 71,
      permId: 7101,
      symbol: "SMH Spread",
      contract: {
        conId: 0,
        symbol: "SMH",
        secType: "BAG",
        strike: null,
        right: null,
        expiry: null,
        comboLegs: [
          { conId: 545, ratio: 1, action: "BUY", symbol: "SMH", strike: 545, right: "P", expiry: "2026-09-18" },
          { conId: 550, ratio: 1, action: "SELL", symbol: "SMH", strike: 550, right: "P", expiry: "2026-09-18" },
        ],
      },
      action: "SELL",
      orderType: "LMT",
      totalQuantity: 150,
      limitPrice: -1.95,
      auxPrice: null,
      status: "Submitted",
      filled: 0,
      remaining: 150,
      avgFillPrice: null,
      tif: "GTC",
    },
  ],
  executed_orders: [],
  open_count: 1,
  executed_count: 0,
};

async function stubApis(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_MOCK),
    });
  });

  await page.route("**/api/orders", (route) => {
    const method = route.request().method();
    if (method === "POST") {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(ORDERS_RISK_REVERSAL),
      });
      return;
    }

    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_RISK_REVERSAL),
    });
  });

  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        as_of: new Date().toISOString(),
        summary: {
          closed_trades: 0,
          open_trades: 0,
          total_commissions: 0,
          realized_pnl: 0,
        },
        closed_trades: [],
        open_trades: [],
      }),
    }),
  );

  await page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: true }),
    }),
  );

  await page.route("**/api/prices", (route) => route.abort());
}

async function stubSmhApis(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SMH_PORTFOLIO) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SMH_BAG_ORDER) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        as_of: new Date().toISOString(),
        summary: { closed_trades: 0, open_trades: 0, total_commissions: 0, realized_pnl: 0 },
        closed_trades: [],
        open_trades: [],
      }),
    }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  await page.route("**/api/risk-free-rate", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rate: 0.05, source: "FRED:DFF", stale: false }) }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
}

test.describe("Orders open-order combo rendering", () => {
  test("combines short put and long call as a risk reversal row and opens combo modify", async ({ page }) => {
    await stubApis(page);
    await page.goto("/orders");

    const riskReversalRow = page
      .locator("tbody tr")
      .filter({ hasText: "AAPL" })
      .filter({ hasText: "Risk Reversal" });

    await expect(riskReversalRow).toBeVisible({ timeout: 10_000 });
    await expect(riskReversalRow).toContainText("COMBO");
    await expect(riskReversalRow).toContainText("Short Put 150");
    await expect(riskReversalRow).toContainText("Long Call 165");
    await expect(riskReversalRow.getByRole("button", { name: "CANCEL ALL" })).toBeVisible();

    const modifyButton = riskReversalRow.getByRole("button", { name: "MODIFY" });
    await expect(modifyButton).toBeEnabled();
    await modifyButton.click();

    const modal = page.locator(".modify-dialog");
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("Edit Legs");
    await expect(modal.locator("#modify-quantity-input")).toHaveValue("10");
    await expect(modal.locator("#modify-leg-0-strike")).toHaveValue("150");
    await expect(modal.locator("#modify-leg-1-strike")).toHaveValue("165");
  });

  test("uses the same calculated combo mark on Orders and Portfolio", async ({ page }, testInfo) => {
    await stubSmhApis(page);

    await page.goto("/orders");

    const orderRow = page.getByTestId("open-order-row-71-7101");
    await expect(orderRow).toBeVisible({ timeout: 10_000 });
    await expect(orderRow).toContainText("$-1.95");
    const orderLast = orderRow.locator("td.last-price-cell");
    await expect(orderLast).toHaveText("C$-1.73");
    const orderLastText = await orderLast.textContent();

    const ordersShot = testInfo.outputPath("smh-orders-price.png");
    await page.getByTestId("open-orders-table").screenshot({ path: ordersShot });
    await testInfo.attach("SMH Orders combo price", { path: ordersShot, contentType: "image/png" });

    await page.goto("/portfolio");

    const positionRow = page.locator("table.position-table-sticky tbody tr").filter({ hasText: "SMH" }).first();
    await expect(positionRow).toBeVisible({ timeout: 10_000 });
    const positionLast = positionRow.locator("td.last-price-cell").last();
    await expect(positionLast).toHaveText("C$-1.73");
    expect(await positionLast.textContent()).toBe(orderLastText);

    const portfolioShot = testInfo.outputPath("smh-portfolio-price.png");
    await page.locator("table.position-table-sticky").first().screenshot({ path: portfolioShot });
    await testInfo.attach("SMH Portfolio combo price", { path: portfolioShot, contentType: "image/png" });
  });
});
