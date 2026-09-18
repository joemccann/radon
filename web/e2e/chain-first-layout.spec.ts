import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";

const WEB_ROOT = fileURLToPath(new URL("..", import.meta.url));

const TICKER = "NVDA";
const NOW = "2026-09-16T17:00:00.000Z";
const EXPIRIES = ["20261218", "20270115"];
const STRIKES = Array.from({ length: 61 }, (_, index) => 70 + index);
const HIGH_TICKER = "SNDK";
const HIGH_SPOT = 1737.28;
const HIGH_STRIKES = Array.from({ length: 29 }, (_, index) => 1670 + index * 5);

test.describe.configure({ timeout: 60_000 });

type ChainFixture = {
  ticker?: string;
  name?: string;
  spot?: number;
  close?: number;
  strikes?: number[];
};

/** Every API and market socket is browser-local; no order can reach a broker. */
async function installFixtures(page: Page, theme: "light" | "dark", fixture: ChainFixture = {}) {
  const ticker = fixture.ticker ?? TICKER;
  const spot = fixture.spot ?? 100;
  const close = fixture.close ?? (ticker === TICKER ? 99 : spot);
  const strikes = fixture.strikes ?? STRIKES;
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
      "/api/ticker/info": { uw_info: { name: fixture.name ?? "NVIDIA" }, stock_state: {}, profile: {}, stats: {} },
      "/api/options/expirations": { symbol: ticker, expirations: EXPIRIES },
      "/api/options/chain": { symbol: ticker, expiry: url.searchParams.get("expiry"), strikes, exchange: "SMART", multiplier: "100" },
      "/api/risk-free-rate": { rate: 0.04 },
      "/api/watchlist": { watchlist: [] },
      "/api/service-health": { services: [] },
    };
    const body = fixtures[url.pathname];
    return route.fulfill({ status: body === undefined ? 503 : 200, contentType: "application/json", body: JSON.stringify(body ?? { error: "Unavailable in isolated chain fixture" }) });
  });
  const quote = (symbol: string, price: number) => ({
    symbol, last: price, bid: price - 0.05, ask: price + 0.05,
    close: symbol === ticker ? close : price, timestamp: NOW, lastIsCalculated: false,
    volume: 1200, delta: 0.4, impliedVol: 0.45, undPrice: spot,
  });
  await page.routeWebSocket(/(?:localhost|127\.0\.0\.1):(?:18765|8765)|\/ws(?:\?|$)/, (socket) => {
    socket.onMessage((raw) => {
      const message = JSON.parse(raw.toString());
      if (message.action !== "subscribe") return;
      const updates: Record<string, unknown> = { [ticker]: quote(ticker, spot) };
      for (const contract of message.contracts ?? []) {
        const key = `${contract.symbol}_${String(contract.expiry).replaceAll("-", "")}_${contract.strike}_${contract.right}`;
        updates[key] = quote(key, 3.5);
      }
      socket.send(JSON.stringify({ type: "status", ib_connected: true, subscriptions: [ticker] }));
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

test("chain-first strike column CSS fits four- and five-digit grouped labels", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 800 });
  const globals = readFileSync(join(WEB_ROOT, "app/globals.css"), "utf8");
  const chainFirst = readFileSync(
    join(WEB_ROOT, "components/ticker-detail/ChainFirst.module.css"),
    "utf8",
  ).replace(/:global\(([^)]+)\)/g, "$1");
  await page.setContent(`<!doctype html><html><head><style>
html { font-size: 16px; --font-sans: system-ui, sans-serif; --text-meta: 12px; --space-2: 8px; --bg-panel-raised: #111; --line-grid: #222; --radius-lg: 6px; }
${globals}
${chainFirst}
</style></head><body>
  <div class="chain-first-cockpit">
    <div class="chainFirst">
      <div class="chain-grid-wrapper chain-anchor-panes" data-side="calls">
        <table class="chain-grid">
          <colgroup><col class="chain-anchor-strike-col" /></colgroup>
          <tbody>
            <tr class="chain-row"><td class="chain-cell chain-strike">$1,737.00</td></tr>
            <tr class="chain-row"><td class="chain-cell chain-strike">$12,345.00</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</body></html>`);
  const clipped = await page.locator("td.chain-strike").evaluateAll((cells) =>
    cells.map((cell) => ({
      text: cell.textContent?.trim() ?? "",
      clientWidth: Math.round(cell.clientWidth),
      scrollWidth: Math.round(cell.scrollWidth),
    })).filter((cell) => cell.scrollWidth > cell.clientWidth + 1),
  );
  expect(clipped, `ellipsis on ${JSON.stringify(clipped)}`).toEqual([]);
});

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

  test(`chain-first ${theme}: four-digit strike labels stay fully visible on the calls ladder`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 800 });
    await installFixtures(page, theme, {
      ticker: HIGH_TICKER,
      name: "Sandisk",
      spot: HIGH_SPOT,
      close: 1614.5,
      strikes: HIGH_STRIKES,
    });
    await page.goto(`/${HIGH_TICKER}?deck=c&side=calls`);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await expect(page.getByTestId("chain-instrument-sidebar")).toBeVisible();
    await expect(page.getByTestId("chain-underlying-quote")).toContainText("1,737.28");
    await expect(page.locator(".chain-first-cockpit")).toBeVisible();
    const atm = page.locator('.chain-row[data-strike="1735"] .chain-strike');
    await expect(atm).toHaveText("$1,735.00");
    const clipped = await page.locator("td.chain-strike").evaluateAll((cells) =>
      cells
        .filter((cell) => {
          const rect = cell.getBoundingClientRect();
          return rect.height > 0 && rect.width > 0;
        })
        .map((cell) => ({
          text: cell.textContent?.trim() ?? "",
          clientWidth: Math.round(cell.clientWidth),
          scrollWidth: Math.round(cell.scrollWidth),
        }))
        .filter((cell) => cell.scrollWidth > cell.clientWidth + 1),
    );
    expect(clipped, `ellipsis on ${JSON.stringify(clipped)}`).toEqual([]);
    await screenshot(page, testInfo, `${theme}-four-digit-strikes`);
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
