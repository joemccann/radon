import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";

const NOW = "2026-10-14T16:00:00Z";
const EXPIRY = "2026-11-20";
const basePosition = {
  id: 71, account_id: "USTRATEGY", position_instance_id: "test-aaoi", ticker: "AAOI",
  structure: "Combo (3 legs)", structure_type: "Combo (3 legs)", risk_profile: "undefined",
  expiry: EXPIRY, contracts: 60, direction: "COMBO", entry_cost: -2_386,
  market_value: -18_200, max_risk: null, basis_source: "ib", entry_date: "2026-10-01",
  ib_daily_pnl: -100, kelly_optimal: null, target: null, stop: null,
  legs: [
    { con_id: 7101, expiry: EXPIRY, direction: "LONG", type: "Call", strike: 105, contracts: 60, entry_cost: 59_340, avg_cost: 989, market_price: 7.2, market_value: 43_200, basis_source: "ib" },
    { con_id: 7102, expiry: EXPIRY, direction: "SHORT", type: "Call", strike: 140, contracts: 60, entry_cost: 17_340, avg_cost: 289, market_price: 1.9, market_value: 11_400, basis_source: "ib" },
    { con_id: 7103, expiry: EXPIRY, direction: "SHORT", type: "Put", strike: 90, contracts: 50, entry_cost: 44_386, avg_cost: 887.72, market_price: 10, market_value: 50_000, basis_source: "ib" },
  ],
};

async function setup(page: Page) {
  let position = structuredClone(basePosition);
  const writes: string[] = [];
  await page.clock.setFixedTime(new Date(NOW));
  await page.addInitScript(() => {
    localStorage.setItem("theme", "dark");
    for (const id of ["positions-defined", "positions-undefined"]) {
      localStorage.setItem(`radon:columns:${id}`, JSON.stringify({ market_value: true, initial_value: true, pnl: true }));
    }
  });
  // One interceptor owns every API transport, so no route reaches a live broker.
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    if (!["GET", "HEAD"].includes(request.method())) {
      if (["/api/profile", "/api/preferences"].includes(new URL(request.url()).pathname)) {
        await route.fulfill({ json: { ok: true } });
        return;
      }
      writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
      await route.fulfill({ status: 403, json: { error: "Broker writes forbidden in grouping regression" } });
      return;
    }
    const path = new URL(request.url()).pathname;
    const payload = path === "/api/portfolio" ? {
      bankroll: 400_000, peak_value: 400_000, last_sync: NOW,
      total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
      position_count: 1, defined_risk_count: 0, undefined_risk_count: 1, avg_kelly_optimal: null,
      exposure: {}, violations: [], positions: [position],
      account_summary: { net_liquidation: 400_000, daily_pnl: -100, unrealized_pnl: -15_814, realized_pnl: 0, settled_cash: 100_000, maintenance_margin: 50_000, excess_liquidity: 300_000, buying_power: 400_000, dividends: 0 },
    } : path === "/api/orders" ? { last_sync: NOW, open_orders: [], executed_orders: [] }
      : path === "/api/blotter" ? { as_of: NOW, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }
      : path === "/api/profile" ? { username: "Operator", avatar_url: null, ui_preferences: null }
      : path === "/api/ib-status" ? { connected: true }
      : path === "/api/ib/ws-ticket" ? { ticket: "synthetic-group-ticket" }
      : path === "/api/flex-token" ? { ok: true, days_until_expiry: 14 }
      : path === "/api/risk-free-rate" ? { rate: 0.04, source: "mock", stale: false } : {};
    await route.fulfill({ json: payload });
  });
  await page.route("https://**", (route) => route.abort());
  await page.routeWebSocket(/.*/, (socket) => {
    socket.onMessage((raw) => {
      if (JSON.parse(String(raw)).action !== "subscribe") return;
      const updates = Object.fromEntries(position.legs.map((leg) => {
        const symbol = `AAOI_20261120_${leg.strike}_${leg.type === "Call" ? "C" : "P"}`;
        return [symbol, { symbol, last: leg.market_price, close: leg.market_price, bid: leg.market_price, ask: leg.market_price, timestamp: NOW, lastIsCalculated: false }];
      }));
      socket.send(JSON.stringify({ type: "batch", updates: { ...updates, AAOI: { symbol: "AAOI", last: 110, close: 109, bid: 109.9, ask: 110.1, timestamp: NOW, lastIsCalculated: false } } }));
    });
  });
  return { writes, reorder: () => { position = { ...position, legs: [...position.legs].reverse() }; }, changeQuantity: () => { position = { ...position, legs: position.legs.map((leg) => leg.con_id === 7103 ? { ...leg, contracts: 49 } : leg) }; } };
}

async function capture(page: Page, info: TestInfo, name: string) {
  const path = info.outputPath(`position-strategy-groups-${name}.png`);
  await page.screenshot({ path, fullPage: true });
  await info.attach(name, { path, contentType: "image/png" });
}

function tickerRows(page: Page, section: "defined" | "undefined") {
  const container = section === "defined" ? page.getByTestId("defined-risk-section")
    : page.locator(".section").filter({ has: page.getByRole("heading", { name: /^Undefined Risk Positions/ }) });
  return container.getByTestId("position-table").getByRole("row")
    .filter({ has: page.getByRole("button", { name: "View details for AAOI", exact: true }) });
}

async function valueInColumn(row: Locator, title: string, expected: string) {
  const table = row.locator("xpath=ancestor::table");
  const headers = await table.getByRole("columnheader").allTextContents();
  const index = headers.findIndex((header) => header.trim() === title);
  expect(index).toBeGreaterThanOrEqual(0);
  await expect(row.getByRole("cell").nth(index)).toHaveText(expected);
}

async function assertSplit(page: Page) {
  const spread = tickerRows(page, "defined");
  const put = tickerRows(page, "undefined");
  await expect(spread).toHaveCount(1);
  await expect(put).toHaveCount(1);
  await expect(spread).toContainText("Bull Call Spread");
  await expect(spread.getByTestId("position-cell-pnl")).toHaveText("-$10,200");
  await expect(put.getByTestId("position-cell-pnl")).toHaveText("-$5,614");
  await valueInColumn(spread, "Qty", "60");
  await valueInColumn(put, "Qty", "50");
  await valueInColumn(spread, "Initial Value", "$42,000");
  await valueInColumn(put, "Initial Value", "-$44,386");
  await valueInColumn(spread, "Market Value", "$31,800");
  await valueInColumn(put, "Market Value", "-$50,000");
}

async function openGrouping(page: Page) {
  await page.getByRole("button", { name: "Group strategies for AAOI", exact: true }).first().click();
  await expect(page.getByRole("dialog", { name: "Group AAOI strategies", exact: true })).toBeVisible();
  const dialog = page.getByRole("dialog");
  return dialog;
}

async function suggestedPreview(page: Page) {
  const dialog = await openGrouping(page);
  await dialog.getByRole("button", { name: /^Suggested split/ }).click();
  await expect(dialog.getByRole("button", { name: "Apply grouping", exact: true })).toBeVisible();
  await expect(dialog).toContainText("Bull Call Spread");
  await expect(dialog).toContainText("Short Put");
  await expect(dialog).toContainText("-$10,200");
  await expect(dialog).toContainText("-$5,614");
  return dialog;
}

for (const device of ["desktop", "mobile"] as const) {
  test(`strategy grouping suggested preview cancel apply Undo on ${device}`, async ({ page }, info) => {
    await page.setViewportSize(device === "desktop" ? { width: 1800, height: 1100 } : { width: 393, height: 852 });
    const transport = await setup(page);
    await page.goto("/portfolio", { waitUntil: "domcontentloaded" });
    const initial = await suggestedPreview(page);
    await capture(page, info, `${device}-preview`);
    await initial.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: /^Defined Risk Positions/ })).toHaveCount(0);

    const preview = await suggestedPreview(page);
    if (device === "mobile") {
      for (const name of ["Apply grouping", "Cancel"]) {
        const box = await preview.getByRole("button", { name, exact: true }).boundingBox();
        expect(box).not.toBeNull();
        expect(box!.height).toBeGreaterThanOrEqual(44);
      }
    }
    await preview.getByRole("button", { name: "Apply grouping", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    if (device === "desktop") await assertSplit(page);
    else {
      const cards = page.getByTestId("mobile-position-list").locator("[data-testid^='mobile-position-AAOI']").filter({ has: page.getByRole("button", { name: "Group strategies for AAOI", exact: true }) });
      await expect(cards).toHaveCount(2);
      await expect(cards.filter({ hasText: "Bull Call Spread" })).toContainText("-$10,200");
      await expect(cards.filter({ hasText: "Short Put" })).toContainText("-$5,614");
    }
    await capture(page, info, `${device}-applied`);
    await page.getByRole("button", { name: "Undo grouping", exact: true }).click();
    await expect(page.getByRole("heading", { name: /^Defined Risk Positions/ })).toHaveCount(0);
    expect(transport.writes).toEqual([]);
  });
}

test("strategy grouping persists through reload and broker leg reorder; quantity change invalidates it", async ({ page }, info) => {
  await page.setViewportSize({ width: 1800, height: 1100 });
  const transport = await setup(page);
  await page.goto("/portfolio", { waitUntil: "domcontentloaded" });
  const preview = await suggestedPreview(page);
  await preview.getByRole("button", { name: "Apply grouping", exact: true }).click();
  await assertSplit(page);
  await page.reload({ waitUntil: "domcontentloaded" });
  await assertSplit(page);
  transport.reorder();
  await page.reload({ waitUntil: "domcontentloaded" });
  await assertSplit(page);
  await capture(page, info, "reordered-persisted");
  transport.changeQuantity();
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: /^Defined Risk Positions/ })).toHaveCount(0);
  const original = tickerRows(page, "undefined");
  await expect(original).toHaveCount(1);
  await expect(original).toContainText("Combo (3 legs)");
  expect(transport.writes).toEqual([]);
});

for (const device of ["desktop", "mobile"] as const) {
test(`manual selected legs form a spread and keyboard cancellation retains holding on ${device}`, async ({ page }, info) => {
  await page.setViewportSize(device === "desktop" ? { width: 1800, height: 1100 } : { width: 393, height: 852 });
  const transport = await setup(page);
  await page.goto("/portfolio", { waitUntil: "domcontentloaded" });
  let dialog = await openGrouping(page);
  await dialog.getByRole("button", { name: /^Select legs/ }).click();
  await dialog.getByRole("checkbox", { name: /Select LONG 60 Call \$105/ }).check();
  await dialog.getByRole("checkbox", { name: /Select SHORT 60 Call \$140/ }).check();
  await expect(dialog.getByRole("checkbox", { name: /Select SHORT 50 Put \$90/ })).not.toBeChecked();
  await dialog.getByRole("button", { name: "Group as spread", exact: true }).click();
  await expect(dialog).toContainText("Bull Call Spread");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  if (device === "desktop") await expect(tickerRows(page, "undefined")).toContainText("Combo (3 legs)");
  else await expect(page.getByTestId("mobile-position-AAOI")).toContainText("Combo (3 legs)");

  dialog = await openGrouping(page);
  await dialog.getByRole("button", { name: /^Select legs/ }).click();
  const firstLeg = dialog.getByRole("checkbox", { name: /Select LONG 60 Call \$105/ });
  await firstLeg.focus();
  await page.keyboard.press("Space");
  await expect(firstLeg).toBeChecked();
  await dialog.getByRole("checkbox", { name: /Select SHORT 60 Call \$140/ }).check();
  await dialog.getByRole("button", { name: "Group as spread", exact: true }).click();
  await capture(page, info, `${device}-selected-legs-preview`);
  await dialog.getByRole("button", { name: "Apply grouping", exact: true }).click();
  if (device === "desktop") await assertSplit(page);
  else await expect(page.getByRole("heading", { name: /^Defined Risk Positions/ })).toBeVisible();
  await page.getByRole("button", { name: "Undo grouping", exact: true }).click();
  await expect(page.getByRole("heading", { name: /^Defined Risk Positions/ })).toHaveCount(0);
  expect(transport.writes).toEqual([]);
});
}

for (const device of ["desktop", "mobile"] as const) {
  test(`named strategy editor assigns held legs and preserves financial totals on ${device}`, async ({ page }, info) => {
    await page.setViewportSize(device === "desktop" ? { width: 1800, height: 1100 } : { width: 393, height: 852 });
    const transport = await setup(page);
    await page.goto("/portfolio", { waitUntil: "domcontentloaded" });
    const dialog = await openGrouping(page);
    await dialog.getByRole("button", { name: /^Edit strategies/ }).click();
    await dialog.getByRole("textbox", { name: "Strategy name 1", exact: true }).fill("Bull Call Spread");
    await dialog.getByRole("button", { name: "Add strategy", exact: true }).click();
    await dialog.getByRole("textbox", { name: "Strategy name 2", exact: true }).fill("Short Put");
    await dialog.getByRole("combobox", { name: "Strategy group for SHORT 50 Put $90", exact: true }).selectOption({ label: "Short Put" });
    await expect(dialog.getByRole("combobox", { name: "Strategy group for LONG 60 Call $105", exact: true })).toHaveValue("whole");
    await expect(dialog.getByRole("combobox", { name: "Strategy group for SHORT 60 Call $140", exact: true })).toHaveValue("whole");
    if (device === "mobile") {
      for (const control of await dialog.getByRole("combobox").all()) {
        const box = await control.boundingBox();
        expect(box?.height).toBeGreaterThanOrEqual(44);
      }
    }
    await capture(page, info, `${device}-editor`);
    await dialog.getByRole("button", { name: "Preview grouping", exact: true }).click();
    await expect(dialog).toContainText("$42,000");
    await expect(dialog).toContainText("-$44,386");
    await expect(dialog).toContainText("-$10,200");
    await expect(dialog).toContainText("-$5,614");
    await dialog.getByRole("button", { name: "Apply grouping", exact: true }).click();
    if (device === "desktop") await assertSplit(page);
    else await expect(page.getByRole("heading", { name: /^Defined Risk Positions/ })).toBeVisible();
    const reset = await openGrouping(page);
    await reset.getByRole("button", { name: "Reset grouping", exact: true }).click();
    await expect(page.getByRole("heading", { name: /^Defined Risk Positions/ })).toHaveCount(0);
    expect(transport.writes).toEqual([]);
  });
}
