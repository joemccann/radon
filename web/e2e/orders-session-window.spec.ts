/**
 * E2E: /orders session chips (RTH vs EXT) on the desktop open-orders table.
 */
import { test, expect, type Page } from "@playwright/test";

const OPEN_NOW = new Date("2026-07-09T15:00:00.000Z"); // 11:00 ET Thursday

const PORTFOLIO = {
  bankroll: 1_500_000,
  peak_value: 1_500_000,
  last_sync: OPEN_NOW.toISOString(),
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
      secType: "STK",
      strike: null,
      right: null,
      expiry: null,
    },
    action: "BUY",
    orderType: "LMT",
    totalQuantity,
    limitPrice: 100,
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
  await page.route("**/api/portfolio**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/orders**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(orders) }),
  );
  await page.route("**/api/blotter", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: OPEN_NOW.toISOString(), closed_trades: [], open_trades: [], summary: {} }),
    }),
  );
  await page.route("**/api/cash-flows*", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ rows: [], count: 0, summary: {} }),
    }),
  );
  await page.route("**/api/regime", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15 }) }),
  );
  await page.route("**/api/ib-status", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
}

test.describe("/orders session window", () => {
  test("desktop chips distinguish option RTH from TQQQ EXT", async ({ page }) => {
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
          symbol: "AAPL",
          contract: {
            conId: 10,
            symbol: "AAPL",
            secType: "OPT",
            strike: 200,
            right: "C",
            expiry: "2026-08-21",
          },
        }),
        makeOpenOrder({
          orderId: 2,
          permId: 1002,
          tif: "GTC",
          outsideRth: true,
          symbol: "TQQQ",
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

    const optionRow = page.getByTestId("open-order-row-1-1001");
    const tqqqRow = page.getByTestId("open-order-row-2-1002");
    await expect(optionRow).toBeVisible();
    await expect(tqqqRow).toBeVisible();

    const optionChip = optionRow.getByTestId("order-session-chip");
    const tqqqChip = tqqqRow.getByTestId("order-session-chip");
    await expect(optionChip).toHaveAttribute("data-session", "rth-only");
    await expect(optionChip).toHaveText("RTH");
    await expect(tqqqChip).toHaveAttribute("data-session", "extended");
    await expect(tqqqChip).toHaveText("EXT");

    await expect(optionRow).toContainText("DAY");
    await expect(tqqqRow).toContainText("GTC");
    await expect(tqqqRow).toHaveClass(/open-order-row--ext/);

    await expect(page.getByTestId("open-orders-rth-count")).toHaveText("1 RTH");
    await expect(page.getByTestId("open-orders-ext-count")).toHaveText("1 EXT");

    const strip = page.getByTestId("orders-command-strip");
    await expect(strip.getByTestId("open-orders-rth-count")).toHaveCount(0);
    await expect(strip.getByTestId("open-orders-ext-count")).toHaveCount(0);
  });

  // 2026-09-01 TQQQ flatten: IB reports PreSubmitted for extended-eligible
  // equity orders that ARE live in the extended session; the table called
  // them QUEUED for the whole after-hours window. While the extended window
  // is live the status must read Working (chip EXT LIVE); once the session
  // closes, Queued is the honest label and the EXT chip names the eligibility.
  const EXTENDED_NOW = new Date("2026-08-27T21:30:00.000Z"); // 17:30 ET Thursday
  const CLOSED_NOW = new Date("2026-08-28T01:00:00.000Z"); // 21:00 ET Thursday

  function tqqqPreSubmittedClose() {
    return {
      last_sync: EXTENDED_NOW.toISOString(),
      open_count: 1,
      executed_count: 0,
      executed_orders: [],
      open_orders: [
        makeOpenOrder({
          orderId: 3,
          permId: 1003,
          action: "SELL",
          tif: "DAY",
          outsideRth: true,
          status: "PreSubmitted",
          symbol: "TQQQ",
          limitPrice: 68.8,
          contract: {
            conId: 30,
            symbol: "TQQQ",
            secType: "STK",
            strike: null,
            right: null,
            expiry: null,
          },
        }),
      ],
    };
  }

  test("a PreSubmitted EXT stock close reads Working while after hours is live", async ({ page }) => {
    await page.clock.install({ time: EXTENDED_NOW });
    await stubOrdersPage(page, tqqqPreSubmittedClose());

    await page.goto("/orders");

    const row = page.getByTestId("open-order-row-3-1003");
    await expect(row).toBeVisible();
    await expect(row).toContainText("Working");
    await expect(row).not.toContainText("Queued");
    await expect(row.getByTestId("order-session-chip")).toHaveText("EXT LIVE");
  });

  test("the same order reads Queued once the extended session has closed", async ({ page }) => {
    await page.clock.install({ time: CLOSED_NOW });
    await stubOrdersPage(page, tqqqPreSubmittedClose());

    await page.goto("/orders");

    const row = page.getByTestId("open-order-row-3-1003");
    await expect(row).toBeVisible();
    await expect(row).toContainText("Queued");
    await expect(row.getByTestId("order-session-chip")).toHaveText("EXT");
  });
});
