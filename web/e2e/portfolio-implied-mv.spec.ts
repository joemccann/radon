import { expect, test, type Locator, type Page } from "@playwright/test";

const NOW = "2026-10-07T16:00:00Z";
const EXPIRY = "2026-11-20";

function leg(direction: "LONG" | "SHORT", strike: number) {
  return { direction, contracts: 2, type: "Call", strike, entry_cost: 1_000, avg_cost: 500, market_price: 5, market_value: 1_000 };
}

const positions = [
  { id: 1, ticker: "AMZN", structure: "Long Call $100", structure_type: "Long Call", risk_profile: "defined", direction: "LONG", legs: [leg("LONG", 100)] },
  { id: 2, ticker: "TSLA", structure: "Short Call $100", structure_type: "Short Call", risk_profile: "undefined", direction: "SHORT", legs: [leg("SHORT", 100)] },
  { id: 3, ticker: "SPY", structure: "Bull Call Spread $100/$110", structure_type: "Bull Call Spread", risk_profile: "defined", direction: "DEBIT", legs: [leg("LONG", 100), leg("SHORT", 110)] },
  { id: 4, ticker: "WULF", structure: "Long Call $100", structure_type: "Long Call", risk_profile: "defined", direction: "LONG", legs: [leg("LONG", 100)] },
].map((position) => ({ ...position, expiry: EXPIRY, contracts: 2, entry_cost: 1_000, market_value: 1_000, max_risk: 1_000, entry_date: "2026-10-01", kelly_optimal: null, target: null, stop: null }));

const prices = Object.fromEntries(positions.flatMap((position) => {
  if (position.ticker === "WULF") return [];
  const quote = { last: 105, close: 104, bid: 104.9, ask: 105.1, lastIsCalculated: false, timestamp: NOW };
  return [
    [position.ticker, { ...quote, symbol: position.ticker }],
    ...position.legs.map((heldLeg) => {
      const symbol = `${position.ticker}_20261120_${heldLeg.strike}_C`;
      return [symbol, { ...quote, symbol, last: 5, close: 4, impliedVol: 0.3, undPrice: 105 }];
    }),
  ];
}));

async function setup(page: Page, theme: "dark" | "light") {
  await page.clock.setFixedTime(new Date(NOW));
  await page.addInitScript((selectedTheme) => {
    localStorage.setItem("theme", selectedTheme);
    for (const table of ["positions-defined", "positions-undefined"]) {
      localStorage.setItem(`radon:columns:${table}`, JSON.stringify({ implied_market_value: true }));
    }
  }, theme);
  // No unlisted API request or relay socket reaches live services.
  await page.route("**/api/**", (route) => route.fulfill({ json: {} }));
  await page.route("https://**", (route) => route.abort());
  await page.routeWebSocket(/.*/, (socket) => {
    socket.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.action === "subscribe") socket.send(JSON.stringify({ type: "batch", updates: prices }));
    });
  });
  await page.route("**/api/portfolio", (route) => route.fulfill({ json: {
    bankroll: 100_000, peak_value: 100_000, last_sync: NOW, total_deployed_pct: 4,
    total_deployed_dollars: 4_000, remaining_capacity_pct: 96, position_count: 4,
    defined_risk_count: 3, undefined_risk_count: 1, avg_kelly_optimal: null,
    exposure: {}, violations: [], positions,
    account_summary: { net_liquidation: 100_000, daily_pnl: 0, unrealized_pnl: 0, realized_pnl: 0, settled_cash: 96_000, maintenance_margin: 0, excess_liquidity: 96_000, buying_power: 100_000, dividends: 0 },
  } }));
  await page.route("**/api/risk-free-rate", (route) => route.fulfill({ json: { rate: 0.04, source: "FRED:DFF", stale: false } }));
  await page.route("**/api/ib/ws-ticket", (route) => route.fulfill({ json: { ticket: "mock-ticket" } }));
  await page.route("**/api/orders", (route) => route.fulfill({ json: { last_sync: NOW, open_orders: [], executed_orders: [] } }));
  await page.route("**/api/blotter", (route) => route.fulfill({ json: { as_of: NOW, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] } }));
  await page.route("**/api/ib-status", (route) => route.fulfill({ json: { connected: true } }));
  await page.route("**/api/profile", (route) => route.fulfill({ json: { username: "Operator", avatar_url: null, ui_preferences: null } }));
  await page.route("**/api/flex-token", (route) => route.fulfill({ json: { ok: true, days_until_expiry: 14 } }));
}

async function assertNeutral(row: Locator, initialIndex: number, expected: RegExp | string) {
  const implied = row.locator('td[title^="Implied market value:"]');
  const initial = row.locator("td").nth(initialIndex);
  await expect(implied).toHaveText(expected);
  await expect(implied).toHaveAttribute("class", await initial.getAttribute("class") ?? "");
  await expect(implied).not.toHaveClass(/positive|negative/);
  await expect.poll(async () => implied.evaluate((cell) => getComputedStyle(cell).color))
    .toBe(await initial.evaluate((cell) => getComputedStyle(cell).color));
}

for (const theme of ["dark", "light"] as const) {
  test(`Implied MV uses neutral Initial Value styling in ${theme} theme`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1800, height: 1100 });
    await setup(page, theme);
    await page.goto("/portfolio", { waitUntil: "domcontentloaded" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);

    const table = page.getByTestId("defined-risk-section").getByTestId("position-table");
    await expect(table.getByRole("columnheader", { name: "Initial Value", exact: true })).toBeVisible();
    const headers = await table.getByRole("columnheader").allTextContents();
    const initialIndex = headers.findIndex((header) => header.trim() === "Initial Value");
    expect(initialIndex).toBeGreaterThanOrEqual(0);
    const positiveValue = /^\$[\d,]+$/;
    const negativeValue = /^-\$[\d,]+$/;
    await assertNeutral(table.getByRole("row").filter({ hasText: "AMZN" }), initialIndex, positiveValue);
    await assertNeutral(page.getByTestId("position-table").getByRole("row").filter({ hasText: "TSLA" }), initialIndex, negativeValue);
    await assertNeutral(table.getByRole("row").filter({ hasText: "WULF" }), initialIndex, "—");

    const spread = table.getByRole("row").filter({ hasText: "SPY" });
    await assertNeutral(spread, initialIndex, positiveValue);
    await spread.getByRole("button", { name: "Expand legs for SPY" }).click();
    const legRows = table.getByRole("row").filter({ has: page.locator("td.cell-indent") });
    await assertNeutral(legRows.filter({ hasText: "LONG Call $100" }), initialIndex, positiveValue);
    await assertNeutral(legRows.filter({ hasText: "SHORT Call $110" }), initialIndex, negativeValue);

    const screenshot = testInfo.outputPath(`portfolio-implied-mv-${theme}.png`);
    await page.screenshot({ path: screenshot, fullPage: true });
    await testInfo.attach(`portfolio-implied-mv-${theme}`, { path: screenshot, contentType: "image/png" });
  });
}
