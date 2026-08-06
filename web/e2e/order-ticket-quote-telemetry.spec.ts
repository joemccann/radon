import { expect, test } from "@playwright/test";

test.use({ serviceWorkers: "block" });

function parsePrice(text: string | null): number {
  return Number((text ?? "").replace(/[$,]/g, ""));
}

function formatUsd(value: number): string {
  return `$${value.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

const PORTFOLIO_WITH_VERTICAL = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 9.84,
  total_deployed_dollars: 9842,
  remaining_capacity_pct: 90.16,
  position_count: 2,
  defined_risk_count: 2,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  account_summary: {
    available_funds: 536_775,
    buying_power: 1_073_550,
  },
  exposure: {},
  violations: [],
  positions: [
    {
      id: 18,
      ticker: "AAOI",
      structure: "Bull Call Spread $105.0/$130.0",
      structure_type: "Vertical",
      direction: "LONG",
      contracts: 25,
      expiry: "2026-03-20",
      entry_date: "2026-03-03",
      entry_cost: 2735,
      market_value: 26450,
      market_price: 10.58,
      market_price_is_calculated: false,
      avg_cost: 1.09,
      risk_profile: "defined",
      target: null,
      stop: null,
      legs: [
        {
          direction: "LONG",
          contracts: 25,
          type: "Call",
          strike: 105,
          avg_cost: 910,
          entry_cost: 22750,
          market_price: 14.67,
          market_price_is_calculated: false,
          market_value: 36675,
        },
        {
          direction: "SHORT",
          contracts: 25,
          type: "Call",
          strike: 130,
          avg_cost: -801,
          entry_cost: -20015,
          market_price: 4.09,
          market_price_is_calculated: false,
          market_value: 10225,
        },
      ],
    },
    {
      id: 84,
      ticker: "EWY",
      structure: "Covered Call $165.0",
      structure_type: "Covered Call",
      direction: "COMBO",
      contracts: 25,
      expiry: "2026-08-07",
      entry_date: "2026-07-31",
      entry_cost: 412_132,
      market_value: 404_000,
      risk_profile: "defined",
      max_risk: null,
      target: null,
      stop: null,
      kelly_optimal: null,
      legs: [
        {
          direction: "LONG",
          contracts: 2_500,
          type: "Stock",
          strike: 0,
          avg_cost: 169.86,
          entry_cost: 424_650,
          market_price: 170.65,
          market_value: 426_625,
        },
        {
          direction: "SHORT",
          contracts: 25,
          type: "Call",
          strike: 165,
          avg_cost: -500.72,
          entry_cost: -12_518,
          market_price: 9.05,
          market_value: -22_625,
        },
      ],
    },
  ],
};

const PRICES = {
  EWY: {
    symbol: "EWY",
    last: 170.65,
    lastIsCalculated: false,
    bid: 170.6,
    ask: 170.7,
    bidSize: 100,
    askSize: 90,
    volume: 2_500_000,
    high: 171,
    low: 168,
    open: 169,
    close: 169.4,
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
  AAOI: {
    symbol: "AAOI",
    last: 17.41,
    lastIsCalculated: false,
    bid: 17.38,
    ask: 17.44,
    bidSize: 100,
    askSize: 90,
    volume: 1_245_000,
    high: 17.89,
    low: 16.72,
    open: 17.05,
    close: 17.22,
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
  AAOI_20260320_105_C: {
    symbol: "AAOI_20260320_105_C",
    last: 14.67,
    lastIsCalculated: false,
    bid: 13.8,
    ask: 16.2,
    bidSize: 12,
    askSize: 9,
    volume: 232,
    high: 15.89,
    low: 14.8,
    open: 15.25,
    close: 24.98,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: 0.42,
    gamma: 0.03,
    theta: -0.12,
    vega: 0.21,
    impliedVol: 0.64,
    undPrice: 17.4,
    timestamp: new Date().toISOString(),
  },
  AAOI_20260320_130_C: {
    symbol: "AAOI_20260320_130_C",
    last: 4.09,
    lastIsCalculated: false,
    bid: 3.9,
    ask: 4.25,
    bidSize: 8,
    askSize: 11,
    volume: 146,
    high: 4.3,
    low: 3.7,
    open: 4.05,
    close: 4.21,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: 0.17,
    gamma: 0.01,
    theta: -0.05,
    vega: 0.12,
    impliedVol: 0.58,
    undPrice: 17.4,
    timestamp: new Date().toISOString(),
  },
};

async function installMockWebSocket(page: import("@playwright/test").Page) {
  await page.addInitScript((priceFixtures) => {
    const NativeWebSocket = window.WebSocket;

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
      listeners = new Map<string, Set<(event: unknown) => void>>();

      constructor(url: string) {
        this.url = url;
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.({});
          this.dispatch("open", {});
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
        let message: {
          action?: string;
          symbols?: string[];
          contracts?: Array<{ symbol: string; expiry: string; strike: number; right: "C" | "P" }>;
        };
        try {
          message = JSON.parse(raw);
        } catch {
          return;
        }
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
        this.dispatch("close", {});
      }

      emit(payload: unknown) {
        const event = { data: JSON.stringify(payload) };
        this.onmessage?.(event);
        this.dispatch("message", event);
      }

      addEventListener(type: string, listener: (event: unknown) => void) {
        const listeners = this.listeners.get(type) ?? new Set<(event: unknown) => void>();
        listeners.add(listener);
        this.listeners.set(type, listeners);
      }

      removeEventListener(type: string, listener: (event: unknown) => void) {
        this.listeners.get(type)?.delete(listener);
      }

      dispatch(type: string, event: unknown) {
        for (const listener of this.listeners.get(type) ?? []) listener(event);
      }
    }

    const RoutedWebSocket = function (
      url: string | URL,
      protocols?: string | string[],
    ): WebSocket {
      if (String(url).includes("/_next/webpack-hmr")) {
        return protocols == null
          ? new NativeWebSocket(url)
          : new NativeWebSocket(url, protocols);
      }
      return new MockWebSocket(String(url)) as unknown as WebSocket;
    } as unknown as typeof WebSocket;
    Object.defineProperties(RoutedWebSocket, {
      CONNECTING: { value: 0 },
      OPEN: { value: 1 },
      CLOSING: { value: 2 },
      CLOSED: { value: 3 },
    });
    window.WebSocket = RoutedWebSocket;
  }, PRICES);
}

async function stubApis(page: import("@playwright/test").Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/portfolio") return json(PORTFOLIO_WITH_VERTICAL);
    if (path === "/api/orders") {
      return json({
        last_sync: new Date().toISOString(),
        open_orders: [],
        executed_orders: [],
        open_count: 0,
        executed_count: 0,
      });
    }
    if (path === "/api/regime") return json({ score: 15, cri: { score: 15 } });
    if (path === "/api/ib-status") return json({ connected: false });
    if (path === "/api/ib/ws-ticket") return json({ ticket: "test" });
    if (path === "/api/blotter") {
      return json({
        as_of: new Date().toISOString(),
        summary: { realized_pnl: 0 },
        closed_trades: [],
        open_trades: [],
      });
    }
    if (path === "/api/service-health") return json({ services: [] });
    if (path === "/api/profile") return json({ username: "Operator" });
    if (path === "/api/watchlist") return json({ symbols: [] });
    if (path === "/api/flex-token") return json({ remaining: 240 });
    return json({});
  });
}

test.describe("Portfolio order ticket quote telemetry", () => {
  test("shows BID, MID, ASK ordering and raw spread percent in the instrument ticket", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await installMockWebSocket(page);
    await stubApis(page);

    await page.goto("/portfolio");

    const expandLegs = page.locator('button[aria-label="Expand legs for AAOI"]').first();
    await expandLegs.waitFor({ timeout: 10_000 });

    await expandLegs.click();

    const legTrigger = page.locator(".leg-clickable", { hasText: "LONG 25x Call $105" }).first();
    await legTrigger.waitFor({ timeout: 5_000 });
    await legTrigger.click();

    const modal = page.locator(".instrument-detail-modal");
    await modal.waitFor({ timeout: 5_000 });

    const labels = await modal.locator(".price-bar .price-bar-item .price-bar-label").allTextContents();
    expect(labels.slice(1, 5)).toEqual(["BID", "MID", "ASK", "SPREAD"]);

    const bidText = await modal
      .locator(".price-bar-item")
      .filter({ hasText: "BID" })
      .locator(".price-bar-value")
      .textContent();
    const askText = await modal
      .locator(".price-bar-item")
      .filter({ hasText: "ASK" })
      .locator(".price-bar-value")
      .textContent();
    const spreadValue = modal
      .locator(".price-bar-item")
      .filter({ hasText: "SPREAD" })
      .locator(".price-bar-value");

    const bid = parsePrice(bidText);
    const ask = parsePrice(askText);
    const spread = ask - bid;
    const mid = (bid + ask) / 2;
    const expectedSpread = `${formatUsd(spread)} / ${((spread / mid) * 100).toFixed(2)}%`;

    await expect(spreadValue).toHaveText(expectedSpread);
  });

  test("covered-call equity leg submits a stock-only close and retains the short call", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1200 });
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await installMockWebSocket(page);
    await stubApis(page);

    let submittedPayload: Record<string, unknown> | null = null;
    await page.route("**/api/orders/place", async (route) => {
      submittedPayload = await route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", orderId: 99, permId: 199 }),
      });
    });

    await page.goto("/portfolio");
    await page.getByLabel("Expand legs for EWY").waitFor({ timeout: 10_000 });

    await page.getByLabel("Expand legs for EWY").click();
    await page.locator(".leg-clickable", { hasText: "LONG 2500x Stock" }).click();

    const dialog = page.getByRole("dialog", { name: "EWY Stock" });
    await expect(dialog).toBeVisible();
    await expect(dialog).not.toContainText("$0 Stock");
    await expect(dialog).not.toContainText("2026-08-07");
    await expect(dialog.getByPlaceholder("Shares")).toHaveValue("2500");
    await expect(dialog).toContainText("BID 170.60");

    await dialog.locator(".modify-price-input").fill("170");
    await dialog.getByRole("button", { name: /place order/i }).click();

    const summary = dialog.locator(".order-confirm-summary");
    await expect(summary).toContainText("SELL 2500 EWY Stock @ $170.00");
    await expect(summary).toContainText("Proceeds:");
    await expect(summary).toContainText("$425,000");
    await expect(summary).toContainText("Est. Realized P&L:");
    await expect(summary).toContainText("$350");
    await expect(summary).toContainText("25 retained short calls uncovered");
    await expect(summary).toContainText("UNAVAILABLE");
    await expect(summary).not.toContainText("EWY $0 P");
    await expect(summary).not.toContainText("$42,500,000");
    await dialog.screenshot({ path: "/tmp/radon-covered-call-stock-close.png" });

    await dialog.getByRole("button", { name: /confirm order/i }).click();
    await expect.poll(() => submittedPayload).not.toBeNull();
    expect(submittedPayload).toEqual({
      type: "stock",
      symbol: "EWY",
      action: "SELL",
      quantity: 2_500,
      limitPrice: 170,
      tif: "DAY",
    });

  });
});
