import { expect, test } from "@playwright/test";

const PORTFOLIO = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 2,
  total_deployed_dollars: 2000,
  remaining_capacity_pct: 98,
  position_count: 1,
  defined_risk_count: 0,
  undefined_risk_count: 1,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [
    {
      id: 16,
      ticker: "PLTR",
      structure: "Risk Reversal (P$152.5/C$155.0)",
      structure_type: "Risk Reversal",
      risk_profile: "undefined",
      expiry: "2026-03-27",
      contracts: 20,
      direction: "COMBO",
      entry_cost: -1571.92,
      max_risk: null,
      market_value: -320,
      market_price_is_calculated: false,
      ib_daily_pnl: null,
      legs: [
        {
          direction: "LONG",
          contracts: 20,
          type: "Call",
          strike: 155,
          entry_cost: 5034.01,
          avg_cost: 251.7,
          market_price: 2.82,
          market_value: 5640,
          market_price_is_calculated: false,
        },
        {
          direction: "SHORT",
          contracts: 20,
          type: "Put",
          strike: 152.5,
          entry_cost: 6605.93,
          avg_cost: 330.29,
          market_price: 2.98,
          market_value: 5960,
          market_price_is_calculated: false,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-24",
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
  symbol: "PLTR",
  expirations: ["20260327", "20260417"],
};

const CHAIN_20260327 = {
  symbol: "PLTR",
  expiry: "20260327",
  exchange: "SMART",
  strikes: [147, 148, 149, 150, 152.5, 155, 157.5],
  multiplier: "100",
};

const CHAIN_20260417 = {
  symbol: "PLTR",
  expiry: "20260417",
  exchange: "SMART",
  strikes: [147, 148, 149, 150, 152.5, 155, 157.5],
  multiplier: "100",
};

const PRICE_FIXTURES = {
  PLTR: {
    symbol: "PLTR",
    last: 153.1,
    lastIsCalculated: false,
    bid: 153.05,
    ask: 153.15,
    bidSize: 100,
    askSize: 100,
    volume: 1000,
    high: null,
    low: null,
    open: null,
    close: 151.5,
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
  PLTR_20260327_150_P: {
    symbol: "PLTR_20260327_150_P",
    last: 3.42,
    lastIsCalculated: false,
    bid: 3.3,
    ask: 3.55,
    bidSize: 50,
    askSize: 75,
    volume: 120,
    high: null,
    low: null,
    open: null,
    close: 3.1,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: -0.35,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.48,
    undPrice: 153.1,
    timestamp: new Date().toISOString(),
  },
  "PLTR_20260327_152.5_P": {
    symbol: "PLTR_20260327_152.5_P",
    last: 4.2,
    lastIsCalculated: false,
    bid: 4.1,
    ask: 4.3,
    bidSize: 50,
    askSize: 75,
    volume: 100,
    high: null,
    low: null,
    open: null,
    close: 4.0,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: -0.45,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.5,
    undPrice: 153.1,
    timestamp: new Date().toISOString(),
  },
  PLTR_20260327_155_C: {
    symbol: "PLTR_20260327_155_C",
    last: 4.8,
    lastIsCalculated: false,
    bid: 4.7,
    ask: 4.9,
    bidSize: 50,
    askSize: 75,
    volume: 100,
    high: null,
    low: null,
    open: null,
    close: 4.5,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: 0.48,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.5,
    undPrice: 153.1,
    timestamp: new Date().toISOString(),
  },
  "PLTR_20260417_150_P": {
    symbol: "PLTR_20260417_150_P",
    last: null,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
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
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
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
        uw_info: { name: "Palantir Technologies Inc.", sector: "Technology", description: "Test" },
        stock_state: {},
        profile: {},
        stats: {},
      }),
    }),
  );
  page.route("**/api/options/expirations*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(EXPIRATIONS) }),
  );
  page.route("**/api/options/chain*", async (route) => {
    const url = new URL(route.request().url());
    const expiry = url.searchParams.get("expiry");
    const body = expiry === "20260327" ? CHAIN_20260327 : CHAIN_20260417;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}

test.describe("PLTR chain position focus", () => {
  test("deep-linked chain view uses the position expiry and shows the nearby $150 strike row", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await installMockWebSocket(page);
    stubApis(page);

    await page.goto("http://127.0.0.1:3000/PLTR?posId=16&tab=chain");

    const detail = page.locator(".ticker-detail-page").last();
    await detail.locator(".chain-grid").first().waitFor();

    const expirySelect = detail.locator(".chain-expiry-select").first();
    await expect(expirySelect).toHaveValue("20260327");

    const row150 = detail.getByRole("row", { name: /\$150\.00/ }).first();
    await expect(row150).toContainText("$3.30");
    await expect(row150).toContainText("$3.42");
    await expect(row150).toContainText("$3.55");
  });
});
