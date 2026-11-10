import { expect, test } from "@playwright/test";

const PORTFOLIO_MOCK = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-03-20T15:00:00.000Z",
  total_deployed_pct: 1.2,
  total_deployed_dollars: 1_200,
  remaining_capacity_pct: 98.8,
  position_count: 1,
  defined_risk_count: 0,
  undefined_risk_count: 1,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [
    {
      id: 12,
      ticker: "IWM",
      structure: "Risk Reversal (P$243.0/C$247.0)",
      structure_type: "Risk Reversal",
      risk_profile: "undefined",
      expiry: "2026-03-26",
      contracts: 50,
      direction: "COMBO",
      entry_cost: -579.79,
      max_risk: null,
      market_value: 750,
      market_price_is_calculated: false,
      ib_daily_pnl: 1395.64,
      legs: [
        {
          direction: "LONG",
          contracts: 50,
          type: "Call",
          strike: 247,
          entry_cost: 17285.02,
          avg_cost: 346,
          market_price: 3.63,
          market_value: 18150,
          market_price_is_calculated: false,
        },
        {
          direction: "SHORT",
          contracts: 50,
          type: "Put",
          strike: 243,
          entry_cost: 17864.81,
          avg_cost: 357,
          market_price: 3.88,
          market_value: 19400,
          market_price_is_calculated: false,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-19",
    },
  ],
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

const ORDERS_EMPTY = {
  last_sync: "2026-03-20T15:00:00.000Z",
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const PRICE_FIXTURES = {
  IWM: {
    symbol: "IWM",
    last: 244.65,
    lastIsCalculated: false,
    bid: 244.64,
    ask: 244.66,
    bidSize: 10,
    askSize: 10,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 246,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: "2026-03-20T15:00:00.000Z",
  },
  IWM_20260326_247_C: {
    symbol: "IWM_20260326_247_C",
    last: 3.63,
    lastIsCalculated: false,
    bid: 3.4,
    ask: 3.46,
    bidSize: 10,
    askSize: 10,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 3.61,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: 244.65,
    timestamp: "2026-03-20T15:00:00.000Z",
  },
  IWM_20260326_243_P: {
    symbol: "IWM_20260326_243_P",
    last: 3.88,
    lastIsCalculated: false,
    bid: 3.8,
    ask: 3.86,
    bidSize: 10,
    askSize: 10,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 3.84,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: 244.65,
    timestamp: "2026-03-20T15:00:00.000Z",
  },
};

async function installMockWebSocket(page: import("@playwright/test").Page) {
  await page.addInitScript((priceFixtures) => {
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
        for (const symbol of message.symbols ?? []) {
          if (priceFixtures[symbol]) updates[symbol] = priceFixtures[symbol];
        }
        for (const contract of message.contracts ?? []) {
          const expiry = String(contract.expiry).replace(/-/g, "");
          const key = `${String(contract.symbol).toUpperCase()}_${expiry}_${Number(contract.strike)}_${contract.right}`;
          if (priceFixtures[key]) updates[key] = priceFixtures[key];
        }
        if (Object.keys(updates).length > 0) this.emit({ type: "batch", updates });
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
        as_of: "2026-03-20T15:00:00.000Z",
        summary: { realized_pnl: 0 },
        closed_trades: [],
        open_trades: [],
      }),
    }),
  );
  await page.route("**/api/ticker/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({}),
    }),
  );
}

test("IWM ticker detail preserves signed combo leg and order quotes", async ({ page, baseURL }) => {
  await page.clock.setFixedTime(new Date("2026-03-20T15:00:00.000Z"));
  await installMockWebSocket(page);
  await stubApis(page);

  const requests: unknown[] = [];
  let placedBody: Record<string, unknown> | null = null;
  await page.route("**/api/orders/place", async (route) => {
    placedBody = JSON.parse(route.request().postData() ?? "{}");
    requests.push({ url: route.request().url(), method: route.request().method(), body: placedBody });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        orderId: 1001,
        permId: 2002,
        initialStatus: "Submitted",
        message: "placed",
        orders: ORDERS_EMPTY,
      }),
    });
  });

  await page.goto("/IWM?posId=12&tab=position");

  await expect(page.locator(".position-summary-grid")).toContainText("Mark Price");
  await expect(page.locator(".position-summary-grid")).toContainText("-$0.40");

  const shortRow = page.locator(".pos-legs-table tbody tr").filter({ hasText: "SHORT" }).first();
  await expect(shortRow).toContainText("-$3.57");
  await expect(shortRow).toContainText("-$3.83");
  await expect(shortRow).not.toContainText("-$3.88");

  const longRow = page.locator(".pos-legs-table tbody tr").filter({ hasText: "LONG" }).first();
  await expect(longRow).toContainText("$3.46");
  await expect(longRow).toContainText("$3.43");
  await expect(longRow).not.toContainText("$3.63");

  await page.getByTestId("pos-trade-combo").click();

  const strip = page.locator(".spread-price-strip");
  await expect(strip).toContainText("-$0.46");
  await expect(strip).toContainText("-$0.40");
  await expect(strip).toContainText("-$0.34");

  await page.getByRole("button", { name: /MID \$?-0.40/i }).click();

  const ticket = page.getByTestId("position-trade-ticket");
  const input = ticket.locator(".modify-price-input");
  await expect(input).toHaveValue("-0.40");

  expect(requests).toHaveLength(0);
  await ticket.getByRole("button", { name: "Review Order" }).click();
  expect(requests).toHaveLength(0);
  await page.getByRole("button", { name: "Confirm Order" }).click();

  expect(placedBody).not.toBeNull();
  expect(placedBody?.limitPrice).toBe(-0.4);
  expect(requests).toEqual([{ url: new URL("/api/orders/place", baseURL).href, method: "POST", body: { type: "combo", symbol: "IWM", action: "SELL", quantity: 50, limitPrice: -0.4, tif: "DAY", legs: [{ expiry: "20260326", strike: 247, right: "C", action: "BUY", ratio: 1 }, { expiry: "20260326", strike: 243, right: "P", action: "SELL", ratio: 1 }] } }]);
});
