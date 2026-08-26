import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { expect, test } from "@playwright/test";

const MOCK_BACKEND_PORT = 8325;

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

const ORDERS_MOCK = {
  last_sync: new Date().toISOString(),
  open_orders: [
    {
      orderId: 101,
      permId: 9001,
      symbol: "TSLL",
      contract: {
        conId: 5001,
        symbol: "TSLL",
        secType: "STK",
        strike: null,
        right: null,
        expiry: null,
      },
      action: "SELL",
      orderType: "LMT",
      totalQuantity: 500,
      limitPrice: 12.34,
      auxPrice: null,
      status: "Submitted",
      filled: 0,
      remaining: 500,
      avgFillPrice: null,
      tif: "GTC",
    },
  ],
  executed_orders: [],
  open_count: 1,
  executed_count: 0,
};

function respondJson(res: ServerResponse, status: number, body: unknown) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(body));
}

let backendServer: Server | null = null;

test.beforeAll(async () => {
  backendServer = createServer((req: IncomingMessage, res: ServerResponse) => {
    if (req.method === "POST" && req.url === "/orders/cancel") {
      respondJson(res, 502, { detail: "Cancel not confirmed by refreshed IB open orders" });
      return;
    }

    if (req.method === "POST" && req.url === "/orders/refresh") {
      respondJson(res, 200, { status: "ok" });
      return;
    }

    respondJson(res, 404, { detail: "Not found" });
  });

  await new Promise<void>((resolve, reject) => {
    backendServer?.once("error", reject);
    backendServer?.listen(MOCK_BACKEND_PORT, "127.0.0.1", () => resolve());
  });
});

test.afterAll(async () => {
  if (!backendServer) return;
  await new Promise<void>((resolve, reject) => {
    backendServer?.close((error) => {
      if (error) reject(error);
      else resolve();
    });
  });
  backendServer = null;
});

async function stubOrdersPageApis(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_MOCK),
    });
  });

  await page.route("**/api/orders", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_MOCK),
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

test.describe("Orders cancel error propagation", () => {
  test("preserves FastAPI 502 detail through /api/orders/cancel and surfaces it in the UI", async ({ page }) => {
    await stubOrdersPageApis(page);
    await page.goto("/orders");

    const openOrderRow = page.locator("tbody tr").filter({ hasText: "TSLL" });
    await expect(openOrderRow).toBeVisible({ timeout: 10_000 });

    await openOrderRow.getByRole("button", { name: "CANCEL" }).click();
    await expect(page.getByRole("dialog", { name: "Cancel Order" })).toBeVisible();

    const cancelResponse = page.waitForResponse((response) =>
      response.url().includes("/api/orders/cancel") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Cancel Order" }).click();

    const response = await cancelResponse;
    expect(response.status()).toBe(502);
    const errorToast = page.locator(".toast-message").filter({
      hasText: "Cancel not confirmed by refreshed IB open orders",
    });
    await expect(errorToast).toBeVisible();
    await expect(errorToast).toContainText("Cancel not confirmed by refreshed IB open orders");
    await expect(openOrderRow).toBeVisible();
  });
});
