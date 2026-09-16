import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";

const TICKER = "NVDA";
const NOW = "2026-09-16T17:00:00.000Z";
const EXPIRIES = ["20261218", "20270115"];
const STRIKES = Array.from({ length: 61 }, (_, index) => 70 + index);

test.describe.configure({ timeout: 60_000 });

/** Every API and market socket is browser-local; no order can reach a broker. */
async function installFixtures(page: Page, theme: "light" | "dark") {
  const placements: string[] = [];
  await page.clock.setFixedTime(new Date(NOW));
  await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/orders/place") placements.push(route.request().postData() ?? "");
    const fixtures: Record<string, unknown> = {
      "/api/portfolio": {
        bankroll: 1_000_000, peak_value: 1_000_000, positions: [], position_count: 0,
        total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
        defined_risk_count: 0, undefined_risk_count: 0, avg_kelly_optimal: null,
        exposure: {}, violations: [], last_sync: NOW,
        account_summary: { net_liquidation: 1_000_000, settled_cash: 900_000, buying_power: 2_000_000, excess_liquidity: 900_000, maintenance_margin: 100_000 },
      },
      "/api/orders": { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0, last_sync: NOW },
      "/api/ib/ws-ticket": { ticket: "isolated-chain-first" },
      "/api/ib-status": { connected: true },
      "/api/regime": { score: 15, cri: { score: 15 } },
      "/api/blotter": { closed_trades: [], open_trades: [], summary: { realized_pnl: 0 } },
      "/api/ticker/info": { uw_info: { name: "NVIDIA" }, stock_state: {}, profile: {}, stats: {} },
      "/api/options/expirations": { symbol: TICKER, expirations: EXPIRIES },
      "/api/options/chain": { symbol: TICKER, expiry: url.searchParams.get("expiry"), strikes: STRIKES, exchange: "SMART", multiplier: "100" },
      "/api/risk-free-rate": { rate: 0.04 },
      "/api/watchlist": { watchlist: [] },
      "/api/service-health": { services: [] },
    };
    const body = fixtures[url.pathname];
    return route.fulfill({ status: body === undefined ? 503 : 200, contentType: "application/json", body: JSON.stringify(body ?? { error: "Unavailable in isolated chain fixture" }) });
  });
  const quote = (symbol: string, price: number) => ({
    symbol, last: price, bid: price - 0.05, ask: price + 0.05,
    close: symbol === TICKER ? 99 : price, timestamp: NOW, lastIsCalculated: false,
    volume: 1200, delta: 0.4, impliedVol: 0.45, undPrice: 100,
  });
  await page.routeWebSocket(/(?:localhost|127\.0\.0\.1):(?:18765|8765)|\/ws(?:\?|$)/, (socket) => {
    socket.onMessage((raw) => {
      const message = JSON.parse(raw.toString());
      if (message.action !== "subscribe") return;
      const updates: Record<string, unknown> = { [TICKER]: quote(TICKER, 100) };
      for (const contract of message.contracts ?? []) {
        const key = `${contract.symbol}_${String(contract.expiry).replaceAll("-", "")}_${contract.strike}_${contract.right}`;
        updates[key] = quote(key, 3.5);
      }
      socket.send(JSON.stringify({ type: "status", ib_connected: true, subscriptions: [TICKER] }));
      socket.send(JSON.stringify({ type: "batch", updates }));
    });
  });
  return placements;
}

async function chainGeometry(page: Page) {
  return page.locator(".chain-anchor-panes").evaluate((chain) => {
    const panes = Array.from(chain.querySelectorAll<HTMLElement>(".chain-anchor-pane"));
    const fullRows: { strike: string | null; height: number }[] = [];
    const quoteTops: number[] = [];
    for (const pane of panes) {
      const clip = pane.getBoundingClientRect();
      const top = Math.max(clip.top, 0);
      const bottom = Math.min(clip.bottom, window.innerHeight);
      for (const row of pane.querySelectorAll<HTMLElement>(".chain-row")) {
        const rect = row.getBoundingClientRect();
        if (rect.bottom > top && rect.top < bottom) quoteTops.push(Math.max(rect.top, top));
        if (rect.top >= top - 0.5 && rect.bottom <= bottom + 0.5) {
          fullRows.push({ strike: row.getAttribute("data-strike"), height: rect.height });
        }
      }
    }
    return {
      fullRows,
      firstQuotesTop: Math.min(...quoteTops),
      horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      pageScroll: window.scrollY,
    };
  });
}

async function screenshot(page: Page, testInfo: TestInfo, name: string) {
  const path = testInfo.outputPath(`chain-first-${name}.png`);
  await page.screenshot({ path });
  await testInfo.attach(name, { path, contentType: "image/png" });
}

async function scrollToBottom(region: Locator) {
  await region.evaluate((element) => { element.scrollTop = element.scrollHeight; });
}

for (const theme of ["light", "dark"] as const) {
  for (const viewport of [
    { width: 1440, height: 800, minimumRows: 14 },
    { width: 1280, height: 720, minimumRows: 12 },
  ]) {
    test(`chain-first ${theme} ${viewport.width}x${viewport.height}: readable strikes fill the viewport`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await installFixtures(page, theme);
      await page.goto(`/${TICKER}?deck=c`);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(page.getByTestId("chain-instrument-sidebar")).toBeVisible();
      await expect(page.getByTestId("chain-underlying-quote")).toContainText("100.00");
      await expect(page.getByTestId("chain-spot-bar")).toContainText("100.00");
      await expect(page.locator('.chain-row[data-strike="100"] .chain-mid').first()).toContainText("3.50");
      await expect(page.locator(".order-builder--rail")).toHaveCount(0);
      await expect.poll(async () => (await chainGeometry(page)).fullRows.length).toBeGreaterThanOrEqual(viewport.minimumRows);
      const geometry = await chainGeometry(page);
      expect(geometry.firstQuotesTop).toBeLessThanOrEqual(160);
      expect(geometry.horizontalOverflow).toBeLessThanOrEqual(1);
      expect(geometry.pageScroll).toBe(0);
      for (const row of geometry.fullRows) expect(row.height, `strike ${row.strike} retains readable row height`).toBeCloseTo(40, 0);
      await screenshot(page, testInfo, `${theme}-${viewport.width}x${viewport.height}`);
    });
  }

  test(`chain-first ${theme}: filters, feed disclosure, staged risk and instrument navigation remain usable`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 800 });
    const placements = await installFixtures(page, theme);
    await page.goto(`/${TICKER}?deck=c`);
    const toolbar = page.getByTestId("chain-first-toolbar");
    await expect(page.getByTestId("chain-spot-bar")).toContainText("100.00");
    await toolbar.getByRole("combobox", { name: "Strikes per side" }).selectOption("25");
    await expect(page).toHaveURL(/strikes=25/);
    for (const side of ["CALLS", "PUTS", "ALL"]) {
      await toolbar.getByRole("button", { name: side, exact: true }).click();
      await expect(page.locator(".chain-anchor-panes")).toHaveAttribute("data-side", side === "ALL" ? "both" : side.toLowerCase());
      await expect(page.locator('.chain-row[data-strike="100"] td')).toHaveCount(side === "ALL" ? 17 : 9);
    }
    const upper = page.getByTestId("chain-upper-pane");
    const lower = page.getByTestId("chain-lower-pane");
    await scrollToBottom(lower);
    await expect.poll(() => lower.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
    await toolbar.getByRole("button", { name: "Recenter chain view", exact: true }).click();
    await expect.poll(() => lower.evaluate((element) => element.scrollTop)).toBe(0);
    await expect.poll(() => upper.evaluate((element) => Math.abs(element.scrollTop - (element.scrollHeight - element.clientHeight)))).toBeLessThan(2);

    const beforeDisclosure = await toolbar.boundingBox();
    await page.getByTestId("chain-feed-trigger").click();
    const feedPanel = page.getByTestId("chain-feed-panel");
    await expect(feedPanel).toBeVisible();
    // Visibility alone passes when a higher stacking context paints over the
    // panel. The center must hit the disclosure, and its sync action must be
    // reachable without issuing an actual refresh.
    await expect.poll(() => feedPanel.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return element.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
    })).toBe(true);
    await feedPanel.getByRole("button", { name: "Sync Now", exact: true }).click({ trial: true });
    expect(await toolbar.boundingBox()).toEqual(beforeDisclosure);
    await screenshot(page, testInfo, `${theme}-feed-disclosure`);
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("chain-feed-panel")).toBeHidden();
    await expect(page.getByTestId("chain-feed-trigger")).toBeFocused();
    await expect(page.locator(".chain-first-cockpit")).toBeVisible();

    // Several legs make the ticket longer than its cell without changing the
    // independent chain panes. Verify stops at review; it never transmits.
    for (const strike of [100, 101, 102, 103, 104]) {
      const row = page.locator(`.chain-row[data-strike="${strike}"]`);
      await expect(row.locator(".chain-mid").first()).toContainText("3.50");
      await row.locator(".chain-mid.chain-clickable").first().click();
    }
    const builder = page.locator(".order-builder--rail");
    await expect(builder.getByTestId("order-builder-leg")).toHaveCount(5);
    await toolbar.getByRole("combobox", { name: "Options expiry" }).selectOption(EXPIRIES[1]);
    await expect(builder.getByTestId("order-builder-leg")).toHaveCount(5);
    await expect(builder.locator(".order-builder-leg-expiry").first()).toHaveText("2026-12-18");
    await expect.poll(() => builder.evaluate((element) => element.scrollHeight - element.clientHeight)).toBeGreaterThan(0);
    const chainBeforeTicketScroll = await lower.evaluate((element) => element.scrollTop);
    await scrollToBottom(builder);
    await expect(builder.getByTestId("ticket-verify")).toBeInViewport({ ratio: 1 });
    await expect(builder.getByTestId("ticket-verify")).toBeEnabled();
    await builder.getByTestId("ticket-verify").click();
    await expect(builder.getByTestId("order-confirm-summary")).toContainText(/max loss/i);
    await scrollToBottom(builder);
    await expect(builder.getByTestId("ticket-transmit")).toBeInViewport({ ratio: 1 });
    expect(await lower.evaluate((element) => element.scrollTop)).toBe(chainBeforeTicketScroll);
    expect((await chainGeometry(page)).horizontalOverflow).toBeLessThanOrEqual(1);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    expect(placements).toEqual([]);
    await screenshot(page, testInfo, `${theme}-staged-risk`);

    const sidebar = page.getByTestId("chain-instrument-sidebar");
    await sidebar.locator("summary").click();
    await expect(sidebar.getByRole("button", { name: "Company", exact: true })).toBeVisible();
    await sidebar.locator("summary").click();
    await sidebar.getByRole("button", { name: /^Position\b/ }).click();
    await expect(page.locator(".chain-first-cockpit")).toHaveCount(0);
    await expect(page.locator(".instrument-workspace")).toBeVisible();
    await expect(sidebar).toBeVisible();
    await expect(sidebar.getByRole("button", { name: /^Position\b/ })).toHaveAttribute("aria-current", "page");
    await expect(page.locator(".asset-deck.open .asset-deck-hd")).toBeVisible();
    await expect(page.getByTestId("chain-feed-trigger")).toBeVisible();
  });

  test(`chain-first ${theme}: mobile retains its ladder, expiry and pending-order controls`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 393, height: 852 });
    const placements = await installFixtures(page, theme);
    await page.goto(`/${TICKER}?deck=c`);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await expect(page.getByTestId("chain-instrument-sidebar")).toBeHidden();
    await expect(page.getByTestId("chain-feed-trigger")).toBeHidden();
    await expect(page.getByTestId("chain-spot-bar")).toContainText("100.00");
    await page.getByTestId("mobile-chain-side-calls").click();
    await expect(page.getByTestId("mobile-chain-put-100")).toHaveCount(0);
    await page.getByTestId("mobile-chain-call-100").click();
    await page.getByTestId("mobile-chain-detail-buy").click();
    await expect(page.getByTestId("mobile-chain-pending-strip")).toContainText("1 LEG");
    await page.getByTestId(`mobile-chain-expiry-${EXPIRIES[1]}`).click();
    await expect(page.getByTestId(`mobile-chain-expiry-${EXPIRIES[1]}`)).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("mobile-chain-pending-strip")).toContainText("1 LEG");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
    expect(placements).toEqual([]);
    await screenshot(page, testInfo, `${theme}-mobile`);
  });
}
