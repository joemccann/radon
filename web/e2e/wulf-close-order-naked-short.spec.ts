import { expect, test } from "@playwright/test";

const PORTFOLIO_MOCK = {
  bankroll: 1_089_652.28,
  peak_value: 1_089_652.28,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 3.68,
  total_deployed_dollars: 40_076.51,
  remaining_capacity_pct: 96.32,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [
    {
      id: 23,
      ticker: "WULF",
      structure: "Long Call",
      structure_type: "Long Call",
      risk_profile: "defined",
      expiry: "2027-01-15",
      contracts: 77,
      direction: "LONG",
      entry_cost: 40_076.51,
      max_risk: 40_076.51,
      market_value: 34_457.5,
      legs: [
        {
          direction: "LONG",
          contracts: 77,
          type: "Call",
          strike: 17,
          entry_cost: 40_076.51,
          avg_cost: 520.4741844,
          market_price: 4.475,
          market_value: 34_457.5,
          market_price_is_calculated: false,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-19",
    },
  ],
  exposure: {},
  violations: [],
};

const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const PRICE_FIXTURES = {
  WULF: {
    symbol: "WULF",
    last: 7.91,
    lastIsCalculated: false,
    bid: 7.9,
    ask: 7.92,
    bidSize: 100,
    askSize: 80,
    volume: 1_250_000,
    high: 8.12,
    low: 7.45,
    open: 7.61,
    close: 7.84,
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
  WULF_20270115_17_C: {
    symbol: "WULF_20270115_17_C",
    last: 4.47,
    lastIsCalculated: false,
    bid: 4.2,
    ask: 4.75,
    bidSize: 12,
    askSize: 8,
    volume: 71,
    high: null,
    low: null,
    open: null,
    close: 4.41,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: 0.52,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.88,
    undPrice: 7.91,
    timestamp: new Date().toISOString(),
  },
};

async function installMockWebSocket(page: import("@playwright/test").Page) {
  await page.addInitScript((priceFixtures) => {
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

        if (Object.keys(updates).length > 0) {
          this.emit({ type: "batch", updates });
        }
      }

      close() {
        this.readyState = MockWebSocket.CLOSED;
        this.onclose?.({});
      }

      emit(payload: unknown) {
        this.onmessage?.({ data: JSON.stringify(payload) });
      }
    }

    // Only the Radon relay socket is mocked; Turbopack HMR and any other
    // sockets pass through to the real implementation. Replacing
    // window.WebSocket wholesale hijacks the dev-server's own HMR socket and
    // stalls hydration (see feedback_e2e_ws_mock_relay_only_and_44px_button_floor
    // and the canonical pattern in e2e/mobile-short-strangle-skew.spec.ts) —
    // the symptom is exactly what this spec is diagnosing against: a page
    // that paints its SSR shell but never hydrates, so React state (the
    // resolved position) never reaches the DOM and every locator resolves to
    // the pre-hydration default ("New Order" / "No position").
    const NativeWebSocket = window.WebSocket;
    const RelayAwareWebSocket = function (url: string | URL, protocols?: string | string[]) {
      return String(url).includes("localhost:8765")
        ? (new MockWebSocket(String(url)) as unknown as WebSocket)
        : new NativeWebSocket(url, protocols);
    } as unknown as typeof WebSocket;
    Object.assign(RelayAwareWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    window.WebSocket = RelayAwareWebSocket;
  }, PRICE_FIXTURES);
}

async function stubApis(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_MOCK),
    }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_EMPTY),
    }),
  );
  await page.route("**/api/orders/place", async (route) => {
    const payload = await route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        orderId: 99123,
        permId: 88123,
        initialStatus: "Submitted",
        message: "Order placed successfully",
        payload,
        orders: ORDERS_EMPTY,
      }),
    });
  });
  await page.route("**/api/regime", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ score: 15, level: "LOW", cri: { score: 15 } }),
    }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: true }),
    }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        as_of: new Date().toISOString(),
        summary: { realized_pnl: 0 },
        closed_trades: [],
        open_trades: [],
      }),
    }),
  );
}

test("WULF close-position order tab does not show a false naked-short warning", async ({ page }) => {
  await installMockWebSocket(page);
  await stubApis(page);

  await page.goto("/WULF?posId=23&tab=order");

  await expect(page.locator(".existing-orders-title").first()).toContainText("Close Position");

  await page.getByRole("button", { name: "SELL" }).click();
  await page.locator(".order-input").fill("77");
  await page.locator(".modify-price-input").fill("4.47");
  await expect(page.locator(".order-error").filter({ hasText: /Naked short call/i })).toHaveCount(0);

  const placeButton = page.getByRole("button", { name: "Place Order" });
  await expect(placeButton).toBeEnabled();
  await placeButton.click();

  const confirmButton = page.getByRole("button", { name: "Confirm Order" });
  await expect(confirmButton).toBeEnabled();
  await confirmButton.click();

  // A relay-connect toast ("IB Gateway · uplink restored") also fires as the
  // mocked relay comes up, so scope to the specific order-placed toast rather
  // than the bare `.toast-success` class (now ambiguous — two may be mounted).
  const orderToast = page.locator(".toast-success").filter({ hasText: /Order placed/i });
  await expect(orderToast).toContainText(/Order placed: SELL 77 WULF/i);
  await expect(page.locator(".order-error").filter({ hasText: /Naked short call/i })).toHaveCount(0);
});
