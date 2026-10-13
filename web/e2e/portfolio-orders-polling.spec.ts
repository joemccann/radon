import { expect, test, type Page } from "@playwright/test";

const OPEN = new Date("2026-10-13T14:00:00Z"); // Tuesday, 10 AM ET
const EXTENDED = new Date("2026-10-13T12:00:00Z"); // Tuesday, 8 AM ET
const CLOSED = new Date("2026-10-13T02:00:00Z"); // Monday, 10 PM ET

async function mockAccount(page: Page, now: Date) {
  await page.clock.install({ time: new Date(now.getTime() - 1_000) });
  await page.clock.pauseAt(now);
  const reads = { portfolio: 0, orders: 0, posts: 0 };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const timestamp = await page.evaluate(() => new Date().toISOString());
    const payloads: Record<string, unknown> = {
      "/api/portfolio": {
        bankroll: 100_000, peak_value: 100_000, last_sync: timestamp,
        position_count: 0, positions: [], exposure: {}, violations: [],
        total_deployed_pct: 0, total_deployed_dollars: 0,
        remaining_capacity_pct: 100, defined_risk_count: 0,
        undefined_risk_count: 0, avg_kelly_optimal: null,
      },
      "/api/orders": {
        last_sync: timestamp, open_count: 0, executed_count: 0,
        open_orders: [], executed_orders: [],
      },
      "/api/blotter": { as_of: timestamp, closed_trades: [], open_trades: [], summary: {} },
      "/api/cash-flows": { rows: [], count: 0, summary: {} },
      "/api/watchlist": { watchlist: [] },
      "/api/service-health": { services: [] },
      "/api/profile": { username: "Operator", avatar_url: null },
      "/api/alerts": { alerts: [] },
      "/api/flex-token": { remaining: 240 },
      "/api/previous-close": { closes: {} },
      "/api/ib-status": { connected: true },
      "/api/regime": { score: 15 },
    };
    if (path === "/api/portfolio" || path === "/api/orders") {
      if (request.method() === "GET") reads[path === "/api/portfolio" ? "portfolio" : "orders"] += 1;
      else reads.posts += 1;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payloads[path] ?? {}) });
  });
  // Prevent the realtime relay from reaching a live broker transport.
  await page.routeWebSocket(/\/ws(?:\/|\?|$)/, (socket) => socket.close());
  await page.goto("/orders", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("orders-command-strip")).toBeVisible();
  await expect(page.getByTestId("open-orders-empty")).toBeVisible();
  await page.waitForLoadState("networkidle");
  expect(reads.portfolio).toBeGreaterThan(0);
  expect(reads.orders).toBeGreaterThan(0);
  return reads;
}

for (const { name, now, interval } of [
  { name: "regular", now: OPEN, interval: 2_000 },
  { name: "extended", now: EXTENDED, interval: 30_000 },
  { name: "closed", now: CLOSED, interval: 30_000 },
]) {
  test(`${name} hours: orders read every ${interval / 1_000}s and portfolio ${name === "closed" ? "pauses" : "matches"}`, async ({ page }, testInfo) => {
    const reads = await mockAccount(page, now);
    for (let cycle = 0; cycle < 2; cycle += 1) {
      const before = { ...reads };
      await page.clock.runFor(interval - 1);
      expect(reads.portfolio).toBe(before.portfolio);
      expect(reads.orders).toBe(before.orders);
      await page.clock.runFor(1);
      await expect.poll(() => reads.portfolio).toBe(before.portfolio + (name === "closed" ? 0 : 1));
      await expect.poll(() => reads.orders).toBe(before.orders + 1);
      await page.waitForLoadState("networkidle");
    }
    expect(reads.posts, "healthy polling only reads cached snapshots").toBe(0);
    const path = testInfo.outputPath(`portfolio-orders-polling-${name}.png`);
    await page.screenshot({ path, fullPage: true, animations: "disabled" });
    await testInfo.attach(`portfolio-orders-polling-${name}`, { path, contentType: "image/png" });
  });
}

test("mounted orders workspace switches to 2s reads at the regular session boundary", async ({ page }) => {
  const reads = await mockAccount(page, new Date("2026-10-13T13:29:00Z"));
  // The existing market-hours owner checks the session once per minute.
  await page.clock.runFor(60_000);
  await page.waitForLoadState("networkidle");
  const before = { ...reads };
  await page.clock.runFor(1_999);
  expect(reads.portfolio).toBe(before.portfolio);
  expect(reads.orders).toBe(before.orders);
  await page.clock.runFor(1);
  await expect.poll(() => reads.portfolio).toBe(before.portfolio + 1);
  await expect.poll(() => reads.orders).toBe(before.orders + 1);
  expect(reads.posts).toBe(0);
});
