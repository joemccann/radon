import { expect, test, type Page, type TestInfo } from "@playwright/test";

const TICKER = "NVDA";
const NOW = "2026-09-16T17:00:00.000Z";
const EXPIRY = "20261218";
const POSITION = {
  id: 71, ticker: TICKER, structure: "Call credit spread $95/$105", structure_type: "Bear Call Spread",
  risk_profile: "defined", expiry: "2026-12-18", contracts: 2, direction: "CREDIT",
  entry_cost: -500, max_risk: 1500, market_value: -600, entry_date: "2026-09-14",
  kelly_optimal: null, target: null, stop: null,
  legs: [
    { direction: "SHORT", type: "Call", strike: 95, contracts: 2, entry_cost: 1000, avg_cost: 500, market_price: 5, market_value: 1000 },
    { direction: "LONG", type: "Call", strike: 105, contracts: 2, entry_cost: 500, avg_cost: 250, market_price: 2, market_value: 400 },
  ],
};

type NewsState = "populated" | "empty" | "error";
test.describe.configure({ timeout: 90_000 });

async function installFixtures(page: Page, theme: "light" | "dark") {
  let newsState: NewsState = "populated";
  const placements: string[] = [];
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.clock.setFixedTime(new Date(NOW));
  await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/orders/place") placements.push(route.request().postData() ?? "");
    const fixtures: Record<string, unknown> = {
      "/api/portfolio": {
        bankroll: 1_000_000, peak_value: 1_000_000, positions: [POSITION], position_count: 1,
        total_deployed_pct: 0.15, total_deployed_dollars: 1500, remaining_capacity_pct: 99.85,
        defined_risk_count: 1, undefined_risk_count: 0, avg_kelly_optimal: null,
        exposure: {}, violations: [], last_sync: NOW,
        account_summary: { net_liquidation: 1_000_000, settled_cash: 900_000, buying_power: 2_000_000, excess_liquidity: 900_000, maintenance_margin: 100_000 },
      },
      "/api/orders": { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0, last_sync: NOW },
      "/api/ib/ws-ticket": { ticket: "isolated-instrument-workspace" },
      "/api/ib-status": { connected: true },
      "/api/regime": { score: 15, cri: { score: 15 } },
      "/api/blotter": { closed_trades: [], open_trades: [], summary: { realized_pnl: 0 } },
      "/api/ticker/info": { uw_info: { name: "NVIDIA Corporation", sector: "Technology", description: "Isolated company fixture with a long description of compute infrastructure and its supply chain. ".repeat(10) }, stock_state: {}, profile: {}, stats: {} },
      "/api/ticker/ratings": { ticker: TICKER, recommendation: "buy", analyst_count: 12, ratings: { strong_buy: 4, buy: 6, hold: 2 }, target_price: { low: 90, high: 150, mean: 120, median: 119, count: 12 }, recent_changes: Array.from({ length: 15 }, (_, index) => ({ date: "2026-09-15", firm: `Research firm ${index + 1}`, action: "upgrade", to_grade: "Buy" })) },
      "/api/ticker/seasonality": { source: "unusualwhales", data: Array.from({ length: 12 }, (_, index) => ({ month: index + 1, avg_change: 0.02, median_change: 0.01, max_change: 0.09, min_change: -0.04, positive_closes: 7, positive_months_perc: 0.7, years: 10 })) },
      "/api/equibles-smart-money-13f": { missing: true },
      "/api/equibles-filing-forensics": { missing: true, checks: [] },
      "/api/options/expirations": { symbol: TICKER, expirations: [EXPIRY] },
      "/api/options/chain": { symbol: TICKER, expiry: EXPIRY, strikes: Array.from({ length: 61 }, (_, index) => 70 + index), exchange: "SMART", multiplier: "100" },
      "/api/risk-free-rate": { rate: 0.04 },
      "/api/watchlist": { watchlist: [] },
      "/api/service-health": { services: [] },
    };
    let body = fixtures[url.pathname];
    let status = body === undefined ? 503 : 200;
    if (url.pathname === "/api/ticker/news") {
      status = newsState === "error" ? 503 : 200;
      body = newsState === "error" ? { error: "News provider is temporarily unavailable" } : {
        source: "unusualwhales",
        data: newsState === "empty" ? [] : Array.from({ length: 30 }, (_, index) => ({
          headline: `Research item ${index + 1}: NVIDIA compute capacity and enterprise demand across international suppliers, platforms and customers`,
          source: "Fixture News", created_at: NOW, tickers: [TICKER], is_major: index === 0,
          url: `https://example.invalid/research/${index + 1}`,
        })),
      };
    }
    return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body ?? { error: "Unavailable in isolated workspace fixture" }) });
  });
  const quote = (symbol: string, price: number) => ({
    symbol, last: price, bid: price - 0.05, ask: price + 0.05, close: symbol === TICKER ? 99 : price,
    timestamp: NOW, lastIsCalculated: false, volume: 1200, delta: 0.4, impliedVol: 0.45, undPrice: 100,
  });
  await page.routeWebSocket(/(?:localhost|127\.0\.0\.1):(?:18765|8765)|\/ws(?:\?|$)/, (socket) => {
    socket.onMessage((raw) => {
      const message = JSON.parse(raw.toString());
      if (message.action !== "subscribe") return;
      const updates: Record<string, unknown> = {
        [TICKER]: quote(TICKER, 100),
        [`${TICKER}_${EXPIRY}_95_C`]: quote(`${TICKER}_${EXPIRY}_95_C`, 5),
        [`${TICKER}_${EXPIRY}_105_C`]: quote(`${TICKER}_${EXPIRY}_105_C`, 2),
      };
      for (const contract of message.contracts ?? []) {
        const key = `${contract.symbol}_${String(contract.expiry).replaceAll("-", "")}_${contract.strike}_${contract.right}`;
        updates[key] ??= quote(key, 3.5);
      }
      socket.send(JSON.stringify({ type: "status", ib_connected: true, subscriptions: [TICKER] }));
      socket.send(JSON.stringify({ type: "batch", updates }));
    });
  });
  return { placements, pageErrors, setNewsState: (state: NewsState) => { newsState = state; } };
}

async function expectDeckUrl(page: Page, deck: string | null) {
  await expect.poll(() => new URL(page.url()).searchParams.get("deck")).toBe(deck);
  expect(new URL(page.url()).searchParams.get("posId")).toBe(String(POSITION.id));
}

async function expectDesktopCanvas(page: Page) {
  const sidebar = page.getByTestId("chain-instrument-sidebar");
  await expect(sidebar).toBeVisible();
  await expect(page.locator(".instrument-workspace")).toBeVisible();
  await expect(page.getByTestId("chain-feed-trigger")).toBeVisible();
  await expect(page.getByTestId("chain-underlying-quote")).toContainText("100.00");
  await expect(page.getByTestId("chain-held-quote")).toContainText("Spread net");
  await expect(page.getByTestId("chain-held-quote")).toContainText("-3.00");
  expect(await page.locator("header.header").evaluate((element) => element.getBoundingClientRect().height)).toBeCloseTo(48, 0);
  expect(await sidebar.evaluate((element) => element.getBoundingClientRect().width)).toBeCloseTo(208, 0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
}

async function expectFullDeck(page: Page) {
  await expectDesktopCanvas(page);
  const sidebar = await page.getByTestId("chain-instrument-sidebar").boundingBox();
  const deck = await page.locator(".asset-deck.open").boundingBox();
  expect(sidebar).not.toBeNull();
  expect(deck).not.toBeNull();
  expect(deck!.x).toBeCloseTo(sidebar!.x + sidebar!.width, 0);
  expect(deck!.x + deck!.width).toBeCloseTo(page.viewportSize()!.width, 0);
  expect(deck!.y).toBeCloseTo(sidebar!.y, 0);
  expect(await page.locator(".asset-deck.open .asset-deck-hd").evaluate((element) => element.getBoundingClientRect().height)).toBeCloseTo(56, 0);
}

async function screenshot(page: Page, testInfo: TestInfo, name: string) {
  const path = testInfo.outputPath(`instrument-workspace-${name}.png`);
  await page.screenshot({ path });
  await testInfo.attach(name, { path, contentType: "image/png" });
}

for (const theme of ["light", "dark"] as const) {
  for (const viewport of [{ width: 1440, height: 800 }, { width: 1280, height: 720 }]) {
    test(`instrument workspace ${theme} ${viewport.width}: persistent context across every desktop view`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport);
      const fixture = await installFixtures(page, theme);
      await page.goto(`/${TICKER}?posId=${POSITION.id}&deck=c`);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expectDesktopCanvas(page);
      const sidebar = page.getByTestId("chain-instrument-sidebar");
      const originalSidebar = await sidebar.boundingBox();
      await sidebar.getByRole("button", { name: /^Position\b/ }).click();
      await expectDeckUrl(page, "p");
      await expect(sidebar.getByRole("button", { name: /^Position\b/ })).toHaveAttribute("aria-current", "page");
      await expect(page.locator(".pos-legs-table tbody tr")).toHaveCount(2);
      await expect(page.locator(".pos-legs-table")).toContainText("SHORT");
      await expect(page.locator(".pos-legs-table")).toContainText("LONG");
      await expect(page.getByTestId("pos-stat-entry-cost")).toHaveText("-$500");
      await expectFullDeck(page);
      expect(await sidebar.boundingBox()).toEqual(originalSidebar);
      await screenshot(page, testInfo, `${theme}-${viewport.width}-position`);

      // The real position closing surface and risk chokepoint remain reachable.
      // Review stops before Confirm Order, and every API is intercepted anyway.
      await page.getByTestId("pos-trade-combo").click();
      const ticket = page.getByTestId("position-trade-ticket");
      await ticket.getByTestId("position-trade-limit").fill("-3.00");
      await ticket.getByRole("button", { name: "Review Order", exact: true }).click();
      await expect(ticket.getByTestId("order-confirm-summary")).toBeVisible();
      const confirm = ticket.getByRole("button", { name: "Confirm Order", exact: true });
      await confirm.scrollIntoViewIfNeeded();
      await expect(confirm).toBeInViewport({ ratio: 1 });
      await expectDesktopCanvas(page);
      await screenshot(page, testInfo, `${theme}-${viewport.width}-position-risk`);

      await sidebar.getByRole("button", { name: /^News\b/ }).click();
      await expectDeckUrl(page, "n");
      await expect(sidebar.getByRole("button", { name: /^News\b/ })).toHaveAttribute("aria-current", "page");
      await expect(page.locator(".news-item")).toHaveCount(30);
      await expectFullDeck(page);
      const body = page.locator(".asset-deck.open .asset-deck-body");
      await expect.poll(() => body.evaluate((element) => element.scrollHeight - element.clientHeight)).toBeGreaterThan(0);
      const toolbarBefore = await page.locator(".asset-deck.open .asset-deck-hd").boundingBox();
      await body.evaluate((element) => { element.scrollTop = element.scrollHeight; });
      await expect(page.locator(".news-item").last()).toBeInViewport();
      expect(await page.locator(".asset-deck.open .asset-deck-hd").boundingBox()).toEqual(toolbarBefore);
      expect(await sidebar.boundingBox()).toEqual(originalSidebar);
      await screenshot(page, testInfo, `${theme}-${viewport.width}-news`);

      for (const view of [
        { label: "Ratings", deck: "r", content: ".ratings-tab" },
        { label: "Seasonality", deck: "s", content: ".seasonality-tab" },
        { label: "Company", deck: "i", content: ".company-tab" },
        { label: "13F holdings", deck: "h", text: "No 13F positioning yet" },
        { label: "Filings", deck: "f", text: "No dossier yet" },
        { label: "Commands", deck: null, content: ".asset-deck-palette" },
      ]) {
        await sidebar.locator("summary").click();
        await sidebar.getByRole("button", { name: view.label, exact: true }).click();
        await expectDeckUrl(page, view.deck);
        await expect(sidebar.locator("summary")).toContainText(view.label);
        await expect(sidebar.locator("summary")).toHaveAttribute("data-active", "true");
        if (view.content) await expect(page.locator(view.content)).toBeVisible();
        else await expect(page.locator(".asset-deck-body").getByText(view.text!, { exact: true })).toBeVisible();
        await expectFullDeck(page);
        expect(await sidebar.boundingBox()).toEqual(originalSidebar);
      }

      // Commands has no URL form; Escape restores book+trade without changing
      // instrument identity, and the keyboard shortcuts reopen reference views.
      await page.keyboard.press("Escape");
      await expect(page.locator(".asset-deck.open")).toHaveCount(0);
      await expect(page.locator(".book-region")).toBeVisible();
      await expect(page.locator(".act-ticket")).toBeVisible();
      await expect(page.locator(".cockpit-head")).toBeVisible();
      await expectDesktopCanvas(page);
      await expect(sidebar.getByRole("button", { name: /^Book & trade/ }).last()).toHaveAttribute("aria-current", "page");
      await screenshot(page, testInfo, `${theme}-${viewport.width}-book`);
      await page.keyboard.press("n");
      await expectDeckUrl(page, "n");
      await expect(page.locator(".news-item")).toHaveCount(30);
      await page.keyboard.press("c");
      await expectDeckUrl(page, "c");
      await expect(page.getByTestId("chain-first-toolbar")).toBeVisible();
      await expectDesktopCanvas(page);
      expect(fixture.placements).toEqual([]);
      expect(fixture.pageErrors).toEqual([]);
    });
  }

  test(`instrument workspace ${theme}: news empty/error/recovery preserve the same canvas`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    const fixture = await installFixtures(page, theme);
    fixture.setNewsState("empty");
    await page.goto(`/${TICKER}?posId=${POSITION.id}&deck=n`);
    await expect(page.getByText(`No recent news for ${TICKER}`, { exact: true })).toBeVisible();
    await expectFullDeck(page);
    const sidebar = page.getByTestId("chain-instrument-sidebar");
    const originalSidebar = await sidebar.boundingBox();
    await screenshot(page, testInfo, `${theme}-news-empty`);
    for (const state of ["error", "populated"] as const) {
      fixture.setNewsState(state);
      await sidebar.getByRole("button", { name: /^Position\b/ }).click();
      await expectDeckUrl(page, "p");
      await sidebar.getByRole("button", { name: /^News\b/ }).click();
      if (state === "error") await expect(page.locator(".toast-error .toast-message")).toHaveText("News provider is temporarily unavailable");
      else await expect(page.locator(".news-item")).toHaveCount(30);
      await expectFullDeck(page);
      expect(await sidebar.boundingBox()).toEqual(originalSidebar);
      await screenshot(page, testInfo, `${theme}-news-${state}`);
    }
    expect(fixture.pageErrors).toEqual([]);
  });

  test(`instrument workspace ${theme}: mobile retains its instrument navigation and chain ladder`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 393, height: 852 });
    const fixture = await installFixtures(page, theme);
    await page.goto(`/${TICKER}?posId=${POSITION.id}&deck=p`);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await expect(page.getByTestId("chain-instrument-sidebar")).toBeHidden();
    await expect(page.getByTestId("chain-feed-trigger")).toBeHidden();
    await expect(page.locator(".pos-legs-table tbody tr")).toHaveCount(2);
    await page.locator(".glyph-rail").getByRole("button", { name: "News", exact: true }).click();
    await expect(page.locator(".news-item")).toHaveCount(30);
    await page.locator(".glyph-rail").getByRole("button", { name: "Chain", exact: true }).click();
    await expect(page.getByTestId("mobile-chain-side-calls")).toBeVisible();
    await expect(page.getByTestId("chain-spot-bar")).toContainText("100.00");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
    expect(fixture.placements).toEqual([]);
    expect(fixture.pageErrors).toEqual([]);
    await screenshot(page, testInfo, `${theme}-mobile`);
  });
}
