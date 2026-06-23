import { expect, test } from "@playwright/test";

declare global {
  interface Window {
    __radonWsMessages: Array<Record<string, unknown>>;
  }
}

const VXN_PRICE = {
  symbol: "VXN",
  last: 31.12,
  lastIsCalculated: true,
  bid: null,
  ask: null,
  bidSize: null,
  askSize: null,
  volume: null,
  high: 31.9,
  low: 29.8,
  open: 30.4,
  close: 27.67,
  week52High: null,
  week52Low: null,
  avgVolume: null,
  delta: null,
  gamma: null,
  theta: null,
  vega: null,
  impliedVol: null,
  undPrice: null,
  fwd: null,
  fwdCurve: null,
  timestamp: "2026-06-23T15:00:00.000Z",
};

async function stubApis(page: import("@playwright/test").Page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path === "/api/portfolio") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ positions: [], account_summary: {}, exposure: {}, violations: [] }),
      });
      return;
    }

    if (path === "/api/orders") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 }),
      });
      return;
    }

    if (path === "/api/ticker/info") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ticker: "VXN", uw_info: { issue_type: "INDEX" }, stock_state: null }),
      });
      return;
    }

    if (path === "/api/watchlist") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ symbols: [] }) });
      return;
    }

    if (path === "/api/profile") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ username: "Operator" }) });
      return;
    }

    if (path === "/api/service-health") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ services: [] }) });
      return;
    }

    if (path === "/api/flex-token") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ remaining: 240 }) });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

async function stubWebSocket(page: import("@playwright/test").Page) {
  await page.addInitScript((vxnPrice) => {
    window.__radonWsMessages = [];

    class MockWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;

      url: string;
      readyState = MockWebSocket.CONNECTING;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;
      onclose: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;

      constructor(url: string) {
        this.url = url;
        window.setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.(new Event("open"));
          this.emit({ type: "status", ib_connected: true, subscriptions: [] });
        }, 0);
      }

      send(message: string) {
        window.__radonWsMessages.push(JSON.parse(message));
        const parsed = JSON.parse(message);
        const hasVxnIndex = Array.isArray(parsed.indexes) &&
          parsed.indexes.some((idx: { symbol?: string; exchange?: string }) =>
            idx.symbol === "VXN" && idx.exchange === "CBOE",
          );

        if (parsed.action === "subscribe" && hasVxnIndex) {
          window.setTimeout(() => {
            this.emit({ type: "price", symbol: "VXN", data: vxnPrice });
          }, 0);
        }
      }

      close() {
        this.readyState = MockWebSocket.CLOSED;
        this.onclose?.(new Event("close"));
      }

      private emit(payload: unknown) {
        this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>);
      }
    }

    Object.defineProperty(window, "WebSocket", {
      configurable: true,
      writable: true,
      value: MockWebSocket,
    });
  }, VXN_PRICE);
}

test.describe("/VXN index routing", () => {
  test("subscribes VXN as a CBOE index and renders the index quote", async ({ page }) => {
    await stubApis(page);
    await stubWebSocket(page);

    await page.goto("/VXN");

    await expect(page.locator(".ckh-sym")).toHaveText("VXN");
    await expect(page.locator(".ckh-last")).toContainText("31.12");
    await expect(page.locator(".index-notice")).toContainText("VXN is an index");

    const subscribe = await page.waitForFunction(() =>
      window.__radonWsMessages.find((msg: { action?: string }) => msg.action === "subscribe"),
    );
    const subscribeMessage = await subscribe.jsonValue() as {
      symbols?: string[];
      indexes?: Array<{ symbol: string; exchange: string }>;
    };

    expect(subscribeMessage.symbols ?? []).not.toContain("VXN");
    expect(subscribeMessage.indexes).toEqual([{ symbol: "VXN", exchange: "CBOE" }]);
  });
});
