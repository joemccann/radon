import { test, expect } from "@playwright/test";
import { TRADING_DAY_ISO, freezeToTradingDay } from "./tradingDayClock";

const PORTFOLIO = {
  bankroll: 1_098_051.01,
  peak_value: 1_098_051.01,
  last_sync: TRADING_DAY_ISO,
  total_deployed_pct: 3.65,
  total_deployed_dollars: 40_076.51,
  remaining_capacity_pct: 96.35,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [
    {
      id: 23,
      ticker: "WULF",
      structure: "Long Call",
      structure_type: "Long Call",
      risk_profile: "defined",
      expiry: "2027-01-15",
      contracts: 77,
      direction: "LONG",
      entry_cost: 40_076.51,
      max_risk: 40_076.51,
      market_value: 34_650.0,
      market_price_is_calculated: false,
      ib_daily_pnl: -3_688.02,
      legs: [
        {
          direction: "LONG",
          contracts: 77,
          type: "Call",
          strike: 17,
          entry_cost: 40_076.51,
          avg_cost: 520.4741844,
          market_price: 4.5,
          market_value: 34_650.0,
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
    net_liquidation: 1_098_051.01,
    daily_pnl: -3_688.02,
    unrealized_pnl: -5_426.51,
    realized_pnl: 0,
    settled_cash: 206_956.63,
    maintenance_margin: 247_662.16,
    excess_liquidity: 476_727.23,
    buying_power: 1_906_908.93,
    dividends: 0,
  },
};

const ORDERS = {
  last_sync: TRADING_DAY_ISO,
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const PRICE_FIXTURES = {
  WULF: {
    symbol: "WULF",
    last: 12.4,
    lastIsCalculated: false,
    bid: 12.35,
    ask: 12.45,
    bidSize: 10,
    askSize: 10,
    volume: 1000,
    high: null,
    low: null,
    open: null,
    close: 12.1,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: TRADING_DAY_ISO,
  },
  WULF_20270115_17_C: {
    symbol: "WULF_20270115_17_C",
    last: 4.5,
    lastIsCalculated: false,
    bid: 4.45,
    ask: 4.55,
    bidSize: 12,
    askSize: 14,
    volume: 180,
    high: null,
    low: null,
    open: null,
    close: 4.41,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: 0.52,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.81,
    undPrice: 12.4,
    timestamp: TRADING_DAY_ISO,
  },
};

function installMockWebSocket(page: import("@playwright/test").Page) {
  return page.addInitScript((priceFixtures) => {
    // window.WebSocket is a GLOBAL override, so a naive replacement also stands
    // in for Next's own Turbopack HMR client socket (a DIFFERENT URL), not only
    // the app's price-relay socket (usePrices/IBStatusContext/TickerSearch all
    // dial resolveRealtimeWebSocketUrl() -> ws://localhost:8765 in this env).
    // Root-caused 2026-08-08 via an A/B diagnostic: with a global mock, Next's
    // dev client assigns `.onopen` (property-style — fine) but then calls
    // `socket.addEventListener(...)` from inside that handler for its own
    // listeners; a property-only mock threw "socket.addEventListener is not a
    // function" there, uncaught, from the mock's own setTimeout callback —
    // which silently wedged Fast Refresh/HMR wiring for the rest of the page
    // closely enough that usePortfolio's mount-effect fetch of /api/portfolio
    // never ran (confirmed: instrumenting window.fetch showed zero calls for
    // 15s+) and the page hung forever on "Waiting for portfolio data...".
    // Removing only the WebSocket override made /api/portfolio fire normally,
    // isolating the global override (not the app or the route stubs) as the
    // cause. Fix: keep the mock EventTarget-based (real addEventListener) AND
    // scope it to the relay URL only — every other WebSocket (Next's HMR
    // client included) goes through the real, unmodified constructor.
    const RealWebSocket = window.WebSocket;

    class MockWebSocket extends EventTarget {
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
        super();
        this.url = url;
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.({});
          this.dispatchEvent(new Event("open"));
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
        this.dispatchEvent(new Event("close"));
      }

      emit(payload: unknown) {
        const data = JSON.stringify(payload);
        this.onmessage?.({ data });
        this.dispatchEvent(new MessageEvent("message", { data }));
      }
    }

    // Passthrough wrapper: only the IB relay URL gets the mock. Anything else
    // (Next's HMR socket, any other future WS consumer) gets the real thing.
    class RelayScopedWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;
      constructor(url: string, protocols?: string | string[]) {
        if (typeof url === "string" && url.includes(":8765")) {
          return new MockWebSocket(url) as unknown as RelayScopedWebSocket;
        }
        return new RealWebSocket(url, protocols) as unknown as RelayScopedWebSocket;
      }
    }

    // @ts-expect-error test-only replacement
    window.WebSocket = RelayScopedWebSocket;
  }, PRICE_FIXTURES);
}

// Each page.route() registration is itself async (it round-trips to install
// the interception in the browser context) and must be awaited before goto()
// fires the page's own requests — otherwise the first load of /api/portfolio
// can race ahead of the stub and fall through to a real (failing) fetch,
// which is why the page was hanging on "Waiting for portfolio data...".
async function stubApis(page: import("@playwright/test").Page) {
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: TRADING_DAY_ISO, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
}

test("portfolio day move prefers IB daily P&L over positive mark-to-close math for same-day WULF position", async ({ page }) => {
  // Registered FIRST: page.addInitScript runs in registration order, so the
  // quote-fixture stamps below must be built under the frozen clock too.
  await freezeToTradingDay(page);
  await installMockWebSocket(page);
  await stubApis(page);

  // Relative path through baseURL (playwright.config.ts derives it from
  // PLAYWRIGHT_PORT) — a hardcoded :3000 origin here silently pointed at
  // whatever (or nothing) was running on that port instead of this run's
  // dev server, which is how ".metric-card" ended up timing out.
  await page.goto("/portfolio");

  const todayPnlRow = page.locator(".metrics-grid-3").filter({ hasText: "Day Move" }).first();
  await expect(todayPnlRow).toContainText("Day Move");
  await expect(todayPnlRow).toContainText("-$3,688");
  await expect(todayPnlRow).toContainText("Total");
  await expect(todayPnlRow).toContainText("-$3,688");

  await page.locator(".metric-card", { hasText: "Day Move" }).first().click();
  const modal = page.locator(".modal-content");
  await expect(modal).toBeVisible();
  await expect(modal).toContainText("IB reqPnLSingle");
  await expect(modal).toContainText("WULF");
  await expect(modal).toContainText("$4.41");
  await expect(modal).toContainText("$4.50");
  await expect(modal).toContainText("-$3,688.02");
});
