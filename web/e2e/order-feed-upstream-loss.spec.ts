import { expect, test, type WebSocketRoute } from "@playwright/test";

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
