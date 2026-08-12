/**
 * E2E: switching the chain EXPIRY dropdown must not wipe the order builder.
 *
 * Repro: build a leg on the near expiry, then pick a different expiry from the
 * dropdown. The builder used to be cleared on every expiry change, so any work
 * in progress vanished the moment the user looked at another date.
 */

import { test, expect } from "@playwright/test";

const TICKER = "MU";
const NEAR = "20260821";
const FAR = "20260918";

const PORTFOLIO_EMPTY = {
  bankroll: 1_000_000,
  peak_value: 1_000_000,
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

const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

function quote(symbol: string, bid: number, ask: number, last: number) {
  return {
    symbol,
    last,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 25,
    askSize: 25,
    volume: 1_000,
    high: null,
    low: null,
    open: null,
    close: last,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: 0.4,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.45,
    undPrice: 120,
    timestamp: new Date().toISOString(),
  };
}

const PRICE_FIXTURES: Record<string, unknown> = {
  [TICKER]: quote(TICKER, 119.9, 120.1, 120),
  [`${TICKER}_${NEAR}_125_C`]: quote(`${TICKER}_${NEAR}_125_C`, 3.1, 3.5, 3.3),
  [`${TICKER}_${FAR}_125_C`]: quote(`${TICKER}_${FAR}_125_C`, 5.1, 5.5, 5.3),
};

/**
 * Only the price relay is mocked. The Next dev server's own HMR socket must
 * keep the native implementation — it calls `addEventListener`, which a bare
 * on*-handler stub does not have, and the resulting TypeError tears down the
 * page before the chain ever loads.
 */
function installMockWebSocket(page: import("@playwright/test").Page) {
  return page.addInitScript((priceFixtures) => {
    const NativeWebSocket = window.WebSocket;
    const isRelay = (url: string) => url.includes(":8765") || url.includes("/ws");

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
      addEventListener(type: string, listener: (event: unknown) => void) {
        if (type === "open") this.onopen = listener;
        if (type === "message") this.onmessage = listener as (e: { data: string }) => void;
        if (type === "close") this.onclose = listener;
        if (type === "error") this.onerror = listener;
      }
      removeEventListener() {}
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
          const expiry = String(contract.expiry).replace(/-/g, "");
          const key = `${String(contract.symbol).toUpperCase()}_${expiry}_${Number(contract.strike)}_${contract.right}`;
          if (priceFixtures[key]) updates[key] = priceFixtures[key];
        }
        if (Object.keys(updates).length > 0) this.emit({ type: "batch", updates });
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
    window.WebSocket = function (url: string, protocols?: string | string[]) {
      // @ts-expect-error test-only replacement
      return isRelay(String(url)) ? new MockWebSocket(String(url)) : new NativeWebSocket(url, protocols);
    };
    Object.assign(window.WebSocket, {
      CONNECTING: 0,
      OPEN: 1,
      CLOSING: 2,
      CLOSED: 3,
    });
  }, PRICE_FIXTURES);
}

async function stubApis(page: import("@playwright/test").Page) {
  const json = (body: unknown) => ({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  await page.route("**/api/portfolio", (route) => route.fulfill(json(PORTFOLIO_EMPTY)));
  await page.route("**/api/orders", (route) => route.fulfill(json(ORDERS_EMPTY)));
  await page.route("**/api/regime", (route) => route.fulfill(json({ score: 15, cri: { score: 15 } })));
  await page.route("**/api/ib-status", (route) => route.fulfill(json({ connected: true })));
  await page.route("**/api/blotter", (route) =>
    route.fulfill(
      json({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    ),
  );
  await page.route("**/api/ticker/**", (route) =>
    route.fulfill(json({ uw_info: { name: "Micron Technology", sector: "Technology" }, stock_state: {}, profile: {}, stats: {} })),
  );
  await page.route("**/api/options/expirations*", (route) =>
    route.fulfill(json({ symbol: TICKER, expirations: [NEAR, FAR] })),
  );
  await page.route("**/api/options/chain*", (route) => {
    const expiry = new URL(route.request().url()).searchParams.get("expiry") ?? NEAR;
    return route.fulfill(json({ symbol: TICKER, expiry, exchange: "SMART", strikes: [115, 120, 125], multiplier: "100" }));
  });
}

test.describe("Options chain expiry switch", () => {
  test("keeps the order builder and its legs when the expiry changes", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await installMockWebSocket(page);
    await stubApis(page);

    await page.goto(`/${TICKER}?tab=chain`);

    const detail = page.locator(".ticker-detail-page");
    await detail.waitFor({ timeout: 15_000 });
    await detail.locator(".chain-grid").waitFor();

    const expirySelect = detail.locator("select.chain-expiry-select").first();
    await expect(expirySelect).toHaveValue(NEAR);

    const row125 = detail.getByRole("row", { name: /\$125\.00/ }).first();
    await row125.locator(".chain-mid.chain-clickable").first().click();

    const orderBuilder = detail.locator(".order-builder");
    await expect(orderBuilder).toBeVisible();
    await expect(orderBuilder).toContainText("1x $125 Call");
    await page.screenshot({ path: "test-results/chain-expiry-before.png", fullPage: false });

    await expirySelect.selectOption(FAR);
    await expect(expirySelect).toHaveValue(FAR);

    await expect(orderBuilder).toBeVisible();
    await expect(orderBuilder).toContainText("1x $125 Call");
    await expect(orderBuilder.locator('[data-testid="order-builder-leg"]')).toHaveCount(1);
    await page.screenshot({ path: "test-results/chain-expiry-after.png", fullPage: false });
  });
});
