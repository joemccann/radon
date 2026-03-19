import { expect, test } from "@playwright/test";

const PORTFOLIO_MOCK = {
  bankroll: 1_089_652.28,
  peak_value: 1_089_652.28,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 3.68,
  total_deployed_dollars: 27_612.88,
  remaining_capacity_pct: 96.32,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [
    {
      id: 8,
      ticker: "CROX",
      structure: "Bull Call Spread $82.5/$95.0",
      structure_type: "Bull Call Spread",
      risk_profile: "defined",
      expiry: "2026-04-17",
      contracts: 163,
      direction: "DEBIT",
      entry_cost: 27_612.88,
      max_risk: 27_612.88,
      market_value: 26_895,
      market_price_is_calculated: false,
      ib_daily_pnl: 1_083.95,
      legs: [
        {
          direction: "LONG",
          contracts: 163,
          type: "Call",
          strike: 82.5,
          entry_cost: 33_529.17,
          avg_cost: 205.70045,
          market_price: 1.925,
          market_value: 31_377.5,
          market_price_is_calculated: false,
        },
        {
          direction: "SHORT",
          contracts: 163,
          type: "Call",
          strike: 95,
          entry_cost: 5_916.29,
          avg_cost: 36.29626,
          market_price: 0.275,
          market_value: 4_482.5,
          market_price_is_calculated: false,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-19",
    },
  ],
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 1_089_652.28,
    daily_pnl: -58_090.38,
    unrealized_pnl: -374_253.59,
    realized_pnl: 0,
    settled_cash: 206_956.63,
    maintenance_margin: 248_269.61,
    excess_liquidity: 474_890.55,
    buying_power: 1_899_562.19,
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

const PRICE_FIXTURES = {
  CROX: {
    symbol: "CROX",
    last: 77.96,
    lastIsCalculated: false,
    bid: 77.9,
    ask: 78.02,
    bidSize: 10,
    askSize: 10,
    volume: 1_000,
    high: null,
    low: null,
    open: null,
    close: 73.81,
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
  "CROX_20260417_82.5_C": {
    symbol: "CROX_20260417_82.5_C",
    last: 7.8,
    lastIsCalculated: false,
    bid: 1.85,
    ask: 2.0,
    bidSize: 40,
    askSize: 40,
    volume: 0,
    high: null,
    low: null,
    open: null,
    close: 2.0,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: 77.96,
    timestamp: new Date().toISOString(),
  },
  "CROX_20260417_95_C": {
    symbol: "CROX_20260417_95_C",
    last: 2.55,
    lastIsCalculated: false,
    bid: 0.22,
    ask: 0.33,
    bidSize: 40,
    askSize: 40,
    volume: 0,
    high: null,
    low: null,
    open: null,
    close: 0.39,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: 77.96,
    timestamp: new Date().toISOString(),
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

test("portfolio row uses guarded spread marks for CROX instead of stale leg last trades", async ({ page }) => {
  await installMockWebSocket(page);
  await stubApis(page);

  await page.goto("http://127.0.0.1:3000/portfolio");

  const croxRow = page.locator("table tbody tr").filter({ hasText: "CROX" }).first();
  await expect(croxRow).toBeVisible();

  const cells = croxRow.locator("td");
  await expect(cells.nth(4)).toContainText("$77.96");
  await expect(cells.nth(6)).toContainText("C$1.65");
  await expect(cells.nth(9)).toContainText("$27,613");
  await expect(cells.nth(10)).toContainText("$26,895");
  await expect(croxRow).not.toContainText("$5.25");
  await expect(croxRow).not.toContainText("$85,575");
});
