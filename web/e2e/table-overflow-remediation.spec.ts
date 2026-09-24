import { expect, test } from "@playwright/test";

const NOW = "2026-09-16T17:00:00.000Z";
const report = {
  market_code: "001", name: "Synthetic long contract name for positioning", category: "Equity index",
  report_date: "2026-09-15", open_interest: 100000, net_commercial: -25000,
  net_noncommercial: 25000, net_noncommercial_pct_oi: 25,
};
const fixtures: Record<string, unknown> = {
  "/api/portfolio": {
    bankroll: 100000, peak_value: 100000, last_sync: NOW, positions: [], position_count: 0,
    total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
    defined_risk_count: 0, undefined_risk_count: 0, avg_kelly_optimal: null, exposure: {}, violations: [],
  },
  "/api/orders": { open_orders: [], open_count: 0, executed_count: 1, last_sync: NOW, executed_orders: [{
    execId: "fixture-fill", symbol: "NVDA", side: "BOT", quantity: 5, avgPrice: 3.5,
    commission: 0.5, realizedPNL: null, time: NOW, exchange: "SMART",
    contract: { conId: 123, symbol: "NVDA", secType: "OPT", strike: 200, right: "C", expiry: "20261218" },
  }] },
  "/api/blotter": { as_of: NOW, closed_trades: [], open_trades: [], summary: {} },
  "/api/cash-flows": { rows: [], count: 0, summary: {}, last_synced_at: NOW },
  "/api/ib-status": { connected: false },
  "/api/regime": { score: 15, cri: { score: 15 } },
  "/api/ticker/info": { uw_info: { name: "NVIDIA Corporation" }, stock_state: {}, profile: {}, stats: {} },
  "/api/ticker/ratings": {
    ticker: "NVDA", recommendation: "buy", analyst_count: 12, ratings: { buy: 12 },
    target_price: { low: 90, high: 150, mean: 120, median: 119, count: 12 },
    recent_changes: [{ date: "2026-09-15", firm: "Synthetic International Institutional Research", action: "upgrade", to_grade: "Buy" }],
  },
  "/api/ticker/seasonality": { source: "unusualwhales", data: Array.from({ length: 12 }, (_, index) => ({
    month: index + 1, avg_change: 0.02, median_change: 0.01, max_change: 0.09,
    min_change: -0.04, positive_closes: 7, positive_months_perc: 0.7, years: 10,
  })) },
  "/api/equibles-cot-positioning": {
    scan_time: NOW, report_date: report.report_date, count: 1, market: [report],
    contracts: [{ ...report, alias: "SPX", weeks: 52, percentile: 75, z_score: 1,
      net_noncommercial_change: 100, crowding: "NEUTRAL", contrarian_bias: "NEUTRAL",
      series: [{ ...report, report_date: "2026-09-08" }, report],
    }],
  },
  "/api/options/expirations": { symbol: "NVDA", expirations: ["20261218"] },
  "/api/options/chain": { symbol: "NVDA", expiry: "20261218", strikes: [], exchange: "SMART", multiplier: "100" },
  "/api/watchlist": { watchlist: [] },
  "/api/service-health": { services: [] },
};

test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: "ignoreErrors" }); });

for (const width of [390, 1024]) {
  for (const [name, url, id] of [
    ["ratings", "/NVDA?deck=r", "ratings-table-scroll"],
    ["seasonality", "/NVDA?deck=s", "seasonality-table-scroll"],
    ["cot", "/regime/cot", "cot-table-scroll"],
    ["executed", "/orders", "executed-table-scroll"],
  ]) {
    test(`${name} contains table overflow at ${width}px`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 852 });
      await page.clock.setFixedTime(new Date(NOW));
      await page.addInitScript(() => localStorage.setItem("theme", "light"));
      await page.routeWebSocket(/.*/, (socket) => socket.close());
      await page.route("**/api/**", (route) => {
        const body = fixtures[new URL(route.request().url()).pathname];
        return route.fulfill({ status: body === undefined ? 503 : 200, json: body ?? { error: "Unmocked fixture endpoint" } });
      });
      await page.goto(url);
      const region = page.getByTestId(name === "executed" && width === 390 ? "mobile-executed-list" : id);
      await expect(region).toBeVisible();
      await region.scrollIntoViewIfNeeded();
      if (!(name === "executed" && width === 390)) {
        await expect(region).toHaveCSS("overflow-x", "auto");
        await expect(region.locator("tbody tr").first()).toBeVisible();
        const geometry = await region.evaluate((el) => {
          el.scrollLeft = el.scrollWidth;
          return { width: el.clientWidth, overflow: el.scrollWidth > el.clientWidth, moved: el.scrollLeft > 0 };
        });
        expect(geometry.width).toBeGreaterThan(0);
        expect(geometry.width).toBeLessThanOrEqual(width);
        if (geometry.overflow) expect(geometry.moved).toBe(true);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      const path = testInfo.outputPath(`table-overflow-${name}-${width}.png`);
      await page.screenshot({ path });
      await testInfo.attach(`${name}-${width}`, { path, contentType: "image/png" });
    });
  }
}
