import { expect, test, type Page } from "@playwright/test";

// Selling a call below a held long call makes a credit spread. The ticket's
// MAX GAIN / MAX LOSS are the order's own (held leg already paid for); the
// SPREAD cells price the whole spread with the held leg at its cost basis.

const EXPIRY = "20261016";

function quote(symbol: string, bid: number, ask: number, last: number) {
  return {
    symbol,
    last,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 30,
    askSize: 30,
    volume: 200,
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
    undPrice: 195.3,
    timestamp: new Date().toISOString(),
  };
}

const PRICES: Record<string, unknown> = {
  CRM: quote("CRM", 195.2, 195.4, 195.3),
  [`CRM_${EXPIRY}_195_C`]: quote(`CRM_${EXPIRY}_195_C`, 3.0, 3.2, 3.1),
  [`CRM_${EXPIRY}_200_C`]: quote(`CRM_${EXPIRY}_200_C`, 1.35, 1.57, 1.46),
};

// Held: LONG 10x CRM 2026-10-16 $200C, $2,000 basis ($200 per contract).
const PORTFOLIO = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 2,
  total_deployed_dollars: 2000,
  remaining_capacity_pct: 98,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [
    {
      id: 8,
      ticker: "CRM",
      structure: "Long Call $200.0",
      structure_type: "Long Option",
      risk_profile: "defined",
      expiry: "2026-10-16",
      contracts: 10,
      direction: "LONG",
      entry_cost: 2000,
      max_risk: 2000,
      market_value: 1460,
      market_price_is_calculated: false,
      legs: [
        {
          direction: "LONG",
          contracts: 10,
          type: "Call",
          strike: 200,
          entry_cost: 2000,
          avg_cost: 200,
          market_price: 1.46,
          market_value: 1460,
          market_price_is_calculated: false,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-09-01",
    },
  ],
};

async function installMockWebSocket(page: Page) {
  await page.addInitScript((prices) => {
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
          this.emit({ type: "status", ib_connected: true, ib_issue: null, ib_status_message: null, subscriptions: [] });
          this.emit({ type: "batch", updates: prices });
        }, 0);
      }
      send(raw: string) {
        const message = JSON.parse(raw) as { action?: string };
        if (message.action === "subscribe") this.emit({ type: "batch", updates: prices });
      }
      close() {
        this.readyState = MockWebSocket.CLOSED;
        this.onclose?.({});
      }
      emit(payload: unknown) {
        this.onmessage?.({ data: JSON.stringify(payload) });
      }
    }
    // Relay socket only; mocking Turbopack HMR too stalls hydration.
    const NativeWebSocket = window.WebSocket;
    const RelayAwareWebSocket = function (url: string | URL, protocols?: string | string[]) {
      return String(url).includes("localhost:8765")
        ? (new MockWebSocket() as unknown as WebSocket)
        : new NativeWebSocket(url, protocols);
    } as unknown as typeof WebSocket;
    Object.assign(RelayAwareWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    window.WebSocket = RelayAwareWebSocket;
  }, PRICES);
}

async function stubApis(page: Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/portfolio") return json(PORTFOLIO);
    if (path === "/api/orders") return json({ open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 });
    if (path === "/api/service-health") return json({ services: [] });
    if (path === "/api/profile") return json({ username: "Operator" });
    if (path === "/api/watchlist") return json({ symbols: [] });
    if (path === "/api/flex-token") return json({ remaining: 240 });
    if (path === "/api/prices" || path === "/api/ib/ws-ticket") return json({ prices: {}, ticket: "test" });
    if (path === "/api/regime") return json({ score: 15, cri: { score: 15 } });
    if (path === "/api/risk-free-rate") return json({ rate: 0 });
    if (path === "/api/ticker/info") return json({ stock_state: {}, uw_info: {}, profile: {}, stats: {} });
    if (path === "/api/options/expirations") return json({ symbol: "CRM", expirations: [EXPIRY] });
    if (path === "/api/options/chain") return json({ symbol: "CRM", expiry: EXPIRY, strikes: [190, 195, 200, 205] });
    return json({});
  });
}

test.describe("Chain ticket: spread risk including the held leg", () => {
  test("selling a call below a held long call shows order-only AND spread max gain / loss", async ({ page }) => {
    await installMockWebSocket(page);
    await stubApis(page);

    await page.goto("/CRM?tab=chain");

    const detail = page.locator(".ticker-detail-page");
    await detail.waitFor({ timeout: 60_000 });

    const row = detail.getByRole("row", { name: /\$195\.00/ }).first();
    await expect(row).toContainText("$3.00", { timeout: 15_000 });
    // Bid click = SELL leg.
    await row.locator(".chain-bid.chain-clickable").first().click();

    const risk = detail.locator('[data-testid="ticket-risk"]');
    await expect(risk).toBeVisible();
    const spread = risk.locator('[data-testid="ticket-risk-spread"]');
    await expect(spread).toBeVisible();
    // 1x $195C sold at the default mid limit $3.10 against 1 of 10 held $200C
    // ($200 basis each). Order-only: gain $310, loss $500 - $310 = $190.
    // Spread with the held leg at basis: gain $310 - $200 = $110, loss $190 + $200 = $390.
    await expect(spread).toContainText("$200 BASIS");
    const cellValue = (label: string) =>
      risk
        .locator(".ticket-risk-cell", {
          has: page.locator(".ticket-risk-cell-label", { hasText: new RegExp(`^${label}$`) }),
        })
        .locator(".ticket-risk-cell-value");
    await expect(cellValue("BEST CASE")).toHaveText("+$310.00");
    await expect(cellValue("WORST CASE")).toHaveText("-$190.00");
    await expect(cellValue("SPREAD BEST CASE")).toHaveText("+$110.00");
    await expect(cellValue("SPREAD WORST CASE")).toHaveText("-$390.00");

    await risk.scrollIntoViewIfNeeded();
    await risk.screenshot({ path: "test-results/held-leg-spread-risk.png" });
  });
});
