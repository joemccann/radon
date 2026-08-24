import { expect, test, type Page } from "@playwright/test";

/**
 * Client-side ticker navigation must keep the Book tab on live depth.
 *
 * Regression (2026-08-24): landing on /AMZN by hard load showed SMART DEPTH,
 * but navigating AMZN -> NVDA inside the app (header search -> router.push)
 * left depthSymbols empty, so the client never sent subscribe-depth and the
 * montage degraded to a single-row "L1 BBO" book with an empty tape.
 */

const now = new Date().toISOString();

function price(symbol: string, last: number) {
  return {
    symbol, last, lastIsCalculated: false, bid: last - 0.05, ask: last + 0.05,
    bidSize: 200, askSize: 200, volume: 1_000, high: null, low: null, open: null,
    close: last - 1, week52High: null, week52Low: null, avgVolume: null, delta: null,
    gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null, timestamp: now,
  };
}

function depth(symbol: string, last: number) {
  const level = (offset: number, size: number, venue: string) => ({
    price: Number((last + offset).toFixed(2)), size, marketMaker: venue, exchange: venue,
  });
  return {
    symbol, kind: "stock", isSmartDepth: true, feed: "SMART DEPTH", entitled: true, timestamp: now,
    bid: [level(-0.05, 300, "ARCA"), level(-0.06, 200, "NSDQ"), level(-0.07, 150, "BATS")],
    ask: [level(0.05, 120, "ARCA"), level(0.06, 100, "NSDQ"), level(0.07, 90, "BATS")],
  };
}

const FIXTURES = {
  AMZN: { price: price("AMZN", 261.57), depth: depth("AMZN", 261.57) },
  NVDA: { price: price("NVDA", 178.4), depth: depth("NVDA", 178.4) },
};

declare global {
  interface Window { __depthSubscriptions?: string[] }
}

async function installMockRelay(page: Page) {
  await page.addInitScript((fixtures: typeof FIXTURES) => {
    window.__depthSubscriptions = [];
    class MockWebSocket {
      static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
      url: string; readyState = 0;
      onopen: ((e?: unknown) => void) | null = null;
      onmessage: ((e: { data: string }) => void) | null = null;
      onclose: ((e?: unknown) => void) | null = null;
      onerror: ((e?: unknown) => void) | null = null;
      constructor(url: string) {
        this.url = url;
        setTimeout(() => {
          this.readyState = 1;
          this.onopen?.({});
          this.emit({ type: "status", ib_connected: true, ib_issue: null, ib_status_message: null, subscriptions: [] });
        }, 0);
      }
      send(raw: string) {
        const message = JSON.parse(raw) as { action?: string; symbols?: string[]; symbol?: string; pattern?: string };
        if (message.action === "subscribe") {
          const updates: Record<string, unknown> = {};
          for (const symbol of message.symbols ?? []) {
            const fixture = fixtures[symbol as keyof typeof fixtures];
            if (fixture) updates[symbol] = fixture.price;
          }
          if (Object.keys(updates).length) this.emit({ type: "batch", updates });
        }
        if (message.action === "subscribe-depth" && message.symbol) {
          window.__depthSubscriptions!.push(message.symbol);
          const fixture = fixtures[message.symbol as keyof typeof fixtures];
          if (fixture) this.emit({ type: "depth-batch", updates: { [message.symbol]: fixture.depth } });
        }
        if (message.action === "search" && message.pattern) {
          const symbol = message.pattern.trim().toUpperCase();
          this.emit({
            type: "searchResults",
            pattern: symbol,
            results: [{ conId: 1, symbol, secType: "STK", primaryExchange: "NASDAQ", currency: "USD" }],
          });
        }
      }
      close() { this.readyState = 3; this.onclose?.({}); }
      emit(payload: unknown) { this.onmessage?.({ data: JSON.stringify(payload) }); }
    }
    // Only the Radon relay socket is mocked; Turbopack HMR passes through.
    const NativeWebSocket = window.WebSocket;
    const RelayAwareWebSocket = function (url: string | URL, protocols?: string | string[]) {
      return String(url).includes("localhost:8765")
        ? (new MockWebSocket(String(url)) as unknown as WebSocket)
        : new NativeWebSocket(url, protocols);
    } as unknown as typeof WebSocket;
    Object.assign(RelayAwareWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    window.WebSocket = RelayAwareWebSocket;
  }, FIXTURES);
}

async function stubApis(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  const json = (body: unknown) => (r: { fulfill: (o: object) => Promise<void> }) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/portfolio", json({
    bankroll: 100_000, peak_value: 100_000, last_sync: now, total_deployed_pct: 0,
    total_deployed_dollars: 0, remaining_capacity_pct: 100, position_count: 0,
    defined_risk_count: 0, undefined_risk_count: 0, avg_kelly_optimal: null,
    exposure: {}, violations: [], positions: [],
  }));
  await page.route("**/api/orders", json({ last_sync: now, open_orders: [], executed_orders: [] }));
  await page.route("**/api/regime", json({ score: 15, level: "LOW" }));
  await page.route("**/api/ib-status", json({ connected: true }));
  await page.route("**/api/blotter", json({ as_of: now, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }));
  await page.route("**/api/cash-flows**", json({ rows: [], summary: {} }));
  await page.route("**/api/flex-token", json({ ok: true, days_until_expiry: 14 }));
  await page.route("**/api/ticker/**", json({}));
}

async function navigateViaSearch(page: Page, symbol: string) {
  const search = page.getByRole("combobox", { name: "Search ticker" });
  await search.click();
  await search.fill(symbol);
  await page.getByRole("option", { name: new RegExp(symbol) }).first().click();
  await page.waitForURL(new RegExp(`/${symbol}(\\?|$)`));
}

test("client-side ticker navigation keeps the book on live depth, not L1 BBO", async ({ page }) => {
  await installMockRelay(page);
  await stubApis(page);

  await page.goto("/AMZN?tab=book");
  const pill = page.locator(".book-feed-pill").first();
  await expect(pill).toHaveText("SMART DEPTH");

  await navigateViaSearch(page, "NVDA");
  await expect(pill).toHaveText("SMART DEPTH");
  await expect(pill).not.toHaveText("L1 BBO");

  const subscriptions = await page.evaluate(() => window.__depthSubscriptions ?? []);
  expect(subscriptions).toContain("AMZN");
  expect(subscriptions).toContain("NVDA");
});
