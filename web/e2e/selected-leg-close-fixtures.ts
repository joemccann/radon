import { expect, type Page, type TestInfo } from "@playwright/test";

export const POSITION_ID = 812;
const NOW = "2026-10-12T17:00:00.000Z";
const EXPIRY = "20261030";
const TICKER = "CRWD";
const POSITION = {
  id: POSITION_ID, ticker: TICKER, structure: "Risk Reversal Put Spread", structure_type: "Risk Reversal Put Spread",
  risk_profile: "undefined", expiry: "2026-10-30", contracts: 100, direction: "COMBO",
  entry_cost: -3162, max_risk: null, market_value: 37000, entry_date: "2026-10-05",
  kelly_optimal: null, target: null, stop: null,
  legs: [
    { direction: "SHORT", type: "Call", strike: 270, contracts: 50, entry_cost: 61162, avg_cost: 1223.24, market_price: 8.8, market_value: 44000 },
    { direction: "SHORT", type: "Put", strike: 230, contracts: 100, entry_cost: 11969, avg_cost: 119.69, market_price: 1.7, market_value: 17000 },
    { direction: "LONG", type: "Put", strike: 260, contracts: 100, entry_cost: 69969, avg_cost: 699.69, market_price: 9.8, market_value: 98000 },
  ],
};

type Placement = { url: string; method: string; body: unknown };
export async function installSelectedLegFixtures(page: Page, variant: "put" | "call" | "mixed" = "put") {
  const position = variant === "mixed" ? {
    ...POSITION, entry_cost: null, basis_source: "mixed",
    legs: POSITION.legs.map((leg, index) => ({
      ...leg, basis_source: index === 2 ? "session_fills" : "ib",
    })),
  } : variant === "put" ? POSITION : {
    ...POSITION, structure: "Risk Reversal Call Spread", structure_type: "Risk Reversal Call Spread",
    legs: [
      { ...POSITION.legs[0], type: "Put", strike: 230 },
      { ...POSITION.legs[1], type: "Call", strike: 280 },
      { ...POSITION.legs[2], type: "Call", strike: 250 },
    ],
  };
  const placements: Placement[] = [];
  const pageErrors: string[] = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  await page.clock.setFixedTime(new Date(NOW));
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  await page.route("**/api/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/orders/place") {
      placements.push({ url: request.url(), method: request.method(), body: request.postDataJSON() });
      await route.fulfill({ json: { status: "ok", orderId: 9812, permId: 19812, initialStatus: "Submitted", message: "Isolated close fixture" } });
      return;
    }
    const fixtures: Record<string, unknown> = {
      "/api/portfolio": {
        bankroll: 1_000_000, peak_value: 1_000_000, positions: [position], position_count: 1,
        total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
        defined_risk_count: 0, undefined_risk_count: 1, avg_kelly_optimal: null,
        exposure: {}, violations: [], last_sync: NOW,
        account_summary: { net_liquidation: 1_000_000, settled_cash: 900_000, buying_power: 2_000_000, excess_liquidity: 900_000, maintenance_margin: 100_000 },
      },
      "/api/orders": { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0, last_sync: NOW },
      "/api/ib/ws-ticket": { ticket: "isolated-selected-leg-close" },
      "/api/ib-status": { connected: true },
      "/api/regime": { score: 15, cri: { score: 15 } },
      "/api/blotter": { closed_trades: [], open_trades: [], summary: { realized_pnl: 0 } },
      "/api/ticker/info": { uw_info: { name: "CrowdStrike Holdings", sector: "Technology" }, stock_state: {}, profile: {}, stats: {} },
      "/api/options/expirations": { symbol: TICKER, expirations: [EXPIRY] },
      "/api/options/chain": { symbol: TICKER, expiry: EXPIRY, strikes: position.legs.map(leg => leg.strike), exchange: "SMART", multiplier: "100" },
      "/api/risk-free-rate": { rate: 0.04 },
      "/api/watchlist": { watchlist: [] },
      "/api/service-health": { services: [] },
    };
    const body = fixtures[path];
    await route.fulfill({ status: body === undefined ? 503 : 200, json: body ?? { error: "Unavailable in isolated selected-leg close fixture" } });
  });
  const quote = (symbol: string, price: number) => ({
    symbol, last: price, bid: price - 0.05, ask: price + 0.05, close: price, timestamp: NOW,
    lastIsCalculated: false, volume: 1200, delta: 0.4, impliedVol: 0.45, undPrice: 259.96,
  });
  await page.routeWebSocket(/(?:localhost|127\.0\.0\.1):(?:18765|8765)|\/ws(?:\?|$)/, socket => {
    socket.onMessage(raw => {
      const message = JSON.parse(raw.toString());
      if (message.action !== "subscribe") return;
      socket.send(JSON.stringify({ type: "status", ib_connected: true, market_data_degraded: false, subscriptions: [TICKER] }));
      const updates: Record<string, unknown> = { [TICKER]: quote(TICKER, 259.96) };
      for (const leg of position.legs) {
        const key = `${TICKER}_${EXPIRY}_${leg.strike}_${leg.type === "Call" ? "C" : "P"}`;
        updates[key] = quote(key, leg.market_price);
      }
      socket.send(JSON.stringify({ type: "batch", updates }));
    });
  });
  await page.goto(`/${TICKER}?posId=${POSITION_ID}&deck=p`, { waitUntil: "domcontentloaded" });
  await expect(page.locator(".pos-legs-table tbody tr")).toHaveCount(3);
  return { placements, pageErrors };
}

export async function captureSelectedLegView(page: Page, testInfo: TestInfo, name: string) {
  const path = testInfo.outputPath(`${name}.png`);
  // Dismiss the relay connection toast through its real control so it does
  // not obscure the closing ticket; hide only Next's development UI at capture.
  await page.getByRole("button", { name: "Dismiss", exact: true }).evaluateAll(buttons => {
    for (const button of buttons) (button as HTMLButtonElement).click();
  });
  await page.screenshot({ path, style: "nextjs-portal { visibility: hidden; }" });
  await testInfo.attach(name, { path, contentType: "image/png" });
}

export async function closePutSubset(page: Page, testInfo: TestInfo) {
  const fixture = await installSelectedLegFixtures(page);
  await page.getByRole("checkbox", { name: "Select SHORT Put $230", exact: true }).check();
  await page.getByRole("checkbox", { name: "Select LONG Put $260", exact: true }).check();
  await expect(page.getByTestId("pos-leg-select-0")).not.toBeChecked();
  const cta = page.getByTestId("pos-trade-selected");
  await expect(cta).toHaveText("Close Selected Legs (2)");
  await cta.scrollIntoViewIfNeeded();
  await captureSelectedLegView(page, testInfo, "selected-put-spread");
  await cta.click();
  const ticket = page.getByTestId("position-trade-ticket");
  await expect(ticket.locator(".position-trade-eyebrow")).toHaveText("Close Selected Legs");
  await expect(ticket.getByTestId("position-trade-qty")).toHaveValue("100");
  // Crossed executable markets for just the two puts, excluding the call.
  await expect(ticket.locator(".modify-quick-buttons")).toContainText("BID $8.00");
  await expect(ticket.locator(".modify-quick-buttons")).toContainText("MID $8.10");
  await expect(ticket.locator(".modify-quick-buttons")).toContainText("ASK $8.20");
  const selected = ticket.getByTestId("position-trade-selected-legs");
  await expect(selected).toContainText("Buy to Close");
  await expect(selected).toContainText("Sell to Close");
  await expect(selected).toContainText("230");
  await expect(selected).toContainText("260");
  await expect(selected).not.toContainText("270");
  const remaining = ticket.getByTestId("position-trade-remaining-legs");
  await expect(remaining).toContainText("270");
  await expect(remaining).toContainText("50");
  await expect(ticket.getByRole("button", { name: "BUY", exact: true })).toHaveCount(0);
  await ticket.getByTestId("position-trade-limit").fill("8.10");
  await ticket.locator(".position-trade-head").scrollIntoViewIfNeeded();
  await captureSelectedLegView(page, testInfo, "put-spread-close-ticket");
  expect(fixture.placements).toHaveLength(0);
  await ticket.getByRole("button", { name: "Review Order", exact: true }).click();
  const summary = ticket.getByTestId("order-confirm-summary");
  await expect(summary).toBeVisible();
  await expect(summary).toContainText("SELL");
  await expect(summary).toContainText("230");
  await expect(summary).toContainText("260");
  await expect(summary).not.toContainText("270");
  expect(fixture.placements).toHaveLength(0);
  const confirm = ticket.getByRole("button", { name: "Confirm Order", exact: true });
  await expect(confirm).toBeEnabled();
  await confirm.scrollIntoViewIfNeeded();
  await expect(confirm).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  await captureSelectedLegView(page, testInfo, "put-spread-close-review");
  await confirm.click();
  await expect.poll(() => fixture.placements.length).toBe(1);
  expect(fixture.placements).toEqual([{ url: new URL("/api/orders/place", page.url()).href, method: "POST", body: {
    type: "combo", symbol: TICKER, action: "SELL", quantity: 100, limitPrice: 8.1, tif: "DAY",
    legs: [
      { expiry: EXPIRY, strike: 230, right: "P", action: "SELL", ratio: 1 },
      { expiry: EXPIRY, strike: 260, right: "P", action: "BUY", ratio: 1 },
    ],
  } }]);
  expect(fixture.pageErrors).toEqual([]);
}

export async function coverSingleShortCall(page: Page, testInfo: TestInfo) {
  const fixture = await installSelectedLegFixtures(page);
  await page.getByRole("checkbox", { name: "Select SHORT Call $270", exact: true }).check();
  await page.getByTestId("pos-trade-selected").click();
  const ticket = page.getByTestId("position-trade-ticket");
  await expect(ticket.getByTestId("position-trade-qty")).toHaveValue("50");
  await expect(ticket.getByTestId("position-trade-selected-legs")).toContainText("Buy to Close");
  await expect(ticket.getByTestId("position-trade-remaining-legs")).toContainText("230");
  await expect(ticket.getByTestId("position-trade-remaining-legs")).toContainText("260");
  await ticket.getByTestId("position-trade-limit").fill("8.80");
  await ticket.getByRole("button", { name: "Review Order", exact: true }).click();
  expect(fixture.placements).toHaveLength(0);
  const confirm = ticket.getByRole("button", { name: "Confirm Order", exact: true });
  await expect(confirm).toBeEnabled();
  await confirm.scrollIntoViewIfNeeded();
  await captureSelectedLegView(page, testInfo, "short-call-cover-review");
  await confirm.click();
  await expect.poll(() => fixture.placements.length).toBe(1);
  expect(fixture.placements[0].method).toBe("POST");
  expect(fixture.placements[0].body).toMatchObject({
    type: "option", symbol: TICKER, action: "BUY", quantity: 50, limitPrice: 8.8, tif: "DAY", expiry: EXPIRY, strike: 270, right: "C",
  });
  expect(fixture.pageErrors).toEqual([]);
}
