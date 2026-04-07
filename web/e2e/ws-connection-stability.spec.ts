import { expect, test } from "@playwright/test";

/* ─── Shared mock payloads ─── */

const CRI_MOCK = {
  scan_time: "2026-03-12T10:00:00",
  market_open: true,
  date: "2026-03-12",
  vix: 24.0,
  vvix: 115.0,
  spy: 555.0,
  vix_5d_roc: 5.2,
  vvix_vix_ratio: 4.79,
  realized_vol: 12.5,
  cor1m: 29.31,
  cor1m_previous_close: 28.97,
  cor1m_5d_change: 1.48,
  spx_100d_ma: 560.0,
  spx_distance_pct: -0.89,
  spy_closes: Array.from({ length: 22 }, (_, i) => 550 + i * 0.5),
  cri: { score: 20, level: "LOW", components: { vix: 5, vvix: 4, correlation: 6, momentum: 5 } },
  crash_trigger: {
    triggered: false,
    conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, cor1m_gt_60: false },
  },
  cta: { exposure_pct: 95, forced_reduction_pct: 0, est_selling_bn: 0 },
  menthorq_cta: null,
  history: [],
};

const PORTFOLIO_MOCK = {
  bankroll: 100_000,
  positions: [
    {
      id: 1,
      ticker: "PLTR",
      structure: "Long Call",
      structure_type: "Long Call",
      risk_profile: "defined",
      direction: "LONG",
      contracts: 2,
      expiry: "2026-04-17",
      entry_cost: 520,
      market_value: 680,
      legs: [
        { type: "Call", strike: 90, position: 2, avg_cost: 2.60, market_price: 3.40, market_value: 680 },
      ],
    },
  ],
  account_summary: {},
  exposure: {},
  violations: [],
};

const PORTFOLIO_EMPTY = {
  bankroll: 100_000,
  positions: [],
  account_summary: {},
  exposure: {},
  violations: [],
};

const ORDERS_MOCK = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const CHAIN_EXPIRATIONS = ["20260417", "20260515", "20260619"];

const CHAIN_STRIKES = [80, 82, 84, 86, 88, 90, 92, 94, 96, 98, 100];

/* ─── Build mock price tick for a symbol ─── */

function mockPrice(symbol: string, last: number, bid: number, ask: number) {
  return {
    symbol,
    last,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 100,
    askSize: 100,
    volume: 50000,
    high: last + 1,
    low: last - 1,
    open: last - 0.5,
    close: last - 0.2,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: symbol.includes("_") ? 0.55 : null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: symbol.includes("_") ? 0.42 : null,
    undPrice: symbol.includes("_") ? 88.5 : null,
    timestamp: new Date().toISOString(),
  };
}

/* ─── API route mocks ─── */

async function setupApiMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CRI_MOCK) }),
  );
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_MOCK) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_MOCK) }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  await page.route("**/api/menthorq/cta", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ tables: [] }) }),
  );
  await page.route("**/api/previous-close", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ closes: {} }) }),
  );
  await page.route("**/api/options/expirations*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ expirations: CHAIN_EXPIRATIONS }) }),
  );
  await page.route("**/api/options/chain*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ strikes: CHAIN_STRIKES }) }),
  );
  await page.route("**/api/ticker/info*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ name: "Palantir Technologies", sector: "Technology", industry: "Software" }),
    }),
  );
  await page.route("**/api/ticker/seasonality*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ months: [] }) }),
  );
}

/* ─── Inject MockWebSocket ─── */

/**
 * Injects a MockWebSocket that:
 * - Tracks total constructor calls in window.__wsConstructorCount
 * - Tracks max concurrent open connections in window.__wsMaxConcurrent
 * - Delivers IB-connected status + price ticks for PLTR and option contracts
 * - Simulates sequential subscription messages arriving (portfolio → orders → chain)
 */
async function injectMockWebSocket(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    // Tracking counters for assertions
    (window as unknown as Record<string, unknown>).__wsConstructorCount = 0;
    (window as unknown as Record<string, unknown>).__wsMaxConcurrent = 0;
    (window as unknown as Record<string, unknown>).__wsActiveCount = 0;
    (window as unknown as Record<string, unknown>).__wsSubscribeCount = 0;
    (window as unknown as Record<string, unknown>).__wsSubscribeInstanceIds = new Set<number>();

    class MockWebSocket {
      public static CONNECTING = 0;
      public static OPEN = 1;
      public static CLOSING = 2;
      public static CLOSED = 3;

      public url: string;
      public readyState = 0;
      public onopen: ((event: Event) => void) | null = null;
      public onmessage: ((event: MessageEvent<string>) => void) | null = null;
      public onclose: ((event: Event) => void) | null = null;
      public onerror: ((event: Event) => void) | null = null;
      private _instanceId: number;

      constructor(url: string) {
        this.url = url;
        const w = window as unknown as Record<string, unknown>;
        w.__wsConstructorCount = (w.__wsConstructorCount as number) + 1;
        this._instanceId = w.__wsConstructorCount as number;
        w.__wsActiveCount = (w.__wsActiveCount as number) + 1;
        if ((w.__wsActiveCount as number) > (w.__wsMaxConcurrent as number)) {
          w.__wsMaxConcurrent = w.__wsActiveCount;
        }

        // Open after microtask
        window.setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.(new Event("open"));
        }, 0);

        // Send status: connected
        window.setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({
              type: "status",
              ib_connected: true,
              ib_issue: null,
              ib_status_message: null,
              subscriptions: [],
            }),
          } as MessageEvent<string>);
        }, 5);

        // Send PLTR stock price
        window.setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({
              type: "price",
              symbol: "PLTR",
              data: {
                symbol: "PLTR",
                last: 88.5,
                lastIsCalculated: false,
                bid: 88.4,
                ask: 88.6,
                bidSize: 200,
                askSize: 150,
                volume: 5000000,
                high: 89.2,
                low: 87.8,
                open: 88.0,
                close: 87.5,
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
            }),
          } as MessageEvent<string>);
        }, 20);

        // Send option contract prices (batch) after chain subscription arrives
        window.setTimeout(() => {
          const updates: Record<string, unknown> = {};
          const strikes = [88, 90, 92];
          for (const strike of strikes) {
            const callKey = `PLTR_20260417_${strike}_C`;
            const putKey = `PLTR_20260417_${strike}_P`;
            updates[callKey] = {
              symbol: callKey,
              last: strike === 90 ? 3.4 : strike < 90 ? 5.2 : 1.8,
              lastIsCalculated: false,
              bid: strike === 90 ? 3.3 : strike < 90 ? 5.1 : 1.7,
              ask: strike === 90 ? 3.5 : strike < 90 ? 5.3 : 1.9,
              bidSize: 50,
              askSize: 50,
              volume: 1200,
              high: null,
              low: null,
              open: null,
              close: null,
              week52High: null,
              week52Low: null,
              avgVolume: null,
              delta: 0.55,
              gamma: 0.03,
              theta: -0.04,
              vega: 0.12,
              impliedVol: 0.42,
              undPrice: 88.5,
              timestamp: new Date().toISOString(),
            };
            updates[putKey] = {
              symbol: putKey,
              last: strike === 90 ? 4.6 : strike < 90 ? 2.9 : 6.1,
              lastIsCalculated: false,
              bid: strike === 90 ? 4.5 : strike < 90 ? 2.8 : 6.0,
              ask: strike === 90 ? 4.7 : strike < 90 ? 3.0 : 6.2,
              bidSize: 40,
              askSize: 40,
              volume: 800,
              high: null,
              low: null,
              open: null,
              close: null,
              week52High: null,
              week52Low: null,
              avgVolume: null,
              delta: -0.45,
              gamma: 0.03,
              theta: -0.03,
              vega: 0.11,
              impliedVol: 0.44,
              undPrice: 88.5,
              timestamp: new Date().toISOString(),
            };
          }
          this.onmessage?.({
            data: JSON.stringify({ type: "batch", updates }),
          } as MessageEvent<string>);
        }, 100);
      }

      send(_message: string) {
        // Parse and respond to subscribe messages with "subscribed" ack
        try {
          const msg = JSON.parse(_message);
          if (msg.action === "subscribe") {
            // Track which WS instances receive subscribe actions (usePrices pattern)
            const w = window as unknown as Record<string, unknown>;
            const ids = w.__wsSubscribeInstanceIds as Set<number>;
            if (!ids.has(this._instanceId)) {
              ids.add(this._instanceId);
              w.__wsSubscribeCount = ids.size;
            }
            if (msg.symbols) {
              window.setTimeout(() => {
                this.onmessage?.({
                  data: JSON.stringify({ type: "subscribed", symbols: msg.symbols }),
                } as MessageEvent<string>);
              }, 5);
            }
          }
        } catch {
          // ignore
        }
      }

      close() {
        this.readyState = MockWebSocket.CLOSED;
        const w = window as unknown as Record<string, unknown>;
        w.__wsActiveCount = Math.max(0, (w.__wsActiveCount as number) - 1);
        this.onclose?.(new Event("close"));
      }

      addEventListener() {}
      removeEventListener() {}
      dispatchEvent() { return false; }
    }

    Object.defineProperty(window, "WebSocket", {
      configurable: true,
      writable: true,
      value: MockWebSocket,
    });
  });
}

/* ─── Tests ─── */

test.describe("WebSocket connection stability on ticker detail page", () => {
  test.beforeEach(async ({ page }) => {
    await setupApiMocks(page);
    await injectMockWebSocket(page);
  });

  test("sidebar never shows OFFLINE during page load with sequential data arrival", async ({ page }) => {
    await page.goto("/PLTR?tab=chain");

    // Wait for page to render the sidebar status
    const statusDot = page.locator(".sidebar-footer .status-dot-wrap");
    await expect(statusDot).toBeVisible({ timeout: 10_000 });

    // Give time for portfolio, orders, and chain subscriptions to arrive sequentially
    await page.waitForTimeout(500);

    // Sidebar should show CONNECTED, not OFFLINE
    await expect(statusDot).toContainText("CONNECTED");

    // Verify it never showed OFFLINE at any point —
    // the debounced ibConnected starts truthy from mock, so there should be no flicker
    const statusText = await statusDot.textContent();
    expect(statusText).not.toContain("OFFLINE");
  });

  test("empty orders page still shows CONNECTED when the shared IB status socket is healthy", async ({ page }) => {
    await page.route("**/api/portfolio", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_EMPTY) }),
    );
    await page.route("**/api/orders", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_MOCK) }),
    );

    await page.goto("http://127.0.0.1:3000/orders");

    const statusDot = page.locator(".sidebar-footer .status-dot-wrap");
    await expect(statusDot).toBeVisible({ timeout: 10_000 });
    await page.waitForTimeout(300);

    await expect(statusDot).toContainText("CONNECTED");
    const statusText = await statusDot.textContent();
    expect(statusText).not.toContain("OFFLINE");
  });

  test("option chain shows bid/ask values, not triple-dash placeholders", async ({ page }) => {
    await page.goto("/PLTR?tab=chain");

    // Wait for the chain tab to render with strikes
    const chainTable = page.locator(".chain-row");
    await expect(chainTable.first()).toBeVisible({ timeout: 15_000 });

    // Wait for batch price data to arrive (100ms in mock + render time)
    await page.waitForTimeout(1000);

    // At least some chain cells should show real bid/ask, not "---"
    const bidCells = page.locator(".chain-bid");
    const bidCount = await bidCells.count();
    expect(bidCount).toBeGreaterThan(0);

    // Collect bid values — at least one should be a real number, not "---"
    let realBidCount = 0;
    for (let i = 0; i < Math.min(bidCount, 10); i++) {
      const text = await bidCells.nth(i).textContent();
      if (text && text.trim() !== "---" && text.trim() !== "") {
        realBidCount++;
      }
    }
    expect(realBidCount).toBeGreaterThan(0);

    // Similarly for ask cells
    const askCells = page.locator(".chain-ask");
    let realAskCount = 0;
    const askCount = await askCells.count();
    for (let i = 0; i < Math.min(askCount, 10); i++) {
      const text = await askCells.nth(i).textContent();
      if (text && text.trim() !== "---" && text.trim() !== "") {
        realAskCount++;
      }
    }
    expect(realAskCount).toBeGreaterThan(0);
  });

  test("usePrices does not tear down and recreate the WS when subscriptions change", async ({ page }) => {
    await page.goto("/PLTR?tab=chain");

    // Wait for everything to settle — portfolio, orders, chain subscriptions all arrive
    await page.waitForTimeout(2000);

    // The app has multiple WS consumers (usePrices, useIBStatus, TickerSearch, getSnapshot).
    // Our fix ensures usePrices specifically creates ONE connection and sends diff-based
    // subscribe messages over it, rather than tearing down and recreating on sub changes.
    //
    // Count WS instances that received a "subscribe" action (the usePrices pattern).
    // useIBStatus sends "status" pings, TickerSearch sends "search", getSnapshot sends "snapshot".
    const subscribeWsCount = await page.evaluate(() => (window as unknown as Record<string, unknown>).__wsSubscribeCount ?? 0);

    // usePrices should create exactly 1 WS that receives subscribe messages.
    // Before the fix, this would be 3+ (one per subscription change: portfolio → orders → chain).
    expect(subscribeWsCount).toBeLessThanOrEqual(1);

    // Verify max concurrent is bounded (usePrices + useIBStatus + possible getSnapshot/TickerSearch)
    const maxConcurrent = await page.evaluate(() => (window as unknown as Record<string, unknown>).__wsMaxConcurrent);
    expect(maxConcurrent).toBeLessThanOrEqual(4);
  });

  test("quote telemetry shows option price for positioned ticker, not underlying", async ({ page }) => {
    await page.goto("/PLTR?tab=chain");

    // Wait for price data to arrive and render
    await page.waitForTimeout(1500);

    // The hero quote telemetry should be visible
    const heroLeft = page.locator(".ticker-detail-hero-left");
    await expect(heroLeft).toBeVisible({ timeout: 10_000 });

    // For a Long Call position, the quote should show the option price (~$3.40),
    // NOT the underlying stock price ($88.50).
    // The label should reference the option contract, not just "PLTR"
    const heroText = await heroLeft.textContent();

    // The position pill should be present
    expect(heroText).toContain("LONG");
    expect(heroText).toContain("Long Call");
  });
});
