import { expect, test } from "@playwright/test";

const PORTFOLIO_MOCK = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 1.2,
  total_deployed_dollars: 1_200,
  remaining_capacity_pct: 98.8,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [
    {
      id: 101,
      ticker: "CROX",
      structure: "Bull Call Spread",
      structure_type: "Bull Call Spread",
      risk_profile: "defined",
      expiry: "2026-04-17",
      contracts: 1,
      direction: "LONG",
      entry_cost: 325,
      max_risk: 325,
      market_value: 325,
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-19",
      legs: [
        {
          direction: "LONG",
          contracts: 1,
          type: "Call",
          strike: 80,
          entry_cost: 300,
          avg_cost: 300,
          market_price: 4.5,
          market_value: 450,
        },
        {
          direction: "SHORT",
          contracts: 1,
          type: "Call",
          strike: 95,
          entry_cost: -25,
          avg_cost: -25,
          market_price: 1.25,
          market_value: -125,
        },
      ],
    },
    {
      id: 202,
      ticker: "MU",
      structure: "Short Strangle $1125.0/$1250.0",
      structure_type: "Short Strangle",
      risk_profile: "undefined",
      expiry: "2026-06-26",
      contracts: 5,
      direction: "COMBO",
      entry_cost: -4_565,
      max_risk: null,
      market_value: -9_275,
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-06-26",
      legs: [
        {
          direction: "SHORT",
          contracts: 5,
          type: "Call",
          strike: 1250,
          entry_cost: -6_830,
          avg_cost: -1_366,
          market_price: 0.05,
          market_value: -25,
        },
        {
          direction: "LONG",
          contracts: 5,
          type: "Put",
          strike: 1125,
          entry_cost: 2_265,
          avg_cost: 453,
          market_price: 4.27,
          market_value: 2_135,
        },
      ],
    },
  ],
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 100_000,
    daily_pnl: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 50_000,
    maintenance_margin: 0,
    excess_liquidity: 50_000,
    buying_power: 100_000,
    dividends: 0,
  },
};

const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

async function installMockWebSocket(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    class MockWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;

      url: string;
      readyState = MockWebSocket.CONNECTING;
      onopen: ((event?: unknown) => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onclose: ((event?: unknown) => void) | null = null;
      onerror: ((event?: unknown) => void) | null = null;

      constructor(url: string) {
        this.url = url;
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.({});
          this.onmessage?.({
            data: JSON.stringify({
              type: "status",
              ib_connected: true,
              ib_issue: null,
              ib_status_message: null,
              subscriptions: [],
            }),
          });
        }, 0);
      }

      send() {}

      close() {
        this.readyState = MockWebSocket.CLOSED;
        this.onclose?.({});
      }
    }

    // @ts-expect-error test-only replacement
    window.WebSocket = MockWebSocket;
  });
}

async function stubApis(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio", (route) =>
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
      body: JSON.stringify(ORDERS_EMPTY),
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
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        as_of: new Date().toISOString(),
        summary: { realized_pnl: 0 },
        closed_trades: [],
        open_trades: [],
      }),
    }),
  );
}

test("portfolio spread legs expand without rtLast runtime errors", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await installMockWebSocket(page);
  await stubApis(page);

  await page.goto("/portfolio");

  const croxRow = page.locator("table tbody tr").filter({ hasText: "CROX" }).first();
  await expect(croxRow).toBeVisible();

  await croxRow.getByLabel("Expand legs for CROX").click();

  await expect(page.locator("table tbody tr")).toContainText(["LONG 1x Call $80", "SHORT 1x Call $95"]);
  await expect(page.locator("table tbody tr")).toContainText(["$450", "$125"]);
  expect(pageErrors).not.toContain("rtLast is not defined");
});

test("portfolio leg modal buy-to-cover keeps portfolio scope and close debit P&L", async ({ page }) => {
  await installMockWebSocket(page);
  await stubApis(page);

  await page.goto("/portfolio");

  const muRow = page.locator("table tbody tr").filter({ hasText: "MU" }).first();
  await expect(muRow).toBeVisible();

  await muRow.getByLabel("Expand legs for MU").click();
  await page.getByText("SHORT 5x Call $1250").click();

  const dialog = page.getByRole("dialog", { name: /MU \$1250 Call 2026-06-26/i });
  await expect(dialog).toBeVisible();

  await dialog.locator(".modify-price-input").fill("0.05");
  await dialog.getByRole("button", { name: /place order/i }).click();

  const summary = dialog.locator(".order-confirm-summary");
  await expect(summary).toBeVisible();
  await expect(summary).not.toContainText("Coverage indeterminate");
  await expect(summary).toContainText("Close Debit:");
  await expect(summary).toContainText("$25");
  await expect(summary).toContainText("Est. Realized P&L:");
  await expect(summary).toContainText("$6,805");

  await dialog.screenshot({ path: "/tmp/radon-buy-to-cover-modal.png" });
});
