import { expect, test, type Page, type Route } from "@playwright/test";

/** REL-295 / R-714: live ticks cannot spend a previous-close retry early. */
const now = "2026-09-29T15:00:00Z";
const portfolio = {
  bankroll: 100_000, peak_value: 100_000, last_sync: now,
  total_deployed_pct: 1, total_deployed_dollars: 1000, remaining_capacity_pct: 99,
  position_count: 1, defined_risk_count: 1, undefined_risk_count: 0,
  exposure: {}, violations: [], account_summary: {
    net_liquidation: 100_000, daily_pnl: null, unrealized_pnl: 30,
    realized_pnl: 0, settled_cash: 99_000, maintenance_margin: 0,
    excess_liquidity: 99_000, buying_power: 100_000, dividends: 0,
  },
  positions: [{
    id: 1, ticker: "AAPL", structure: "Stock (10 shares)", structure_type: "Stock",
    risk_profile: "equity", direction: "LONG", contracts: 10, expiry: "N/A",
    entry_cost: 1000, market_value: 1000, entry_date: "2026-09-28",
    legs: [{ direction: "LONG", contracts: 10, type: "Stock", strike: 0,
      entry_cost: 1000, avg_cost: 100, market_price: 100, market_value: 1000 }],
  }],
};

type FixtureWindow = Window & { reliabilityQuote?: (last: number) => void };
async function quote(page: Page, last: number) {
  await page.evaluate(value => (window as FixtureWindow).reliabilityQuote?.(value), last);
  // Let the relay's React batching publish this tick before the next response.
  await page.clock.runFor(300);
}

test("Retry-After survives intervening live ticks and recovers the close", async ({ page }, testInfo) => {
  await page.clock.install({ time: new Date(now) });
  await page.addInitScript(() => {
    const Native = window.WebSocket;
    class Relay {
      readyState = 0;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onclose: ((event: Event) => void) | null = null;
      onerror = null;
      constructor() {
        (window as FixtureWindow).reliabilityQuote = last => this.onmessage?.({ data: JSON.stringify({
          type: "batch", updates: { AAPL: { symbol: "AAPL", last, close: null,
            bid: last - 0.01, ask: last + 0.01, timestamp: new Date().toISOString() } },
        }) } as MessageEvent);
        setTimeout(() => {
          this.readyState = 1;
          this.onopen?.(new Event("open"));
          this.onmessage?.({ data: JSON.stringify({ type: "status", ib_connected: true, subscriptions: [] }) } as MessageEvent);
        }, 0);
      }
      send() {}
      close() { this.readyState = 3; this.onclose?.(new Event("close")); }
    }
    const Socket = function (url: string | URL, protocols?: string | string[]) {
      return String(url).includes(":8765") ? new Relay() : new Native(url, protocols);
    } as unknown as typeof WebSocket;
    Object.assign(Socket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    window.WebSocket = Socket;
  });
  await page.route(/^https?:\/\/(?!localhost:|127\.0\.0\.1:)/, route => route.abort());
  let pending: Route | undefined;
  const requests: { url: string; method: string; body: unknown }[] = [];
  // Every API request is synthetic, including unrecognized background readers.
  await page.route("**/api/**", route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/previous-close") {
      requests.push({ url: request.url(), method: request.method(), body: request.postDataJSON() });
      if (requests.length === 1) { pending = route; return; }
      return route.fulfill({ json: { closes: { AAPL: 99 } } });
    }
    const body = path === "/api/portfolio" ? portfolio
      : path === "/api/orders" ? { last_sync: now, open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 }
      : path === "/api/ib-status" ? { connected: true }
      : path === "/api/ws-ticket" ? { ticket: "fixture" }
      : {};
    return route.fulfill({ json: body });
  });
  await page.goto("/portfolio");
  await expect(page.locator("tr", { hasText: "AAPL" }).first()).toBeVisible();
  await expect.poll(() => page.evaluate(() => Boolean((window as FixtureWindow).reliabilityQuote))).toBe(true);
  await quote(page, 100);
  await expect.poll(() => requests.length).toBe(1);
  await quote(page, 101); // Effect cleanup while the request remains in flight.
  await pending!.fulfill({ status: 429, headers: { "Retry-After": "30" }, json: { error: "rate limited" } });
  await page.clock.runFor(100);
  await quote(page, 102);
  await quote(page, 103);
  expect(requests).toHaveLength(1);
  await page.clock.runFor(30_000);
  await expect.poll(() => requests.length).toBe(2);
  for (const request of requests) {
    expect(request.url).toBe(new URL("/api/previous-close", page.url()).href);
    expect(request.method).toBe("POST");
    expect(request.body).toEqual({ symbols: ["AAPL"] });
  }
  await expect(page.locator("tr", { hasText: "AAPL" }).first()).toContainText("4.04%");
  await page.locator("tr", { hasText: "AAPL" }).first().scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("previous-close-recovered.png"), fullPage: true });
});
