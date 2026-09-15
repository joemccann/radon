import { test, expect, type Locator, type Page, type WebSocketRoute } from "@playwright/test";

const TICKER = "MU";
const EXPIRIES = ["20261218", "20270115"];
const STRIKES = Array.from({ length: 61 }, (_, index) => 90 + index);

async function installFixtures(page: Page) {
  let last = 120;
  const sockets = new Set<WebSocketRoute>();
  const quote = (symbol: string, price: number) => ({
    symbol, last: price, bid: price - 0.05, ask: price + 0.05,
    close: symbol === TICKER ? 119 : price, timestamp: new Date().toISOString(),
    lastIsCalculated: false, delta: 0.4, impliedVol: 0.45,
  });
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const fixtures: Record<string, unknown> = {
      "/api/portfolio": { bankroll: 100000, peak_value: 100000, positions: [], position_count: 0, total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100, defined_risk_count: 0, undefined_risk_count: 0, avg_kelly_optimal: null, exposure: {}, violations: [], last_sync: new Date().toISOString() },
      "/api/orders": { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0, last_sync: new Date().toISOString() },
      "/api/ib/ws-ticket": { ticket: "chain-test" },
      "/api/ib-status": { connected: true },
      "/api/regime": { score: 15, cri: { score: 15 } },
      "/api/blotter": { closed_trades: [], open_trades: [], summary: { realized_pnl: 0 } },
      "/api/ticker/info": { uw_info: { name: "Micron Technology" }, stock_state: {}, profile: {}, stats: {} },
      "/api/options/expirations": { symbol: TICKER, expirations: EXPIRIES },
      "/api/options/chain": { symbol: TICKER, expiry: url.searchParams.get("expiry"), strikes: STRIKES, exchange: "SMART", multiplier: "100" },
      "/api/risk-free-rate": { rate: 0.04 },
    };
    const body = fixtures[url.pathname];
    return route.fulfill({ status: body === undefined ? 503 : 200, contentType: "application/json", body: JSON.stringify(body ?? { error: "Unavailable in isolated chain fixture" }) });
  });
  await page.routeWebSocket(/(?:localhost|127\.0\.0\.1):(?:18765|8765)|\/ws(?:\?|$)/, (socket) => {
    sockets.add(socket);
    socket.onClose(() => sockets.delete(socket));
    socket.onMessage((raw) => {
      const message = JSON.parse(raw.toString());
      if (message.action !== "subscribe") return;
      const updates: Record<string, unknown> = { [TICKER]: quote(TICKER, last) };
      for (const contract of message.contracts ?? []) {
        const key = `${contract.symbol}_${String(contract.expiry).replaceAll("-", "")}_${contract.strike}_${contract.right}`;
        updates[key] = quote(key, 3.5);
      }
      socket.send(JSON.stringify({ type: "status", ib_connected: true, subscriptions: [TICKER] }));
      socket.send(JSON.stringify({ type: "batch", updates }));
    });
  });
  return (price: number) => {
    last = price;
    expect(sockets.size).toBeGreaterThan(0);
    for (const socket of sockets) socket.send(JSON.stringify({ type: "batch", updates: { [TICKER]: quote(TICKER, price) } }));
  };
}

async function paneState(pane: Locator) {
  return pane.evaluate((element) => {
    const viewport = element.getBoundingClientRect();
    const rows = Array.from(element.querySelectorAll(".chain-row, .mobile-chain__row"));
    const visible = rows.filter((row) => {
      const bounds = row.getBoundingClientRect();
      return bounds.top >= viewport.top && bounds.bottom <= viewport.bottom;
    });
    return {
      scrollTop: element.scrollTop,
      partition: rows.map((row) => row.querySelector(".chain-strike, .mobile-chain__strike")?.textContent ?? row.getAttribute("data-testid")),
      visible: visible.map((row) => ({
        strike: row.querySelector(".chain-strike, .mobile-chain__strike")?.textContent ?? row.getAttribute("data-testid"),
        offset: Math.round(row.getBoundingClientRect().top - viewport.top),
      })),
    };
  });
}

for (const mobile of [false, true]) {
  for (const theme of ["light", "dark"]) {
    test(`${mobile ? "mobile" : "desktop"} ${theme}: live crossing preserves both browsing panes`, async ({ page }, testInfo) => {
      await page.setViewportSize(mobile ? { width: 393, height: 852 } : { width: 1440, height: 900 });
      await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
      const moveSpot = await installFixtures(page);
      await page.goto(`/${TICKER}?deck=c`);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      const upper = page.getByTestId("chain-upper-pane");
      const lower = page.getByTestId("chain-lower-pane");
      const spot = page.getByTestId("chain-spot-bar");
      await expect(spot).toContainText("120.00");
      const range = mobile ? page.getByTestId("mobile-chain-strikes-select") : page.locator(".chain-expiry-select").nth(1);
      await range.selectOption("25");
      await expect.poll(() => upper.evaluate((element) => element.scrollHeight - element.clientHeight)).toBeGreaterThan(100);
      await expect.poll(() => lower.evaluate((element) => element.scrollHeight - element.clientHeight)).toBeGreaterThan(100);
      const spotBefore = await spot.boundingBox();
      const header = page.locator(mobile ? ".mobile-chain__ladder-head" : ".chain-grid thead").first();
      const headerBefore = await header.boundingBox();
      const lowerBefore = await paneState(lower);
      await upper.hover();
      await page.mouse.wheel(0, -120);
      await expect.poll(async () => (await paneState(upper)).scrollTop).toBeLessThan(await upper.evaluate((element) => element.scrollHeight - element.clientHeight));
      expect((await paneState(lower)).scrollTop).toBe(lowerBefore.scrollTop);
      await lower.hover();
      await page.mouse.wheel(0, 120);
      await expect.poll(async () => (await paneState(lower)).scrollTop).toBeGreaterThan(0);
      await page.waitForTimeout(250);
      const before = { upper: await paneState(upper), lower: await paneState(lower), page: await page.evaluate(() => window.scrollY) };
      expect(before.upper.visible.length).toBeGreaterThan(0);
      expect(before.lower.visible.length).toBeGreaterThan(0);
      const scrolledPath = testInfo.outputPath(`chain-anchor-${mobile ? "mobile" : "desktop"}-${theme}-scrolled.png`);
      await page.screenshot({ path: scrolledPath });
      await testInfo.attach("chain-scrolled", { path: scrolledPath, contentType: "image/png" });
      moveSpot(127);
      await expect(spot).toContainText("127.00");
      expect(await paneState(upper)).toEqual(before.upper);
      expect(await paneState(lower)).toEqual(before.lower);
      expect(await page.evaluate(() => window.scrollY)).toBe(before.page);
      expect(await spot.boundingBox()).toEqual(spotBefore);
      expect(await header.boundingBox()).toEqual(headerBefore);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      const crossingPath = testInfo.outputPath(`chain-anchor-${mobile ? "mobile" : "desktop"}-${theme}-live-crossing.png`);
      await page.screenshot({ path: crossingPath });
      await testInfo.attach("chain-live-crossing", { path: crossingPath, contentType: "image/png" });
      await spot.getByRole("button", { name: "Recenter options chain", exact: true }).click();
      await expect.poll(async () => (await paneState(lower)).scrollTop).toBe(0);
      await expect.poll(async () => (await paneState(upper)).partition).not.toEqual(before.upper.partition);
      await expect.poll(() => upper.evaluate((element) => Math.abs(element.scrollTop - (element.scrollHeight - element.clientHeight)))).toBeLessThan(2);
      if (mobile) {
        await page.getByTestId("mobile-chain-side-calls").click();
        await expect(page.getByTestId("mobile-chain-put-127")).toHaveCount(0);
        await page.getByTestId("mobile-chain-call-127").click();
        await page.getByTestId("mobile-chain-detail-buy").click();
        await expect(page.getByTestId("mobile-chain-pending-strip")).toContainText("1 LEG");
        await page.getByTestId(`mobile-chain-expiry-${EXPIRIES[1]}`).click();
        await expect(page.getByTestId(`mobile-chain-expiry-${EXPIRIES[1]}`)).toHaveAttribute("aria-pressed", "true");
        await expect(page.getByTestId("mobile-chain-pending-strip")).toContainText("1 LEG");
      } else {
        const row = page.locator(".chain-row").filter({ has: page.locator(".chain-strike", { hasText: "$127.00" }) });
        await row.locator(".chain-mid.chain-clickable").first().click();
        await expect(page.getByTestId("order-builder-leg")).toHaveCount(1);
        await page.locator(".chain-expiry-select").first().selectOption(EXPIRIES[1]);
        await expect(page.getByTestId("order-builder-leg")).toHaveCount(1);
      }
    });
  }
}
