import { expect, test } from "@playwright/test";

const PORTFOLIO = {
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
  exposure: {},
  violations: [],
  risk_budget: {
    clusters: [],
    breaches: [],
    aggregate_exposure: 0,
    insufficient_data: [],
    corr_threshold: 0.7,
    book_budget: 0.025,
  },
  positions: [
    {
      id: 1,
      ticker: "AAOI",
      structure: "Long Put",
      structure_type: "Single",
      risk_profile: "Defined",
      expiry: "2026-03-27",
      contracts: 50,
      direction: "LONG",
      entry_cost: 25_000,
      max_risk: 25_000,
      market_value: null,
      legs: [
        {
          direction: "LONG",
          contracts: 50,
          type: "Put",
          strike: 90,
          entry_cost: 25_000,
          avg_cost: 500,
          market_price: null,
          market_value: null,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-01",
    },
  ],
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

const ORDERS = {
  last_sync: new Date().toISOString(),
  open_orders: [
    {
      orderId: 95,
      permId: 653624857,
      symbol: "AAOI P90",
      contract: {
        conId: 987654,
        symbol: "AAOI",
        secType: "OPT",
        strike: 90,
        right: "P",
        expiry: "2026-03-27",
      },
      action: "SELL",
      orderType: "LMT",
      totalQuantity: 50,
      limitPrice: 5.7,
      auxPrice: null,
      status: "Submitted",
      filled: 0,
      remaining: 50,
      avgFillPrice: null,
      tif: "DAY",
    },
  ],
  executed_orders: [],
  open_count: 1,
  executed_count: 0,
};

async function stubApis(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    class MockWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;
      readyState = MockWebSocket.CONNECTING;
      onopen: ((event?: unknown) => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onclose: ((event?: unknown) => void) | null = null;
      onerror: ((event?: unknown) => void) | null = null;
      constructor() {
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.({});
          this.onmessage?.({ data: JSON.stringify({ type: "status", ib_connected: true, subscriptions: [] }) });
        }, 0);
      }
      send() {}
      close() { this.readyState = MockWebSocket.CLOSED; this.onclose?.({}); }
    }
    // @ts-expect-error test-only replacement
    window.WebSocket = MockWebSocket;
  });
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route(/^https?:\/\/(?!localhost:|127\.0\.0\.1:)/, (route) => route.abort());
  await page.route("**/api/**", (route) => route.fulfill({ status: 503, body: "Unmocked API blocked by test fixture" }));

  await page.route("**/api/portfolio**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO),
    }),
  );

  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS),
    }),
  );

  await page.route("**/api/orders/modify", (route) =>
    route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ error: "Modify not confirmed by refreshed orders" }),
    }),
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
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: true }),
    }),
  );

  await page.route("**/api/regime", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ score: 15, cri: { score: 15 } }),
    }),
  );

  await page.route("**/api/prices", (route) => route.abort());
}

test.describe("Order modify confirmation", () => {
  test("does not enter a fake pending state when modify is not confirmed", async ({ page }) => {
    await stubApis(page);

    await page.goto("/orders");

    const row = page.locator("tbody tr").filter({ hasText: "AAOI" }).first();
    await expect(row).toBeVisible({ timeout: 10_000 });
    await expect(row).toContainText("$5.70");

    await row.getByRole("button", { name: "MODIFY" }).click();

    const modal = page.locator(".modify-dialog");
    await expect(modal).toBeVisible();
    await modal.locator("#modify-price-input").fill("5.55");
    await expect(modal).toContainText("Est. Realized P&L:");
    await expect(modal.getByTestId("order-confirm-estimated-pnl")).toHaveText("$2,750 (+11.0%)");
    await modal.getByRole("button", { name: /modify order/i }).click();

    await expect(row).toContainText("$5.70");
    await expect(row).not.toContainText("Modifying...");
    await expect(row).not.toContainText("PENDING");
  });
});
