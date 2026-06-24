import { expect, test, type Page } from "@playwright/test";

const THETA_PAYLOAD = {
  scan_time: "2026-06-24T15:00:00Z",
  source: "Unusual Whales",
  universe: "explicit",
  requested_tickers: ["META"],
  tickers_scanned: 1,
  candidates_found: 1,
  theta_harvest_count: 1,
  results: [
    {
      ticker: "META",
      score: 91.2,
      verdict: "THETA_HARVEST",
      spot: 575,
      iv: 34,
      hv20: 18,
      hv60: 20,
      iv_rv_edge: 14,
      iv_rv_ratio: 1.7,
      trend_20d_pct: 0.4,
      range_score: 0.83,
      dealer_support: "SUPPORT",
      net_gex: 1_200_000,
      gex_flip: 560,
      setup: "TRUE_THETA",
      gates: {
        delta_near_zero: true,
        iv_rich_vs_rv: true,
        dealer_support: true,
        theta_positive: true,
        range_bound: true,
      },
      errors: [],
      structure: {
        expiry: "20260717",
        dte: 23,
        net_delta: 0.01,
        theta: 0.082,
        gamma: -0.004,
        vega: -0.04,
        credit: 8.2,
        short_put: {
          symbol: "META260717P00520000",
          expiry: "20260717",
          strike: 520,
          right: "P",
          iv: 36,
          delta: -0.16,
          theta: -0.04,
          gamma: 0.002,
          vega: 0.02,
          bid: 3.8,
          ask: 4.1,
          volume: 240,
          open_interest: 1200,
        },
        short_call: {
          symbol: "META260717C00625000",
          expiry: "20260717",
          strike: 625,
          right: "C",
          iv: 33,
          delta: 0.15,
          theta: -0.042,
          gamma: 0.002,
          vega: 0.02,
          bid: 3.9,
          ask: 4.3,
          volume: 210,
          open_interest: 1100,
        },
      },
    },
  ],
};

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
      constructor() {
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.({});
          this.onmessage?.({
            data: JSON.stringify({
              type: "batch",
              updates: {
                META: {
                  symbol: "META",
                  last: 575,
                  lastIsCalculated: false,
                  bid: 574.9,
                  ask: 575.1,
                  bidSize: 100,
                  askSize: 100,
                  volume: 1_000_000,
                  high: null,
                  low: null,
                  open: null,
                  close: 570,
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
              },
            }),
          });
        }, 0);
      }
      send() {}
      close() {
        this.readyState = MockWebSocket.CLOSED;
        this.onclose?.({});
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

    if (path === "/api/scanner/theta") return json(THETA_PAYLOAD);
    if (path === "/api/scanner") {
      return json({ scan_time: "2026-06-24T15:00:00Z", tickers_scanned: 0, signals_found: 0, top_signals: [] });
    }
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
      });
    }
    if (path === "/api/orders") return json({ open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 });
    if (path === "/api/service-health") return json({ services: [] });
    if (path === "/api/profile") return json({ username: "Operator" });
    if (path === "/api/watchlist") return json({ symbols: [] });
    if (path === "/api/flex-token") return json({ remaining: 240 });
    if (path === "/api/prices" || path === "/api/ib/ws-ticket") return json({ prices: {}, ticket: "test" });
    if (path === "/api/regime") return json({ score: 15, cri: { score: 15 } });
    if (path === "/api/risk-free-rate") return json({ rate: 0 });
    if (path === "/api/ticker/info") return json({ stock_state: {}, uw_info: {}, profile: {}, stats: {} });
    if (path === "/api/options/expirations") return json({ symbol: "META", expirations: ["20260717"] });
    if (path === "/api/options/chain") {
      return json({ symbol: "META", expiry: "20260717", strikes: [500, 520, 575, 625, 650] });
    }
    return json({});
  });
}

test.describe("theta harvester order prefill", () => {
  test("clicking a candidate opens the chain builder with both short strangle legs", async ({ page }) => {
    await installMockWebSocket(page);
    await stubApis(page);

    await page.goto("/scanner?mode=theta");
    await page.getByTestId("theta-harvester-section").waitFor();

    await page.getByRole("link", { name: /open META theta order builder/i }).click();

    await expect(page).toHaveURL(/\/META\?/);
    expect(new URL(page.url()).searchParams.get("legs")).toBe("SELL:1x520P,SELL:1x625C");

    const builder = page.locator(".order-builder");
    await expect(builder).toBeVisible();
    await expect(builder).toContainText("PREFILLED FROM THETA HARVESTER");
    await expect(builder).toContainText("ORDER BUILDER : Short Strangle");
    await expect(builder).toContainText("1x $520 Put");
    await expect(builder).toContainText("1x $625 Call");
    await expect(builder).toContainText("2026-07-17");
  });
});
