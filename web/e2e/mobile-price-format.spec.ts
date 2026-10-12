import { expect, test, type Locator, type Page } from "@playwright/test";

// Three legs and the signed 3.52 / 3.07 / 3.98 quotes from the mobile report.
// Dates stay relative so an expired fixture cannot stop cockpit resolution.
const now = new Date().toISOString();
const dateOffset = (days: number) => new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);
const expiry = dateOffset(30);
const expiryKey = expiry.replaceAll("-", "");
const legs = [
  { direction: "SHORT", type: "Call", strike: 270, last: 4, bid: 3.8, ask: 4.1 },
  { direction: "SHORT", type: "Put", strike: 230, last: 2.48, bid: 2.32, ask: 2.53 },
  { direction: "LONG", type: "Put", strike: 260, last: 10, bid: 9.7, ask: 10.1 },
];
const portfolio = {
  bankroll: 100_000, peak_value: 100_000, last_sync: now,
  total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
  position_count: 1, defined_risk_count: 0, undefined_risk_count: 1,
  avg_kelly_optimal: null, exposure: {}, violations: [],
  account_summary: { net_liquidation: 100_000, daily_pnl: 0, unrealized_pnl: 0,
    realized_pnl: 0, settled_cash: 100_000, maintenance_margin: 0,
    excess_liquidity: 100_000, buying_power: 200_000, dividends: 0 },
  positions: [{
    id: 12, ticker: "CRWD", structure: "Combo (3 legs)", structure_type: "Combo",
    risk_profile: "undefined", expiry, contracts: 100, direction: "COMBO",
    entry_cost: -3_200, max_risk: null, market_value: 35_200,
    market_price_is_calculated: true, ib_daily_pnl: 0, kelly_optimal: null,
    target: null, stop: null, entry_date: dateOffset(-7),
    legs: legs.map((leg) => ({ ...leg, contracts: 100, entry_cost: leg.last * 10_000,
      avg_cost: leg.last * 100, market_price: leg.last,
      market_value: leg.last * 10_000, market_price_is_calculated: false })),
  }],
};
const stockQuote = { symbol: "CRWD", last: 250, bid: 249.9, ask: 250.1,
  bidSize: 10, askSize: 10, volume: 10, close: 249, timestamp: now,
  lastIsCalculated: false, delta: null, gamma: null, theta: null, vega: null,
  impliedVol: null, undPrice: null };
const quotes = {
  CRWD: stockQuote,
  ...Object.fromEntries(legs.map((leg) => {
    const key = `CRWD_${expiryKey}_${leg.strike}_${leg.type === "Call" ? "C" : "P"}`;
    return [key, { ...stockQuote, symbol: key, last: leg.last, bid: leg.bid,
      ask: leg.ask, bidSize: null, askSize: null, close: leg.last, undPrice: 250 }];
  })),
};

async function installFixtures(page: Page, credit = false) {
  const longPutKey = `CRWD_${expiryKey}_260_P`;
  const activeQuotes = credit
    ? { ...quotes, [longPutKey]: { ...stockQuote, symbol: longPutKey, last: 2, bid: 1.7, ask: 2.1, bidSize: null, askSize: null, undPrice: 250 } }
    : quotes;
  await page.addInitScript((updates) => {
    class MockRelay {
      readyState = 0;
      onopen: ((e?: unknown) => void) | null = null;
      onmessage: ((e: { data: string }) => void) | null = null;
      onclose: ((e?: unknown) => void) | null = null;
      onerror: ((e?: unknown) => void) | null = null;
      constructor() {
        setTimeout(() => {
          this.readyState = 1;
          this.onopen?.({});
          this.onmessage?.({ data: JSON.stringify({ type: "status", ib_connected: true,
            market_data_degraded: false, subscriptions: [] }) });
        }, 0);
      }
      send(raw: string) {
        if (JSON.parse(raw).action === "subscribe") {
          this.onmessage?.({ data: JSON.stringify({ type: "batch", updates }) });
        }
      }
      close() { this.readyState = 3; this.onclose?.({}); }
    }
    const NativeWebSocket = window.WebSocket;
    const RelayWebSocket = function(url: string | URL, protocols?: string | string[]) {
      return String(url).includes("localhost:8765")
        ? new MockRelay() as unknown as WebSocket
        : new NativeWebSocket(url, protocols);
    } as unknown as typeof WebSocket;
    Object.assign(RelayWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    window.WebSocket = RelayWebSocket;
  }, activeQuotes);
  await page.route("**/api/**", (route) => route.fulfill({ status: 503, json: { error: "No fixture for this read" } }));
  const reads: Record<string, unknown> = {
    portfolio,
    orders: { last_sync: now, open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 },
    regime: { score: 15, level: "LOW" },
    "ib-status": { connected: true },
    "ib/ws-ticket": { ticket: "test" },
    blotter: { as_of: now, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] },
  };
  for (const [path, body] of Object.entries(reads)) {
    await page.route(`**/api/${path}`, (route) => route.fulfill({ status: 200, json: body }));
  }
  await page.route("**/api/ticker/**", (route) => route.fulfill({ status: 200, json: {} }));
  await page.goto("/CRWD?posId=12", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("cockpit-host")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("book-kind")).toHaveText("IMPLIED SPREAD");
  await expect(page.locator(".book-head-stat.bid b")).toHaveText(credit ? "-4.93" : "3.07");
  await expect(page.locator(".book-head-stat.ask b")).toHaveText(credit ? "-4.02" : "3.98");
  await page.evaluate(() => document.fonts.ready);
}

// toBeVisible allows text hidden by overflow. Measure the text's own bounds
// against its button/cell, including each line of a wrapped label.
async function expectTextFits(locator: Locator) {
  const escape = await locator.evaluate((el) => {
    const box = el.getBoundingClientRect();
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let maxEscape = 0;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.textContent?.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const rect of range.getClientRects()) {
        maxEscape = Math.max(maxEscape, box.left - rect.left, rect.right - box.right,
          box.top - rect.top, rect.bottom - box.bottom);
      }
    }
    return maxEscape;
  });
  expect(escape, `text escapes its control by ${escape}px`).toBeLessThanOrEqual(1);
}

for (const width of [320, 393, 1440]) {
  test(`three-leg prices and instrument labels fit at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 844 });
    await installFixtures(page);
    const selector = page.getByRole("group", { name: "Spread and option leg book" });
    await expect(selector).toBeVisible();
    const implied = page.getByRole("button", { name: "Implied spread book" });
    await expectTextFits(implied);
    for (const leg of legs) {
      const button = page.getByRole("button", { name: `$${leg.strike} ${leg.type} book`, exact: true });
      await expect(button).toContainText(leg.direction);
      await expect(button).toContainText(`$${leg.strike} ${leg.type.toUpperCase()}`);
      await expectTextFits(button);
      await button.scrollIntoViewIfNeeded();
      const box = await button.boundingBox();
      const group = await selector.boundingBox();
      expect(box).not.toBeNull();
      expect(group).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(group!.x - 1);
      expect(box!.x + box!.width).toBeLessThanOrEqual(group!.x + group!.width + 1);
    }
    await selector.evaluate((el) => { el.scrollLeft = 0; });
    for (const stat of await page.locator(".book-head-stat").all()) await expectTextFits(stat);
    await expect(page.locator(".book-head-stat").first()).toHaveText(/MARK\s+3\.52/);
    await expect(page.locator(".book-head-stat").last()).toHaveText(/SPRD\s+0\.91/);
    if (width < 640) {
      const values = page.locator(".ckp-quote-price");
      await expect(values).toHaveText(["$3.07", "$3.52", "$3.98"]);
      for (const cell of await page.locator(".ckp-quote-cell").all()) await expectTextFits(cell);
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.screenshot({ path: testInfo.outputPath(`mobile-price-format-${width}.png`), fullPage: true });
    const lastLeg = page.getByRole("button", { name: "$260 Put book", exact: true });
    await lastLeg.scrollIntoViewIfNeeded();
    const box = await lastLeg.boundingBox();
    const group = await selector.boundingBox();
    expect(box).not.toBeNull();
    expect(group).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(group!.x - 1);
    expect(box!.x + box!.width).toBeLessThanOrEqual(group!.x + group!.width + 1);
    await lastLeg.click();
    await expect(lastLeg).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(".book-sym")).toContainText("CRWD $260P");
    await expect(page.getByTestId("book-kind")).toHaveText("OPTION");
    await expect(page.locator(".book-head-stat.bid b")).toHaveText("9.70");
    await expect(page.locator(".book-head-stat.ask b")).toHaveText("10.10");
    await page.screenshot({ path: testInfo.outputPath(`mobile-price-format-last-leg-${width}.png`), fullPage: true });
  });
}


test("negative combo premiums keep their signs at 320px", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await installFixtures(page, true);
  await expect(page.locator(".ckp-quote-price")).toHaveText(["$-4.93", "$-4.48", "$-4.02"]);
  for (const cell of await page.locator(".ckp-quote-cell").all()) await expectTextFits(cell);
  for (const stat of await page.locator(".book-head-stat").all()) await expectTextFits(stat);
  await page.screenshot({ path: testInfo.outputPath("mobile-price-format-credit-320.png"), fullPage: true });
});
