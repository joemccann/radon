import { expect, test, type Page } from "@playwright/test";

const TICKER = "AAOI";
const EXPIRY = "20270115";
const NOW = "2026-10-06T14:20:00.000Z";

async function stubRiskReversal(page: Page, placed: Record<string, unknown>[]) {
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  await page.clock.setFixedTime(new Date(NOW));
  // Every API request stays synthetic, including the final order mutation.
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (!["localhost", "127.0.0.1"].includes(url.hostname)) return route.abort();
    if (!url.pathname.startsWith("/api/")) return route.continue();
    let body: unknown;
    if (url.pathname === "/api/orders/place") {
      placed.push(route.request().postDataJSON());
      body = { ok: true, orderId: 9999 };
    } else if (url.pathname === "/api/portfolio") {
      body = { positions: [], last_sync: NOW, bankroll: 100000, peak_value: 100000,
        total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
        position_count: 0, defined_risk_count: 0, undefined_risk_count: 0,
        avg_kelly_optimal: null, exposure: {}, violations: [] };
    } else if (url.pathname === "/api/orders") {
      body = { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0, last_sync: NOW };
    } else if (url.pathname === "/api/blotter") {
      body = { as_of: NOW, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] };
    } else if (url.pathname === "/api/options/expirations") {
      body = { symbol: TICKER, expirations: [EXPIRY] };
    } else if (url.pathname === "/api/options/chain") {
      body = { symbol: TICKER, expiry: EXPIRY, exchange: "SMART", strikes: [120, 125, 130, 135], multiplier: "100" };
    } else if (url.pathname.startsWith("/api/ticker/")) {
      body = { uw_info: { name: "Applied Optoelectronics", sector: "Technology", description: "Fixture" }, stock_state: {}, profile: {}, stats: {} };
    } else {
      return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Fixture unavailable" }) });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  const quote = (symbol: string, bid: number, ask: number, delta: number) => ({
    symbol, last: (bid + ask) / 2, bid, ask, close: (bid + ask) / 2,
    timestamp: NOW, impliedVol: 0.89, delta, undPrice: 128,
  });
  await page.routeWebSocket(/(?:localhost|127\.0\.0\.1):8765|\/ws(?:\?|$)/, (socket) => {
    socket.onMessage((raw) => {
      if (JSON.parse(raw.toString()).action !== "subscribe") return;
      socket.send(JSON.stringify({ type: "status", ib_connected: true, subscriptions: [TICKER] }));
      socket.send(JSON.stringify({ type: "batch", updates: {
        AAOI: quote(TICKER, 127.95, 128.05, 1),
        [`${TICKER}_${EXPIRY}_130_C`]: quote(`${TICKER}_${EXPIRY}_130_C`, 8.10, 8.85, 0.47),
        [`${TICKER}_${EXPIRY}_125_P`]: quote(`${TICKER}_${EXPIRY}_125_P`, 7.95, 8.70, -0.45),
      } }));
    });
  });
}

test.use({ viewport: { width: 393, height: 852 } });

for (const [side, value] of [["bid", "-0.90"], ["mid", "-0.15"]] as const) {
  test(`risk reversal preserves negative ${side} preset through confirmed order`, async ({ page }, testInfo) => {
    const placed: Record<string, unknown>[] = [];
    await stubRiskReversal(page, placed);
    await page.goto(`/${TICKER}?tab=chain`);
    await page.getByTestId("mobile-chain-call-130").click();
    await page.getByTestId("mobile-chain-detail-sell").click();
    await page.getByTestId("mobile-chain-put-125").click();
    await page.getByTestId("mobile-chain-detail-buy").click();
    await page.getByTestId("mobile-chain-pending-strip").click();

    await expect(page.getByTestId("mobile-order-ticket")).toBeVisible();
    for (const [quoteSide, quoteValue] of [["bid", "-0.90"], ["mid", "-0.15"], ["ask", "0.60"]]) {
      await expect(page.getByTestId(`mobile-order-ticket-quote-${quoteSide}`)).toHaveText(`$${quoteValue}`);
    }
    await page.getByTestId(`mobile-order-ticket-quote-${side}`).click();
    await expect(page.getByTestId("mobile-order-ticket-price-input")).toHaveValue(value);
    await page.screenshot({ path: testInfo.outputPath(`negative-${side}-preset.png`), fullPage: true });
    expect(placed).toHaveLength(0);
    await page.getByTestId("mobile-order-ticket-review").click();
    await expect(page.getByTestId("mobile-order-ticket-back")).toBeVisible();
    expect(placed).toHaveLength(0);
    await page.getByTestId("ticket-unbounded-ack").check();
    await page.getByTestId("mobile-order-ticket-submit").click();
    await expect(page.getByTestId("mobile-order-ticket-success")).toBeVisible();
    expect(placed).toEqual([{
      type: "combo", symbol: TICKER, action: "BUY", quantity: 1,
      limitPrice: Number(value), tif: "DAY", legs: [
        { symbol: TICKER, secType: "OPT", expiry: EXPIRY, strike: 130, right: "CALL", action: "SELL", ratio: 1, limitPrice: 8.475 },
        { symbol: TICKER, secType: "OPT", expiry: EXPIRY, strike: 125, right: "PUT", action: "BUY", ratio: 1, limitPrice: 8.325 },
      ],
    }]);
  });
}
