import { expect, test, type Page } from "@playwright/test";

function price(symbol: string, last: number, bid: number | null, ask: number | null) {
  return {
    symbol,
    last,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 100,
    askSize: 100,
    volume: 1_000,
    high: null,
    low: null,
    open: null,
    close: last,
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
  };
}

async function installMockWebSocket(page: Page) {
  await page.addInitScript(() => {
    class MockWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;
      readyState = MockWebSocket.CONNECTING;
      onopen: ((event?: unknown) => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onclose: ((event?: unknown) => void) | null = null;
      listeners: Record<string, Array<(event?: unknown) => void>> = {
        open: [],
        message: [],
        close: [],
        error: [],
      };
      constructor() {
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.emit("open", {});
          this.emit("message", {
            data: JSON.stringify({
              type: "batch",
              updates: {
                AVGO: {
                  symbol: "AVGO",
                  last: 1200,
                  lastIsCalculated: false,
                  bid: 1199.5,
                  ask: 1200.5,
                  bidSize: 100,
                  askSize: 100,
                  volume: 1_000_000,
                  high: null,
                  low: null,
                  open: null,
                  close: 1195,
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
                AVGO_20260626_1200_C: {
                  symbol: "AVGO_20260626_1200_C",
                  last: 10.33,
                  lastIsCalculated: false,
                  bid: 10.2,
                  ask: 10.46,
                  bidSize: 100,
                  askSize: 100,
                  volume: 500,
                  high: null,
                  low: null,
                  open: null,
                  close: 10.1,
                  week52High: null,
                  week52Low: null,
                  avgVolume: null,
                  delta: 0.5,
                  gamma: null,
                  theta: null,
                  vega: null,
                  impliedVol: 0.25,
                  undPrice: 1200,
                  timestamp: new Date().toISOString(),
                },
                AVGO_20260626_1250_C: {
                  symbol: "AVGO_20260626_1250_C",
                  last: 5.75,
                  lastIsCalculated: false,
                  bid: 5.6,
                  ask: 5.9,
                  bidSize: 100,
                  askSize: 100,
                  volume: 500,
                  high: null,
                  low: null,
                  open: null,
                  close: 5.6,
                  week52High: null,
                  week52Low: null,
                  avgVolume: null,
                  delta: 0.3,
                  gamma: null,
                  theta: null,
                  vega: null,
                  impliedVol: 0.25,
                  undPrice: 1200,
                  timestamp: new Date().toISOString(),
                },
              },
            }),
          });
        }, 0);
      }
      addEventListener(type: string, listener: (event?: unknown) => void) {
        this.listeners[type] ??= [];
        this.listeners[type].push(listener);
      }
      removeEventListener(type: string, listener: (event?: unknown) => void) {
        this.listeners[type] = (this.listeners[type] ?? []).filter((item) => item !== listener);
      }
      emit(type: "open" | "message" | "close" | "error", event: unknown) {
        if (type === "open") this.onopen?.(event);
        if (type === "message") this.onmessage?.(event as { data: string });
        if (type === "close") this.onclose?.(event);
        for (const listener of this.listeners[type] ?? []) listener(event);
      }
      send() {}
      close() {
        this.readyState = MockWebSocket.CLOSED;
        this.emit("close", {});
      }
    }
    // @ts-expect-error test-only websocket replacement
    window.WebSocket = MockWebSocket;
  });
}

async function stubApis(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = (body: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/portfolio") {
      return json({
        bankroll: 100_000,
        peak_value: 100_000,
        last_sync: new Date().toISOString(),
        positions: [],
        total_deployed_pct: 0,
        total_deployed_dollars: 0,
        remaining_capacity_pct: 100,
        position_count: 0,
        defined_risk_count: 0,
        undefined_risk_count: 0,
        avg_kelly_optimal: null,
        account_summary: {
          net_liquidation: 1_000_000,
          daily_pnl: 0,
          unrealized_pnl: 0,
          realized_pnl: 0,
          settled_cash: 500_000,
          maintenance_margin: 0,
          excess_liquidity: 500_000,
          buying_power: 2_000_000,
          dividends: 0,
          available_funds: 500_000,
        },
      });
    }
    if (path === "/api/orders") return json({ open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 });
    if (path === "/api/service-health") return json({ services: [] });
    if (path === "/api/profile") return json({ username: "Operator" });
    if (path === "/api/watchlist") return json({ symbols: [] });
    if (path === "/api/flex-token") return json({ remaining: 240 });
    if (path === "/api/ib/ws-ticket") return json({ ticket: "test" });
    if (path === "/api/prices") {
      return json({
        prices: {
          AVGO: price("AVGO", 1200, 1199.5, 1200.5),
          AVGO_20260626_1200_C: price("AVGO_20260626_1200_C", 10.33, 10.2, 10.46),
          AVGO_20260626_1250_C: price("AVGO_20260626_1250_C", 5.75, 5.6, 5.9),
        },
      });
    }
    if (path === "/api/regime") return json({ score: 15, cri: { score: 15 } });
    if (path === "/api/risk-free-rate") return json({ rate: 0 });
    if (path === "/api/ticker/info") return json({ stock_state: {}, uw_info: {}, profile: {}, stats: {} });
    if (path === "/api/options/expirations") return json({ symbol: "AVGO", expirations: ["20260626"] });
    if (path === "/api/options/chain") {
      return json({ symbol: "AVGO", expiry: "20260626", strikes: [1200, 1250] });
    }
    if (path === "/api/short-availability/AVGO") {
      return json({
        ticker: "AVGO",
        shortable: true,
        difficulty: null,
        shortable_shares: 1_000_000,
        fee_rate: null,
        rebate_rate: null,
        source: "ib",
        as_of: "2026-06-24T19:00:00Z",
        missing: false,
      });
    }
    return json({});
  });
}

test.describe("order margin impact unavailable state", () => {
  test("ratio call spread confirm panel shows margin impact unavailable", async ({ page }) => {
    await installMockWebSocket(page);
    await stubApis(page);

    await page.goto("/AVGO?deck=c&expiry=2026-06-26&strikes=100&legs=SELL:10x1250C,BUY:5x1200C");

    const builder = page.locator(".order-builder");
    await expect(builder).toBeVisible();
    await expect(builder).toContainText("ORDER BUILDER : Bull Call Spread");

    await page.getByRole("button", { name: /place bull call spread/i }).click();

    const summary = page.locator(".order-confirm-summary");
    await expect(summary).toContainText("Margin Req:");
    await expect(summary).toContainText("UNAVAILABLE");
    await expect(summary).toContainText("IB what-if required");
    await expect(summary).toContainText("Available Funds After:");
    await expect(summary.locator('[data-margin-requirement-unavailable="true"]')).toBeVisible();
  });
});
