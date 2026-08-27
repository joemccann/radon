import { expect, test } from "@playwright/test";
import { TRADING_DAY_ISO, freezeToTradingDay } from "./tradingDayClock";

const PORTFOLIO_MOCK = {
  bankroll: 1_089_652.28,
  peak_value: 1_089_652.28,
  last_sync: TRADING_DAY_ISO,
  total_deployed_pct: 3.68,
  total_deployed_dollars: 40_076.51,
  remaining_capacity_pct: 96.32,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
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
      market_value: 34_457.5,
      ib_daily_pnl: -3_405.31,
      legs: [
        {
          direction: "LONG",
          contracts: 77,
          type: "Call",
          strike: 17,
          entry_cost: 40_076.51,
          avg_cost: 520.4741844,
          market_price: 4.475,
          market_value: 34_457.5,
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
  last_sync: TRADING_DAY_ISO,
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const PRICE_FIXTURES = {
  WULF_20270115_17_C: {
    symbol: "WULF_20270115_17_C",
    last: 21.015,
    lastIsCalculated: false,
    bid: 4.2,
    ask: 4.75,
    bidSize: 1644,
    askSize: 13660,
    volume: 0,
    high: null,
    low: null,
    open: null,
    close: 4.78,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: 0.617401944729918,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.934928093906453,
    undPrice: 14.743120193481445,
    timestamp: TRADING_DAY_ISO,
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
        as_of: TRADING_DAY_ISO,
        summary: { realized_pnl: 0 },
        closed_trades: [],
        open_trades: [],
      }),
    }),
  );
}

test("dashboard day move card prefers IB daily P&L for same-day option positions", async ({ page }) => {
  // Registered FIRST: page.addInitScript runs in registration order, so the
  // quote-fixture stamps below must be built under the frozen clock too.
  await freezeToTradingDay(page);
  await installMockWebSocket(page);
  await stubApis(page);

  await page.goto("/portfolio");

  const dayMoveCard = page.locator(".metrics-grid-3 .metric-card").filter({
    has: page.locator(".metric-label", { hasText: "Day Move" }),
  });

  await expect(dayMoveCard).toContainText("-$3,405");
  await expect(dayMoveCard.locator(".metric-value")).toHaveClass(/negative/);

  await dayMoveCard.click();

  const modal = page.locator(".pnl-breakdown-modal");
  await expect(modal).toContainText("WULF");
  await expect(modal).toContainText("-$3,405.31");
  await expect(modal).not.toContainText("+$500.50");
});

test("portfolio row uses the live option market around bid/ask, not a stale WULF last trade", async ({ page }) => {
  // Registered FIRST: page.addInitScript runs in registration order, so the
  // quote-fixture stamps below must be built under the frozen clock too.
  await freezeToTradingDay(page);
  await installMockWebSocket(page);
  await stubApis(page);

  await page.goto("/portfolio");

  const wulfRow = page.locator("table tbody tr").filter({ hasText: "WULF" }).first();
  await expect(wulfRow).toBeVisible();

  const lastPriceCell = wulfRow.locator("td.last-price-cell").last();
  await expect(lastPriceCell).toContainText("C$4.48");

  await expect(wulfRow.locator("td")).toContainText(["-$3,405"]);
  await expect(wulfRow).toContainText("$34,458");
  await expect(wulfRow).not.toContainText("$161,816");
});
