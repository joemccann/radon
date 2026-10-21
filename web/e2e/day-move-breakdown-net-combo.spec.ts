import { expect, test } from "@playwright/test";
import { TRADING_DAY_ISO, freezeToTradingDay } from "./tradingDayClock";

/**
 * Day Move breakdown modal: a combo row shows the NET per-unit close/current
 * (not its first leg), option rows carry a % reading, and a row whose IB
 * daily P&L contradicts the quoted move is flagged IB≠QUOTE.
 */

const SPCX_LEG = (direction: "LONG" | "SHORT", strike: number) => ({
  direction,
  contracts: 200,
  type: "Call",
  strike,
  entry_cost: direction === "LONG" ? 120_000 : -32_200,
  avg_cost: direction === "LONG" ? 600 : 161,
  market_price: direction === "LONG" ? 3.1 : 1.2,
  market_value: direction === "LONG" ? 62_000 : -24_000,
  market_price_is_calculated: false,
});

const PORTFOLIO_MOCK = {
  bankroll: 1_210_801.92,
  peak_value: 1_210_801.92,
  last_sync: TRADING_DAY_ISO,
  total_deployed_pct: 10,
  total_deployed_dollars: 121_050,
  remaining_capacity_pct: 90,
  position_count: 2,
  defined_risk_count: 2,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [
    {
      id: 1,
      ticker: "SPCX",
      structure: "Bull Call Spread $175.0/$200.0",
      structure_type: "Bull Call Spread",
      risk_profile: "defined",
      expiry: "2026-11-13",
      contracts: 200,
      direction: "LONG",
      entry_cost: 87_800,
      max_risk: 87_800,
      market_value: 38_000,
      ib_daily_pnl: 25_232.97,
      legs: [SPCX_LEG("LONG", 175), SPCX_LEG("SHORT", 200)],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-08-03",
    },
    {
      id: 2,
      ticker: "IWO",
      structure: "Long Call $380.0",
      structure_type: "Long Call",
      risk_profile: "defined",
      expiry: "2026-12-18",
      contracts: 50,
      direction: "LONG",
      entry_cost: 33_250,
      max_risk: 33_250,
      market_value: 14_750,
      ib_daily_pnl: -5_081.31,
      legs: [
        {
          direction: "LONG",
          contracts: 50,
          type: "Call",
          strike: 380,
          entry_cost: 33_250,
          avg_cost: 665,
          market_price: 2.95,
          market_value: 14_750,
          market_price_is_calculated: false,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-08-03",
    },
  ],
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 1_210_801.92,
    daily_pnl: 20_151.66,
    unrealized_pnl: -520_168.44,
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

function quote(symbol: string, last: number, close: number) {
  return {
    symbol,
    last,
    lastIsCalculated: false,
    bid: last - 0.05,
    ask: last + 0.05,
    bidSize: 10,
    askSize: 10,
    volume: 0,
    high: null,
    low: null,
    open: null,
    close,
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
  };
}

const PRICE_FIXTURES = {
  SPCX_20261113_175_C: quote("SPCX_20261113_175_C", 3.1, 1.8),
  SPCX_20261113_200_C: quote("SPCX_20261113_200_C", 1.2, 0.5),
  IWO_20261218_380_C: quote("IWO_20261218_380_C", 2.95, 1.7),
};

async function installMockWebSocket(page: import("@playwright/test").Page) {
  await page.addInitScript((priceFixtures: Record<string, unknown>) => {
    class MockWebSocket {
      public static OPEN = 1;
      public url: string;
      public readyState = 0;
      public onopen: ((event: Event) => void) | null = null;
      public onmessage: ((event: MessageEvent<string>) => void) | null = null;
      public onclose: ((event: Event) => void) | null = null;
      public onerror: ((event: Event) => void) | null = null;

      constructor(url: string) {
        this.url = url;
        window.setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.(new Event("open"));
        }, 0);
        window.setTimeout(() => {
          this.emit({ type: "status", ib_connected: true, ib_issue: null, ib_status_message: null, subscriptions: [] });
        }, 10);
        window.setTimeout(() => {
          this.emit({ type: "batch", updates: priceFixtures });
        }, 20);
      }

      send(_message: string) {}

      close() {
        this.readyState = 3;
        this.onclose?.(new Event("close"));
      }

      emit(payload: unknown) {
        this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>);
      }
    }

    // Relay socket only: replacing window.WebSocket wholesale hijacks the dev
    // server's HMR socket and stalls hydration. See
    // feedback_e2e_ws_mock_relay_only_and_44px_button_floor.
    const NativeWebSocket = window.WebSocket;
    const RelayAwareWebSocket = function (url: string | URL, protocols?: string | string[]) {
      return String(url).includes("localhost:8765")
        ? (new MockWebSocket(String(url)) as unknown as WebSocket)
        : new NativeWebSocket(url, protocols);
    } as unknown as typeof WebSocket;
    Object.assign(RelayAwareWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    Object.defineProperty(window, "WebSocket", {
      configurable: true, writable: true, value: RelayAwareWebSocket,
    });
  }, PRICE_FIXTURES);
}

async function stubApis(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  const json = (body: unknown) => (route: import("@playwright/test").Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

  await page.route("**/api/portfolio", json(PORTFOLIO_MOCK));
  await page.route("**/api/orders", json(ORDERS_EMPTY));
  await page.route("**/api/regime", json({ score: 15, level: "LOW", cri: { score: 15 } }));
  await page.route("**/api/flex-token", json({ ok: true, days_until_expiry: 14 }));
  await page.route("**/api/ib-status", json({ connected: true }));
  await page.route(
    "**/api/blotter",
    json({ as_of: TRADING_DAY_ISO, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
}

test("day move modal nets combo quotes, shows option %, and flags IB vs quote disagreement", async ({ page }) => {
  await freezeToTradingDay(page);
  await installMockWebSocket(page);
  await stubApis(page);

  await page.goto("/portfolio");

  const dayMoveCard = page.locator(".metrics-grid-3 .metric-card").filter({
    has: page.locator(".metric-label", { hasText: "Day Move" }),
  });
  await expect(dayMoveCard).toContainText("+$20,152");
  await dayMoveCard.click();

  const modal = page.locator(".pnl-breakdown-modal");
  await expect(modal).toBeVisible();

  const spcxRow = modal.locator("tbody tr").filter({ hasText: "SPCX" });
  await expect(spcxRow).toContainText("$1.30");
  await expect(spcxRow).toContainText("$1.90");
  await expect(spcxRow).not.toContainText("$1.80");
  await expect(spcxRow).toContainText("+97.0%");
  await expect(spcxRow.locator(".pb-quote-flag")).toHaveCount(0);

  const iwoRow = modal.locator("tbody tr").filter({ hasText: "IWO" });
  await expect(iwoRow).toContainText("-$5,081.31");
  const flag = iwoRow.locator(".pb-quote-flag");
  await expect(flag).toHaveText("IB≠QUOTE");
  await expect(flag).toHaveAttribute("title", /quoted move \+\$6,250\.00/);

  await modal.screenshot({ path: "test-results/day-move-breakdown-net-combo.png" });
});
