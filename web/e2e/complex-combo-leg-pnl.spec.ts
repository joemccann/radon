import { expect, test, type Page } from "@playwright/test";

const now = new Date().toISOString();
const dateOffset = (days: number) => new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);
const expiry = dateOffset(30);
const legs = [
  { direction: "LONG", type: "Call", strike: 1450, contracts: 10, entry_cost: 52766, avg_cost: 9999, market_price: 63.15, market_value: 63150 },
  { direction: "SHORT", type: "Call", strike: 1600, contracts: 10, entry_cost: 13016, avg_cost: 1301.6, market_price: 17.55, market_value: -17550 },
  { direction: "SHORT", type: "Put", strike: 1400, contracts: 5, entry_cost: 24475, avg_cost: 4895, market_price: 39.50, market_value: -19750 },
];
const position = { id: 19, ticker: "SNDK", structure: "Combo (3 legs)", structure_type: "Combo", risk_profile: "undefined", expiry, contracts: 10, direction: "COMBO", entry_cost: 15275, max_risk: null, market_value: 25850, ib_daily_pnl: null, entry_date: dateOffset(-7), legs };
const portfolio = { bankroll: 100000, peak_value: 100000, last_sync: now, total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100, position_count: 1, defined_risk_count: 0, undefined_risk_count: 1, avg_kelly_optimal: null, exposure: {}, violations: [], account_summary: { net_liquidation: 100000, daily_pnl: 9000, unrealized_pnl: 10575, realized_pnl: 0, settled_cash: 100000, maintenance_margin: 0, excess_liquidity: 100000, buying_power: 200000, dividends: 0 }, positions: [position] };
const quote = { last: 1453.5, bid: 1453, ask: 1454, close: 1450, timestamp: now, lastIsCalculated: false, bidSize: 10, askSize: 10, volume: 10, delta: null, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: 1453.5 };

async function installFixtures(page: Page, sameDay = false, missingClose = false, zeroBasis = false) {
  const activeQuotes = { SNDK: { ...quote, symbol: "SNDK" }, ...Object.fromEntries(legs.map((leg, i) => {
    const key = `SNDK_${expiry.replaceAll("-", "")}_${leg.strike}_${leg.type === "Call" ? "C" : "P"}`;
    return [key, { ...quote, symbol: key, last: leg.market_price, bid: null, ask: null, close: missingClose && i === 2 ? null : [60, 20, 40][i] }];
  })) };
  await page.addInitScript((updates) => {
    localStorage.setItem("radon:columns:positions-undefined", JSON.stringify({
      structure: false, qty: true, direction: false, avg_entry: true, last_price: true,
      implied: false, implied_market_value: false, daily_chg: false, today_pnl: true,
      market_value: false, entry_cost: false, initial_value: true, pnl: true, pnl_pct: true, expiry: true,
    }));
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
  await page.route("**/api/**", (route) => route.fulfill({ status: 200, json: {} }));
  const reads: Record<string, unknown> = {
    portfolio: { ...portfolio, positions: [{ ...position, legs: zeroBasis ? legs.map((leg, i) => ({ ...leg, entry_cost: i === 0 ? 0 : leg.entry_cost })) : legs, entry_date: sameDay ? new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date()) : position.entry_date }] },
    orders: { last_sync: now, open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 },
    "ib-status": { connected: true }, "ib/ws-ticket": { ticket: "test" },
    blotter: { as_of: now, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] },
  };
  for (const [path, body] of Object.entries(reads)) await page.route(`**/api/${path}`, (route) => route.fulfill({ status: 200, json: body }));
  await page.goto("/portfolio", { waitUntil: "domcontentloaded" });
  await page.evaluate(() => document.fonts.ready);
}

for (const width of [1440, 393]) {
  test(`complex ratio combo reconciles per-leg total and today P&L at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width > 640 ? 1400 : 1000 });
    await installFixtures(page);
    await expect.poll(() => page.evaluate(() => document.querySelector('[data-testid="position-cell-today-pnl"]')?.textContent ?? document.querySelector('[data-testid="mobile-position-today"]')?.textContent)).toContain("+$5,850");
    if (width > 640) {
      const row = page.locator("tr", { hasText: "SNDK" }).first();
      await expect(row.getByTestId("position-cell-pnl")).toHaveText("+$10,575");
      await expect(row.getByTestId("position-cell-pnl-pct")).toHaveText("+69.2%");
      await expect(row.getByTestId("position-cell-today-pnl")).toHaveText("+$5,850");
      await page.getByRole("button", { name: "Expand legs for SNDK" }).click();
      await expect(page.getByTestId("position-leg-pnl")).toHaveText(["+$10,384", "-$4,534", "+$4,725"]);
      await expect(page.getByTestId("position-leg-today-pnl")).toHaveText(["+$3,150", "+$2,450", "+$250"]);
      for (const dollar of await page.getByTestId("position-leg-pnl").all()) {
        await expect(dollar.locator("xpath=ancestor::td")).not.toContainText("%");
      }
    } else {
      const card = page.getByTestId("mobile-position-SNDK");
      await expect(card.getByTestId("mobile-position-pnl")).toHaveText("+$10,575");
      await expect(card).toContainText("+69.23%");
      await expect(card.getByTestId("mobile-position-today")).toContainText("+$5,850");
      await card.getByRole("button", { name: "SNDK Combo (3 legs)", exact: true }).click();
      await expect(card.getByTestId("position-leg-pnl")).toHaveText(["+$10,384", "-$4,534", "+$4,725"]);
      await expect(card.getByTestId("position-leg-today-pnl")).toHaveText(["+$3,150", "+$2,450", "+$250"]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    }
    await expect(page.getByTestId("position-leg-pnl-pct")).toHaveText(["+19.7%", "-34.8%", "+19.3%"]);
    await expect(page.getByTestId("position-leg-return-cell")).toHaveCount(3);
    for (const cell of await page.getByTestId("position-leg-return-cell").all()) {
      await expect(cell.getByTestId("position-leg-pnl-pct")).toBeVisible();
      await expect(cell.getByTestId("position-leg-pnl")).toHaveCount(0);
    }
    for (const percent of await page.getByTestId("position-leg-pnl-pct").all()) {
      await expect(percent).toHaveAttribute("aria-label", "P&L % of entry value");
      await expect(percent).toHaveAttribute("title", "P&L % of entry value");
    }
    await expect(page.getByText("$52.77", { exact: true }).first()).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`complex-combo-leg-pnl-${width}.png`), fullPage: true });
  });
}

test("same-day ratio legs use their own entry basis", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await installFixtures(page, true);
  await page.getByRole("button", { name: "Expand legs for SNDK" }).click();
  await expect(page.getByTestId("position-leg-today-pnl")).toHaveText(["+$10,384", "-$4,534", "+$4,725"]);
});

test("missing overnight close withholds that leg and the combo Today total", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await installFixtures(page, false, true);
  const row = page.locator("tr", { hasText: "SNDK" }).first();
  await expect(row.getByTestId("position-cell-today-pnl")).toHaveText("—");
  await page.getByRole("button", { name: "Expand legs for SNDK" }).click();
  await expect(page.getByTestId("position-leg-today-pnl")).toHaveText(["+$3,150", "+$2,450", "—"]);
});


for (const width of [1440, 393]) {
  test(`zero leg entry value leaves P&L percentage unavailable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await installFixtures(page, false, false, true);
    if (width > 640) await page.getByRole("button", { name: "Expand legs for SNDK" }).click();
    else await page.getByTestId("mobile-position-SNDK").getByRole("button", { name: "SNDK Combo (3 legs)", exact: true }).click();
    await expect(page.getByTestId("position-leg-pnl").first()).toHaveText("+$63,150");
    await expect(page.getByTestId("position-leg-pnl-pct")).toHaveText(["—", "-34.8%", "+19.3%"]);
  });
}
