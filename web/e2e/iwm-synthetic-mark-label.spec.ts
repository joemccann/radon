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
      id: 13,
      ticker: "IWM",
      structure: "Risk Reversal (P$243.0/C$247.0)",
      structure_type: "Risk Reversal",
      risk_profile: "undefined",
      expiry: "2026-03-26",
      contracts: 50,
      direction: "COMBO",
      entry_cost: -579.79,
      market_value: 1800,
      market_price_is_calculated: false,
      ib_daily_pnl: null,
      legs: [
        {
          direction: "LONG",
          contracts: 50,
          type: "Call",
          strike: 247,
          entry_cost: 17285.02,
          avg_cost: 345.7,
          market_price: 3.26,
          market_value: 16300,
          market_price_is_calculated: false,
        },
        {
          direction: "SHORT",
          contracts: 50,
          type: "Put",
          strike: 243,
          entry_cost: 17864.81,
          avg_cost: 357.29,
          market_price: 3.51,
          market_value: 17550,
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
    last: 245.37,
    lastIsCalculated: false,
    bid: 245.36,
    ask: 245.38,
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
    last: 3.26,
    lastIsCalculated: false,
    bid: 3.25,
    ask: 3.28,
    bidSize: 10,
    askSize: 10,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 3.22,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: 245.37,
    timestamp: "2026-03-20T15:00:00.000Z",
  },
  IWM_20260326_243_P: {
    symbol: "IWM_20260326_243_P",
    last: 3.51,
    lastIsCalculated: false,
    bid: 3.00,
    ask: 3.02,
    bidSize: 10,
    askSize: 10,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 3.01,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: 245.37,
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

test("synthetic combo telemetry shows MARK instead of stale LAST for IWM", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-03-20T15:00:00.000Z"));
  await installMockWebSocket(page);
  await stubApis(page);

  await page.goto("/IWM?posId=13&tab=position");

  const hero = page.getByTestId("chain-held-quote");
  await expect(hero).toContainText("MARK");
  await expect(hero).toContainText("$0.26");
  await expect(hero).not.toContainText("-$0.25");

  await expect(page.locator(".position-summary-grid")).toContainText("Mark Price");
  await expect(page.locator(".position-summary-grid")).toContainText("$0.26");
  await expect(page.locator(".position-summary-grid")).not.toContainText("-$0.25");
});
