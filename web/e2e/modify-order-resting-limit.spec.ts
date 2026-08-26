import { test, expect } from "@playwright/test";

import { watchPortfolioRequests } from "./portfolio-request-guard";

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
  positions: [],
};

const ORDERS = {
  last_sync: new Date().toISOString(),
  open_orders: [
    {
      orderId: 72,
      permId: 653611397,
      symbol: "AAOI",
      contract: {
        conId: 742392001,
        symbol: "AAOI",
        secType: "OPT",
        strike: 90,
        right: "P",
        expiry: "2026-03-27",
      },
      action: "SELL",
      orderType: "LMT",
      totalQuantity: 50,
      limitPrice: 5.05,
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

const PRICE_FIXTURES = {
  "AAOI_20260327_90_P": {
    symbol: "AAOI_20260327_90_P",
    last: 4.8,
    lastIsCalculated: false,
    bid: 4.3,
    ask: 5.1,
    bidSize: 16,
    askSize: 21,
    volume: 108,
    high: null,
    low: null,
    open: null,
    close: 4.7,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: -0.42,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.68,
    undPrice: 95.19,
    timestamp: new Date().toISOString(),
  },
};

function installMockWebSocket(page: import("@playwright/test").Page) {
  return page.addInitScript((priceFixtures) => {
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
          this.emit({
            type: "status",
            ib_connected: true,
            ib_issue: null,
            ib_status_message: null,
            subscriptions: [],
          });
        }, 0);
      }

      send(raw: string) {
        const message = JSON.parse(raw) as {
          action?: string;
          symbols?: string[];
          contracts?: Array<{ symbol: string; expiry: string; strike: number; right: "C" | "P" }>;
        };
        if (message.action !== "subscribe") return;

        const updates: Record<string, unknown> = {};
        for (const contract of message.contracts ?? []) {
          const expiry = String(contract.expiry).replace(/-/g, "");
          const key = `${String(contract.symbol).toUpperCase()}_${expiry}_${Number(contract.strike)}_${contract.right}`;
          if (priceFixtures[key]) updates[key] = priceFixtures[key];
        }

        if (Object.keys(updates).length > 0) {
          this.emit({ type: "batch", updates });
        }
      }

      close() {
        this.readyState = MockWebSocket.CLOSED;
        this.onclose?.({});
      }

      emit(payload: unknown) {
        this.onmessage?.({ data: JSON.stringify(payload) });
      }
    }

    // @ts-expect-error test-only replacement
    window.WebSocket = MockWebSocket;
  }, PRICE_FIXTURES);
}

function stubApis(page: import("@playwright/test").Page) {
  const portfolioGuard = watchPortfolioRequests(page);

  page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS) }),
  );
  // "**/api/portfolio**" — /orders asks for "/api/portfolio?include=entry-dates"
  // and the bare glob does not match a URL with a query string (T-172).
  page.route("**/api/portfolio**", (route) => {
    portfolioGuard.record(route.request().url());
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) });
  });
  page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );

  return portfolioGuard;
}

test("modify modal shows the resting sell limit as the effective ask", async ({ page }) => {
  await installMockWebSocket(page);
  const portfolioGuard = stubApis(page);

  await page.goto("http://127.0.0.1:3000/orders");

  const row = page.getByRole("row", { name: /AAOI Short \$90 Put 2026-03-27/ });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "MODIFY" }).click();

  const dialog = page.locator(".modify-order-modal");
  await expect(dialog).toContainText(/ASK\s*\$5\.05/);
  await expect(dialog).not.toContainText(/ASK\s*\$5\.10/);

  // No /api/portfolio* request may escape the mock into the real server.
  await portfolioGuard.assertAllRouted();
  expect(portfolioGuard.seen.some((url) => url.includes("include=entry-dates"))).toBe(true);
});
