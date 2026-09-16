import { test, expect } from "@playwright/test";

const PORTFOLIO_WITH_CRM_SPREAD = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 6.12,
  total_deployed_dollars: 6120.21,
  remaining_capacity_pct: 93.88,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [
    {
      id: 8,
      ticker: "CRM",
      structure: "Bull Call Spread $197.5/$200.0",
      structure_type: "Bull Call Spread",
      risk_profile: "defined",
      expiry: "2026-03-20",
      contracts: 50,
      direction: "DEBIT",
      entry_cost: 6120.21,
      max_risk: 6120.21,
      market_value: 4350.0,
      market_price_is_calculated: false,
      ib_daily_pnl: -1918.5,
      legs: [
        {
          direction: "LONG",
          contracts: 50,
          type: "Call",
          strike: 197.5,
          entry_cost: 20785.02,
          avg_cost: 415.70045,
          market_price: 2.33,
          market_value: 11650.0,
          market_price_is_calculated: false,
        },
        {
          direction: "SHORT",
          contracts: 50,
          type: "Call",
          strike: 200.0,
          entry_cost: 14664.81,
          avg_cost: 293.29626,
          market_price: 1.46,
          market_value: 7300.0,
          market_price_is_calculated: false,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-17",
    },
  ],
};

const ORDERS = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const EXPIRATIONS = {
  symbol: "CRM",
  expirations: ["2026-03-20", "2026-04-17"],
};

const CHAIN_STRIKES = {
  symbol: "CRM",
  expiry: "2026-03-20",
  exchange: "SMART",
  strikes: [195, 197.5, 200, 202.5],
  multiplier: "100",
};

const PRICE_FIXTURES = {
  CRM: {
    symbol: "CRM",
    last: 195.3,
    lastIsCalculated: false,
    bid: 195.2,
    ask: 195.4,
    bidSize: 100,
    askSize: 100,
    volume: 1000,
    high: null,
    low: null,
    open: null,
    close: 194.8,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: new Date().toISOString(),
  },
  "CRM_20260320_197.5_C": {
    symbol: "CRM_20260320_197.5_C",
    last: 2.33,
    lastIsCalculated: false,
    bid: 2.2,
    ask: 2.46,
    bidSize: 30,
    askSize: 30,
    volume: 250,
    high: null,
    low: null,
    open: null,
    close: 2.5,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: 0.42,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.34,
    undPrice: 195.3,
    timestamp: new Date().toISOString(),
  },
  "CRM_20260320_200_C": {
    symbol: "CRM_20260320_200_C",
    last: 1.46,
    lastIsCalculated: false,
    bid: 1.35,
    ask: 1.57,
    bidSize: 30,
    askSize: 30,
    volume: 180,
    high: null,
    low: null,
    open: null,
    close: 1.6,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: 0.28,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.31,
    undPrice: 195.3,
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
        for (const symbol of message.symbols ?? []) {
          if (priceFixtures[symbol]) updates[symbol] = priceFixtures[symbol];
        }
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
  page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_WITH_CRM_SPREAD) }),
  );
  page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS) }),
  );
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
  page.route("**/api/ticker/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        uw_info: { name: "Salesforce, Inc.", sector: "Technology", description: "Test" },
        stock_state: {},
        profile: {},
        stats: {},
      }),
    }),
  );
  page.route("**/api/options/expirations*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(EXPIRATIONS) }),
  );
  page.route("**/api/options/chain*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CHAIN_STRIKES) }),
  );
}

test.describe("Chain held-leg pricing", () => {
  test("shows bid/mid/ask for held option legs when expiries arrive dashed from the API", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await installMockWebSocket(page);
    stubApis(page);

    await page.goto("http://127.0.0.1:3000/CRM?tab=chain");

    const detail = page.locator(".ticker-detail-page");
    await detail.waitFor({ timeout: 5_000 });
    await detail.locator(".chain-grid").first().waitFor();

    const shortCallRow = detail.getByRole("row", { name: /\$200\.00/ }).first();
    await expect(shortCallRow).toContainText("$1.35");
    await expect(shortCallRow).toContainText("$1.46");
    await expect(shortCallRow).toContainText("$1.57");

    await shortCallRow.locator(".chain-mid.chain-clickable").first().click();

    const orderBuilder = detail.locator(".order-builder");
    await expect(orderBuilder).toBeVisible();
    await expect(orderBuilder.locator(".order-builder-leg")).toContainText("2026-03-20");
    await expect(orderBuilder.getByRole("button", { name: "BID 1.35" })).toBeVisible();
    await expect(orderBuilder.getByRole("button", { name: "MID 1.46" })).toBeVisible();
    await expect(orderBuilder.getByRole("button", { name: "ASK 1.57" })).toBeVisible();
  });
});
