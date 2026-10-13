import { expect, test } from "@playwright/test";
import { watchPortfolioRequests } from "./portfolio-request-guard";

const NOW = "2026-10-13T19:41:16.000Z";
const EXPIRY = "2026-10-23";
const portfolio = {
  bankroll: 614_523, peak_value: 614_523, last_sync: NOW,
  total_deployed_pct: 2.5, total_deployed_dollars: 15_275, remaining_capacity_pct: 97.5,
  position_count: 1, defined_risk_count: 0, undefined_risk_count: 1,
  exposure: {}, violations: [],
  risk_budget: { clusters: [], breaches: [], aggregate_exposure: 0, insufficient_data: [], corr_threshold: 0.7, book_budget: 0.025 },
  account_summary: { net_liquidation: 614_523, settled_cash: 564_523, available_funds: 564_523,
    excess_liquidity: 564_523, buying_power: 1_129_046, maintenance_margin: 50_000,
    daily_pnl: null, unrealized_pnl: 0, realized_pnl: 0, dividends: 0 },
  positions: [{
    id: 1, ticker: "SNDK", structure: "Complex Combo", structure_type: "Complex Combo",
    risk_profile: "Undefined", direction: "COMBO", contracts: 10, expiry: EXPIRY,
    entry_cost: 15_275, market_value: 29_025, max_risk: null, entry_date: "2026-10-12",
    legs: [
      { direction: "LONG", contracts: 10, type: "Call", strike: 1450, expiry: EXPIRY,
        entry_cost: 52_766, avg_cost: 5276.6, market_price: 110.5, market_value: 110_500 },
      { direction: "SHORT", contracts: 10, type: "Call", strike: 1600, expiry: EXPIRY,
        entry_cost: -13_016, avg_cost: 1301.6, market_price: 63.1, market_value: -63_100 },
      { direction: "SHORT", contracts: 5, type: "Put", strike: 1400, expiry: EXPIRY,
        entry_cost: -24_475, avg_cost: 4895, market_price: 36.75, market_value: -18_375 },
    ],
  }],
};
const combo = {
  orderId: 77, permId: 653611587, symbol: "SNDK Combo",
  contract: { conId: 28812380, symbol: "SNDK", secType: "BAG", strike: 0, right: "?", expiry: null,
    comboLegs: [
      { conId: 101, ratio: 1, action: "SELL", symbol: "SNDK", strike: 1600, right: "C", expiry: EXPIRY },
      { conId: 102, ratio: 1, action: "BUY", symbol: "SNDK", strike: 1450, right: "C", expiry: EXPIRY },
    ] },
  action: "SELL", orderType: "LMT", totalQuantity: 10, limitPrice: 56, auxPrice: null,
  status: "Submitted", filled: 0, remaining: 10, avgFillPrice: null, tif: "DAY",
};
const putClose = {
  orderId: 78, permId: 653611588, symbol: "SNDK",
  contract: { conId: 103, symbol: "SNDK", secType: "OPT", strike: 1400, right: "P", expiry: EXPIRY },
  action: "BUY", orderType: "LMT", totalQuantity: 5, limitPrice: 34.95, auxPrice: null,
  status: "Submitted", filled: 0, remaining: 5, avgFillPrice: null, tif: "DAY",
};
const orders = { last_sync: NOW, open_orders: [putClose, combo], executed_orders: [], open_count: 2, executed_count: 0 };

test("modifying a held call subset uses selected basis and preserves the broker BAG definition", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1250 });
  await page.clock.install({ time: new Date(NOW) });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  await page.routeWebSocket(/:8765/, socket => {
    socket.send(JSON.stringify({ type: "status", ib_connected: true, market_data_degraded: false, subscriptions: [] }));
    socket.onMessage(() => {
      const quote = (symbol: string, bid: number, ask: number) => ({
        symbol, bid, ask, last: (bid + ask) / 2, close: (bid + ask) / 2,
        timestamp: new Date(NOW).toISOString(), bidSize: 10, askSize: 10, volume: 100,
        impliedVol: 0.6, undPrice: 1520,
      });
      socket.send(JSON.stringify({ type: "batch", updates: {
        SNDK: quote("SNDK", 1519, 1521),
        SNDK_20261023_1450_C: quote("SNDK_20261023_1450_C", 110, 111),
        SNDK_20261023_1600_C: quote("SNDK_20261023_1600_C", 62.6, 63.6),
        SNDK_20261023_1400_P: quote("SNDK_20261023_1400_P", 36.5, 37),
      } }));
    });
  });
  await page.route(/^https?:\/\/(?!localhost:|127\.0\.0\.1:)/, route => route.abort());
  const guard = watchPortfolioRequests(page);
  const modifications: { method: string; body: unknown }[] = [];
  await page.route("**/api/**", route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/portfolio") guard.record(request.url());
    if (path === "/api/orders/modify") {
      modifications.push({ method: request.method(), body: request.postDataJSON() });
      return route.fulfill({ json: { status: "ok", message: "Replacement placed", orders } });
    }
    const body = path === "/api/portfolio" ? portfolio
      : path === "/api/orders" ? orders
      : path === "/api/ws-ticket" ? { ticket: "fixture" }
      : path === "/api/ib-status" ? { connected: true }
      : path === "/api/regime" ? { score: 15, cri: { score: 15 } }
      : path === "/api/blotter" ? { as_of: NOW, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }
      : {};
    return route.fulfill({ json: body });
  });
  await page.goto("/orders");
  const row = page.locator("tbody tr").filter({ hasText: "SNDK Combo" }).first();
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "MODIFY", exact: true }).click();
  const dialog = page.locator(".modify-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("#modify-leg-0-action")).toHaveValue("SELL");
  await expect(dialog.locator("#modify-leg-0-strike")).toHaveValue("1600");
  await expect(dialog.locator("#modify-leg-1-action")).toHaveValue("BUY");
  await expect(dialog.locator("#modify-leg-1-strike")).toHaveValue("1450");
  await dialog.locator("#modify-price-input").fill("50");
  const summary = dialog.locator(".order-confirm-summary");
  await expect(summary).toContainText("Close Credit:");
  await expect(summary).toContainText("$50,000");
  await expect(summary.getByTestId("order-confirm-estimated-pnl")).toContainText("$10,250");
  await expect(summary.getByTestId("order-confirm-estimated-pnl")).toContainText("(+25.8%)");
  await expect(summary).not.toContainText("Max Gain:");
  await expect(summary).not.toContainText("Max Loss:");
  await expect(summary).not.toContainText("$34,725"); // Aggregate basis includes the retained put.
  await dialog.screenshot({ path: testInfo.outputPath("modify-sndk-subset-close.png") });
  await summary.screenshot({ path: testInfo.outputPath("modify-sndk-subset-close-summary.png") });
  const submit = dialog.getByRole("button", { name: "Modify Order", exact: true });
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect.poll(() => modifications.length).toBe(1);
  expect(modifications[0]).toEqual({ method: "POST", body: {
    orderId: 77, permId: 653611587,
    replaceOrder: { type: "combo", symbol: "SNDK", action: "SELL", quantity: 10, limitPrice: 50, tif: "DAY",
      legs: [
        { action: "SELL", right: "C", strike: 1600, expiry: "20261023", ratio: 1 },
        { action: "BUY", right: "C", strike: 1450, expiry: "20261023", ratio: 1 },
      ] },
  } });
  await guard.assertAllRouted();
  expect(guard.seen.some(url => url.includes("include=entry-dates"))).toBe(true);
});
