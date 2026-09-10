import { expect, test, type Page } from "@playwright/test";

// Window-relative, NOT a literal: `LeapScanner.isScanStale` suppresses
// `bestRow` — and with it the TRADE BEST link this spec clicks — once the scan
// is older than SERVICE_FRESHNESS_WINDOWS["leap-scan"].open (26h). A hardcoded
// stamp passes until it crosses that line and then fails forever; this one
// aged out on 2026-08-28 and took the P0-financial smoke red with it.
const FRESH_SCAN_TIME = new Date(Date.now() - 5 * 60_000).toISOString();

const LEAP_PAYLOAD = {
  scan_time: FRESH_SCAN_TIME,
  min_gap: 10,
  universe: "preset:largecaps",
  requested_tickers: [],
  results: [
    {
      ticker: "MSFT",
      price: 490.2,
      hv_20: 18.3,
      hv_60: 19.1,
      hv_252: 21.4,
      current_iv: 20.9,
      iv_rank: 44,
      leap_count: 5,
      best_gap: 0,
      is_mispriced: false,
      best_leap: null,
    },
    {
      ticker: "NVDA",
      price: 181.4,
      hv_20: 42.1,
      hv_60: 38.7,
      hv_252: 44.9,
      current_iv: 31.2,
      iv_rank: 12.5,
      leap_count: 8,
      best_gap: 13.7,
      is_mispriced: true,
      best_leap: {
        symbol: "NVDA270115C00210000",
        expiry: "2027-01-15",
        strike: 210,
        right: "C",
        iv: 28.4,
        delta: 0.42,
        gap: 13.7,
        oi: 900,
        volume: 12,
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
                NVDA: {
                  symbol: "NVDA",
                  last: 181.4,
                  lastIsCalculated: false,
                  bid: 181.3,
                  ask: 181.5,
                  bidSize: 100,
                  askSize: 100,
                  volume: 1_000_000,
                  high: null,
                  low: null,
                  open: null,
                  close: 180,
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
    // Relay socket only — mocking Turbopack HMR too stalls hydration
    // (see theta-harvester-prefill.spec.ts).
    const NativeWebSocket = window.WebSocket;
    const RelayAwareWebSocket = function (url: string | URL, protocols?: string | string[]) {
      return String(url).includes("localhost:8765")
        ? (new MockWebSocket() as unknown as WebSocket)
        : new NativeWebSocket(url, protocols);
    } as unknown as typeof WebSocket;
    Object.assign(RelayAwareWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    window.WebSocket = RelayAwareWebSocket;
  });
}

async function stubApis(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = (body: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/leap") return json(LEAP_PAYLOAD);
    if (path === "/api/scanner") {
      return json({ scan_time: FRESH_SCAN_TIME, tickers_scanned: 0, signals_found: 0, top_signals: [] });
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
    if (path === "/api/options/expirations") return json({ symbol: "NVDA", expirations: ["20270115"] });
    if (path === "/api/options/chain") {
      return json({ symbol: "NVDA", expiry: "20270115", strikes: [170, 180, 190, 200, 210, 220] });
    }
    return json({});
  });
}

test.describe("LEAP IV mispricing order prefill", () => {
  test("TRADE BEST opens the widest-gap LEAP in the chain order builder", async ({ page }) => {
    await installMockWebSocket(page);
    await stubApis(page);

    await page.goto("/scanner?mode=leap");
    await page.getByTestId("leap-scanner-section").waitFor();

    await page.getByTestId("leap-best-order-link").click();

    await expect(page).toHaveURL(/\/NVDA\?/);
    const params = new URL(page.url()).searchParams;
    expect(params.get("legs")).toBe("BUY:1x210C");
    expect(params.get("expiry")).toBe("2027-01-15");
    expect(params.get("deck")).toBe("c");

    const builder = page.locator(".order-builder");
    await expect(builder).toBeVisible();
    await expect(builder).toContainText("PREFILLED FROM LEAP SCAN");
    await expect(builder).toContainText("1x $210 Call");
    await expect(builder).toContainText("2027-01-15");
  });
});
