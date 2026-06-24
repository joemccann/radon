import { expect, test, type Page } from "@playwright/test";

const now = new Date().toISOString();

const PORTFOLIO_MOCK = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: now,
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
  last_sync: now,
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const PRICE_FIXTURES: Record<string, unknown> = {
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
    timestamp: now,
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
    timestamp: now,
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
    timestamp: now,
  },
};

async function installMockWebSocket(page: Page) {
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

async function stubApis(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_MOCK) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, level: "LOW" }) }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: now, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  await page.route("**/api/ticker/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) }),
  );
}

test("mobile combo position shows STOCK|OPTION switcher", async ({ page }) => {
  await installMockWebSocket(page);
  await stubApis(page);

  await page.goto("/IWM?posId=12");
  await expect(page.locator(".cockpit--mobile")).toBeVisible();

  const switcher = page.getByRole("group", { name: "Instrument view" });
  await expect(switcher).toBeVisible();

  const option = page.getByRole("button", { name: "OPTION" });
  const stock = page.getByRole("button", { name: "STOCK" });
  await expect(option).toHaveAttribute("aria-pressed", "true");
  await expect(stock).toHaveAttribute("aria-pressed", "false");
  await expect(option).toHaveClass(/(^|\s)on(\s|$)/);
  await expect(stock).not.toHaveClass(/(^|\s)on(\s|$)/);
  await expect(page.locator(".ckh--mobile")).toContainText("$-0.40");

  await stock.click();
  await expect(stock).toHaveAttribute("aria-pressed", "true");
  await expect(option).toHaveAttribute("aria-pressed", "false");
  await expect(stock).toHaveClass(/(^|\s)on(\s|$)/);
  await expect(option).not.toHaveClass(/(^|\s)on(\s|$)/);
  await expect(page.locator(".ckh--mobile")).toContainText("$244.65");
});
