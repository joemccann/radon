import { expect, test, type Page, type Route, type WebSocketRoute } from "@playwright/test";

const INSUFFICIENT = ["ADBE", "CBRS", "GLD", "META", "SLV", "SOFI", "SPCX", "VIX", "WULF"];

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
  risk_budget: {
    clusters: [],
    breaches: [],
    aggregate_exposure: 0,
    insufficient_data: INSUFFICIENT,
    corr_threshold: 0.7,
    book_budget: 0.025,
  },
};

const ORDERS = {
  last_sync: new Date().toISOString(),
  open_orders: [
    {
      orderId: 72,
      permId: 653611397,
      symbol: "CBRS",
      contract: {
        conId: 742392001,
        symbol: "CBRS",
        secType: "OPT",
        strike: 230,
        right: "P",
        expiry: "2026-08-14",
      },
      action: "BUY",
      orderType: "LMT",
      totalQuantity: 35,
      limitPrice: 3.5,
      auxPrice: null,
      status: "Submitted",
      filled: 0,
      remaining: 35,
      avgFillPrice: null,
      tif: "DAY",
    },
  ],
  executed_orders: [],
  open_count: 1,
  executed_count: 0,
};

async function stubApis(page: import("@playwright/test").Page) {
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS) }),
  );
  await page.route("**/api/portfolio**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ score: 15, cri: { score: 15 } }),
    }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
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

/**
 * Measure the header's title and gate in a SINGLE evaluate.
 *
 * Two sequential `boundingBox()` calls are two round-trips, and the modal
 * reflows once shortly after it opens (the mono webfont swaps in and every
 * row above the banner re-measures). Straddling that reflow compares a
 * pre-shift `title.y` against a post-shift `gate.y` and reports a gap no
 * single frame ever had - main measured 5.85 against this 6px tolerance
 * purely by luck. Reading both rects in one frame, after fonts settle, is
 * what "on one row" actually means, and it is stricter than the old form:
 * a genuine wrap puts the gap at a full row height (~24px), not 4px.
 */
async function headerGeometry(banner: import("@playwright/test").Locator) {
  await banner.page().evaluate(() => document.fonts?.ready);
  return banner.evaluate((el) => {
    const rect = (selector: string) => {
      const node = el.querySelector(selector);
      if (!node) return null;
      const { x, y, width } = node.getBoundingClientRect();
      return { x, y, width };
    };
    return { title: rect(".crb-title"), gate: rect(".crb-gate") };
  });
}

test("modify modal keeps correlation risk header on one row with ticker chips", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.addInitScript(() => {
    localStorage.setItem("theme", "dark");
  });
  await stubApis(page);
  await page.goto("/orders");

  const row = page.locator("tbody tr").filter({ hasText: "CBRS" }).first();
  await expect(row).toBeVisible({ timeout: 10_000 });
  await row.getByRole("button", { name: "MODIFY" }).click();

  const banner = page.getByTestId("correlation-risk-banner");
  await expect(banner).toBeVisible();
  await expect(banner.locator(".crb-headline")).toContainText(/Gate 3:.*correlation/i);
  await expect(banner.locator(".crb-detail")).toContainText(/price history/i);

  const { title: titleBox, gate: gateBox } = await headerGeometry(banner);
  expect(titleBox).toBeTruthy();
  expect(gateBox).toBeTruthy();
  expect(Math.abs((titleBox?.y ?? 0) - (gateBox?.y ?? 0))).toBeLessThan(6);
  expect((gateBox?.x ?? 0)).toBeGreaterThan((titleBox?.x ?? 0) + (titleBox?.width ?? 0) - 4);

  await expect(banner.locator(".crb-ticker")).toHaveText(INSUFFICIENT);
  const dialog = page.locator(".modify-dialog");
  await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Modify Order" })).toBeVisible();

  await page.setViewportSize({ width: 393, height: 852 });
  const { title: titleMobile, gate: gateMobile } = await headerGeometry(banner);
  expect(titleMobile).toBeTruthy();
  expect(gateMobile).toBeTruthy();
  expect(Math.abs((titleMobile?.y ?? 0) - (gateMobile?.y ?? 0))).toBeLessThan(6);
});


test.describe('REL-295 previous-close retry durability', () => {

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

});


test.describe('REL-236 broker feed safety', () => {

/** REL-236 / NF-3: an open relay is not proof the broker feed is connected. */
test("broker disconnect disarms a fresh modify ticket until recovery", async ({ page }, testInfo) => {
  const now = "2026-09-29T15:00:00Z";
  await page.clock.install({ time: new Date(now) });
  const sockets: WebSocketRoute[] = [];
  const price = { symbol: "AAPL", last: 103, bid: 102, ask: 104, close: 99,
    timestamp: now, bidSize: 100, askSize: 100 };
  await page.routeWebSocket(/:8765/, socket => {
    sockets.push(socket);
    socket.send(JSON.stringify({ type: "status", ib_connected: true, subscriptions: [] }));
    socket.onMessage(() => socket.send(JSON.stringify({ type: "batch", updates: { AAPL: price } })));
  });
  await page.route(/^https?:\/\/(?!localhost:|127\.0\.0\.1:)/, route => route.abort());
  const modifications: { url: string; method: string; body: unknown }[] = [];
  const order = {
    orderId: 11, permId: 1101, symbol: "AAPL", contract: { conId: 265598, symbol: "AAPL", secType: "STK" },
    action: "SELL", orderType: "LMT", totalQuantity: 10, limitPrice: 104,
    auxPrice: null, status: "Submitted", filled: 0, remaining: 10, avgFillPrice: 0, tif: "DAY",
  };
  await page.route("**/api/**", route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/orders/modify") {
      modifications.push({ url: request.url(), method: request.method(), body: request.postDataJSON() });
      return route.fulfill({ json: { status: "ok", orderId: 11 } });
    }
    const body = path === "/api/orders"
      ? { last_sync: now, open_orders: [order], executed_orders: [], open_count: 1, executed_count: 0 }
      : path === "/api/portfolio" ? {
        bankroll: 100_000, peak_value: 100_000, last_sync: now, position_count: 1,
        total_deployed_pct: 1, total_deployed_dollars: 1000, remaining_capacity_pct: 99,
        defined_risk_count: 1, undefined_risk_count: 0, exposure: {}, violations: [],
        positions: [{ id: 1, ticker: "AAPL", structure: "Stock (10 shares)", structure_type: "Stock",
          risk_profile: "equity", direction: "LONG", contracts: 10, expiry: "N/A", entry_cost: 1000,
          market_value: 1030, entry_date: "2026-09-28", legs: [{ direction: "LONG", contracts: 10,
            type: "Stock", strike: 0, entry_cost: 1000, avg_cost: 100, market_price: 103, market_value: 1030 }] }],
      }
      : path === "/api/ib-status" ? { connected: true }
      : path === "/api/ws-ticket" ? { ticket: "fixture" }
      : {};
    return route.fulfill({ json: body });
  });
  await page.goto("/orders");
  const row = page.locator("tbody tr").filter({ hasText: "AAPL" }).first();
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "MODIFY", exact: true }).click();
  await page.locator("#modify-price-input").fill("103");
  const submit = page.getByRole("button", { name: "Modify Order", exact: true });
  await expect(submit).toBeEnabled();
  expect(sockets.length).toBeGreaterThan(0);
  for (const socket of sockets) socket.send(JSON.stringify({ type: "status", ib_connected: false }));
  await expect(submit).toBeDisabled();
  await expect(page.getByText("Live feed disconnected. Submit disabled until quotes resume.")).toBeVisible();
  await submit.dispatchEvent("click");
  await page.clock.runFor(1_000);
  expect(modifications).toEqual([]);
  await submit.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("broker-disconnect-blocked.png"), fullPage: true });
  for (const socket of sockets) {
    socket.send(JSON.stringify({ type: "status", ib_connected: true }));
    socket.send(JSON.stringify({ type: "batch", updates: { AAPL: { ...price, timestamp: new Date(now).toISOString() } } }));
  }
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect.poll(() => modifications.length).toBe(1);
  expect(modifications[0]).toEqual({
    url: new URL("/api/orders/modify", page.url()).href,
    method: "POST", body: { orderId: 11, permId: 1101, newPrice: 103 },
  });
});

});


test("REL-318: degraded market data disarms a fresh modify ticket with the broker socket open", async ({ page }, testInfo) => {
  const now = "2026-09-29T15:00:00Z";
  await page.clock.install({ time: new Date(now) });
  const sockets: WebSocketRoute[] = [];
  const price = { symbol: "AAPL", last: 103, bid: 102, ask: 104, close: 99,
    timestamp: now, bidSize: 100, askSize: 100 };
  await page.routeWebSocket(/:8765/, socket => {
    sockets.push(socket);
    socket.send(JSON.stringify({ type: "status", ib_connected: true, market_data_degraded: false, subscriptions: [] }));
    socket.onMessage(() => socket.send(JSON.stringify({ type: "batch", updates: { AAPL: price } })));
  });
  await page.route(/^https?:\/\/(?!localhost:|127\.0\.0\.1:)/, route => route.abort());
  const modifications: { url: string; method: string; body: unknown }[] = [];
  const order = {
    orderId: 11, permId: 1101, symbol: "AAPL", contract: { conId: 265598, symbol: "AAPL", secType: "STK" },
    action: "SELL", orderType: "LMT", totalQuantity: 10, limitPrice: 104,
    auxPrice: null, status: "Submitted", filled: 0, remaining: 10, avgFillPrice: 0, tif: "DAY",
  };
  await page.route("**/api/**", route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/orders/modify") {
      modifications.push({ url: request.url(), method: request.method(), body: request.postDataJSON() });
      return route.fulfill({ json: { status: "ok", orderId: 11 } });
    }
    const body = path === "/api/orders"
      ? { last_sync: now, open_orders: [order], executed_orders: [], open_count: 1, executed_count: 0 }
      : path === "/api/portfolio" ? {
        bankroll: 100_000, peak_value: 100_000, last_sync: now, position_count: 1,
        total_deployed_pct: 1, total_deployed_dollars: 1000, remaining_capacity_pct: 99,
        defined_risk_count: 1, undefined_risk_count: 0, exposure: {}, violations: [],
        positions: [{ id: 1, ticker: "AAPL", structure: "Stock (10 shares)", structure_type: "Stock",
          risk_profile: "equity", direction: "LONG", contracts: 10, expiry: "N/A", entry_cost: 1000,
          market_value: 1030, entry_date: "2026-09-28", legs: [{ direction: "LONG", contracts: 10,
            type: "Stock", strike: 0, entry_cost: 1000, avg_cost: 100, market_price: 103, market_value: 1030 }] }],
      }
      : path === "/api/ib-status" ? { connected: true }
      : path === "/api/ws-ticket" ? { ticket: "fixture" }
      : {};
    return route.fulfill({ json: body });
  });
  await page.goto("/orders");
  const row = page.locator("tbody tr").filter({ hasText: "AAPL" }).first();
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "MODIFY", exact: true }).click();
  await page.locator("#modify-price-input").fill("103");
  const submit = page.getByRole("button", { name: "Modify Order", exact: true });
  await expect(submit).toBeEnabled();
  expect(sockets.length).toBeGreaterThan(0);
  for (const socket of sockets) socket.send(JSON.stringify({ type: "status", ib_connected: true, market_data_degraded: true }));
  await expect(submit).toBeDisabled();
  await expect(page.getByRole("status", { name: "System telemetry" })).toContainText("Degraded");
  await expect(page.getByText("Live feed disconnected. Submit disabled until quotes resume.")).toBeVisible();
  await submit.dispatchEvent("click");
  await page.clock.runFor(1_000);
  expect(modifications).toEqual([]);
  await submit.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("market-data-degraded-blocked.png"), fullPage: true });
  for (const socket of sockets) {
    socket.send(JSON.stringify({ type: "status", ib_connected: true, market_data_degraded: false }));
    socket.send(JSON.stringify({ type: "batch", updates: { AAPL: { ...price, timestamp: new Date(now).toISOString() } } }));
  }
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect.poll(() => modifications.length).toBe(1);
  expect(modifications[0]).toEqual({
    url: new URL("/api/orders/modify", page.url()).href,
    method: "POST", body: { orderId: 11, permId: 1101, newPrice: 103 },
  });
});
