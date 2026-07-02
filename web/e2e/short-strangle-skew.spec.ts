import { expect, test, type Page } from "@playwright/test";

const EXPIRY = "20260717";

const PORTFOLIO = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [],
};

function priceData(symbol: string, last: number, bid: number, ask: number, overrides: Record<string, unknown> = {}) {
  return {
    symbol,
    last,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 50,
    askSize: 50,
    volume: 1000,
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
    ...overrides,
  };
}

async function stubApis(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ open_orders: [], executed_orders: [], open_count: 0, executed_count: 0, last_sync: new Date().toISOString() }) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
  await page.route("**/api/ticker/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ uw_info: { name: "Apple Inc.", sector: "Technology", description: "Test" }, stock_state: {}, profile: {}, stats: {} }) }),
  );
  await page.route("**/api/options/expirations*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ symbol: "AAPL", expirations: [EXPIRY] }) }),
  );
  await page.route("**/api/options/chain*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ symbol: "AAPL", expiry: EXPIRY, exchange: "SMART", strikes: [100, 105, 110, 115, 120], multiplier: "100" }) }),
  );
  await page.route("**/api/prices", (route) => route.abort());
}

async function installMockWebSocket(page: Page) {
  const fixtures = {
    AAPL: priceData("AAPL", 110, 109.95, 110.05),
    [`AAPL_${EXPIRY}_105_P`]: priceData(`AAPL_${EXPIRY}_105_P`, 3.4, 3.3, 3.5, { impliedVol: 0.42, delta: -0.231 }),
    [`AAPL_${EXPIRY}_115_C`]: priceData(`AAPL_${EXPIRY}_115_C`, 2.7, 2.6, 2.8, { impliedVol: 0.30, delta: 0.184 }),
  };

  await page.addInitScript((priceFixtures) => {
    class MockWebSocket {
      static OPEN = 1;
      readyState = MockWebSocket.OPEN;
      onopen: ((event?: unknown) => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onclose: ((event?: unknown) => void) | null = null;
      onerror: ((event?: unknown) => void) | null = null;

      constructor() {
        setTimeout(() => {
          this.onopen?.({});
          this.emit({ type: "status", ib_connected: true, ib_issue: null, ib_status_message: null, subscriptions: [] });
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
          const key = `${String(contract.symbol).toUpperCase()}_${String(contract.expiry).replace(/-/g, "")}_${Number(contract.strike)}_${contract.right}`;
          if (priceFixtures[key]) updates[key] = priceFixtures[key];
        }
        if (Object.keys(updates).length > 0) this.emit({ type: "batch", updates });
      }

      close() {
        this.onclose?.({});
      }

      emit(payload: unknown) {
        this.onmessage?.({ data: JSON.stringify(payload) });
      }
    }

    // Only the Radon relay socket is mocked; Turbopack HMR and any other
    // sockets pass through to the real implementation.
    const NativeWebSocket = window.WebSocket;
    const RelayAwareWebSocket = function (url: string | URL, protocols?: string | string[]) {
      return String(url).includes("localhost:8765")
        ? (new MockWebSocket() as unknown as WebSocket)
        : new NativeWebSocket(url, protocols);
    } as unknown as typeof WebSocket;
    Object.assign(RelayAwareWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    window.WebSocket = RelayAwareWebSocket;
  }, fixtures);
}

test("short strangle proposals show skew and signed delta telemetry", async ({ page }) => {
  await stubApis(page);
  await installMockWebSocket(page);

  await page.goto("/AAPL?tab=chain");
  const detail = page.locator(".ticker-detail-page").last();
  await detail.waitFor({ timeout: 5_000 });
  await detail.locator(".chain-grid").waitFor();

  await detail.getByRole("row", { name: /\$105\.00/ }).first().locator(".chain-bid.chain-clickable").last().click();
  await detail.getByRole("row", { name: /\$115\.00/ }).first().locator(".chain-bid.chain-clickable").first().click();

  const panel = detail.getByTestId("short-strangle-skew-panel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("IV SKEW");
  await expect(panel).toContainText("+12.0 pt");
  await expect(panel).toContainText("CALL Δ");
  await expect(panel).toContainText("-0.184");
  await expect(panel).toContainText("PUT Δ");
  await expect(panel).toContainText("+0.231");
  await expect(panel).toContainText("NET Δ");
  await expect(panel).toContainText("+5 sh");
});
