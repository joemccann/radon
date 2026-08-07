import { expect, test, type Page } from "@playwright/test";

/**
 * Mobile cockpit book layout (2026-08-04 "broken and cluttered" report):
 *
 * 1. The MicrostructureStrip was stretched by `.book-tab-only > * { flex: 1 }`
 *    into a giant near-empty box (one line of content vertically centered in
 *    ~250px of border).
 * 2. Its nowrap "(±x.xx vs mid)" microprice tail pushed the document wider
 *    than the viewport (right-edge clipping).
 * 3. `.book-window-head` (nowrap + overflow hidden) clipped SPRD and the tape
 *    toggle clean off a 393px screen.
 * 4. The header position chip repeated the FULL structure string that the
 *    position summary right below it already shows.
 */

const now = new Date().toISOString();

function daysFromToday(n: number): string {
  return new Date(Date.now() + n * 86_400_000).toISOString().slice(0, 10);
}

const EXPIRY = daysFromToday(30);
const STRUCTURE = "Risk Reversal (P$243.0/C$247.0)";

const PORTFOLIO_MOCK = {
  bankroll: 100_000, peak_value: 100_000, last_sync: now,
  total_deployed_pct: 1.2, total_deployed_dollars: 1_200, remaining_capacity_pct: 98.8,
  position_count: 1, defined_risk_count: 0, undefined_risk_count: 1, avg_kelly_optimal: null,
  exposure: {}, violations: [],
  account_summary: {
    net_liquidation: 100_000, daily_pnl: 0, unrealized_pnl: 0, realized_pnl: 0,
    settled_cash: 100_000, maintenance_margin: 0, excess_liquidity: 100_000,
    buying_power: 200_000, dividends: 0,
  },
  positions: [
    {
      id: 12, ticker: "IWM", structure: STRUCTURE, structure_type: "Risk Reversal",
      risk_profile: "undefined", expiry: EXPIRY, contracts: 50, direction: "COMBO",
      entry_cost: -579.79, max_risk: null, market_value: 750,
      market_price_is_calculated: false, ib_daily_pnl: 0, kelly_optimal: null,
      target: null, stop: null, entry_date: daysFromToday(-7),
      legs: [
        { direction: "LONG", contracts: 50, type: "Call", strike: 247, entry_cost: 17285.02, avg_cost: 346, market_price: 3.63, market_value: 18150, market_price_is_calculated: false },
        { direction: "SHORT", contracts: 50, type: "Put", strike: 243, entry_cost: 17864.81, avg_cost: 357, market_price: 3.88, market_value: 19400, market_price_is_calculated: false },
      ],
    },
  ],
};

const IWM_PRICE = {
  symbol: "IWM", last: 244.65, lastIsCalculated: false, bid: 244.64, ask: 244.66,
  bidSize: 10, askSize: 10, volume: 10, high: null, low: null, open: null, close: 246,
  week52High: null, week52Low: null, avgVolume: null, delta: null, gamma: null,
  theta: null, vega: null, impliedVol: null, undPrice: null, timestamp: now,
};

// Uneven sizes so imbalance is nonzero and the "(±x.xx vs mid)" microprice
// premium span renders — the exact tail that used to overflow the viewport.
const IWM_DEPTH = {
  symbol: "IWM", kind: "stock", isSmartDepth: true, feed: "SMART DEPTH",
  entitled: true, timestamp: now,
  bid: [
    { price: 244.64, size: 300, marketMaker: "ARCA", exchange: "ARCA" },
    { price: 244.63, size: 200, marketMaker: "NSDQ", exchange: "NSDQ" },
    { price: 244.62, size: 150, marketMaker: "BATS", exchange: "BATS" },
  ],
  ask: [
    { price: 244.66, size: 120, marketMaker: "ARCA", exchange: "ARCA" },
    { price: 244.67, size: 100, marketMaker: "NSDQ", exchange: "NSDQ" },
    { price: 244.68, size: 90, marketMaker: "BATS", exchange: "BATS" },
  ],
};

async function installMockWebSocket(page: Page) {
  await page.addInitScript(
    ({ price, depth }) => {
      class MockWebSocket {
        static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        url: string; readyState = 0;
        onopen: ((e?: unknown) => void) | null = null;
        onmessage: ((e: { data: string }) => void) | null = null;
        onclose: ((e?: unknown) => void) | null = null;
        onerror: ((e?: unknown) => void) | null = null;
        constructor(url: string) {
          this.url = url;
          setTimeout(() => {
            this.readyState = 1;
            this.onopen?.({});
            this.emit({ type: "status", ib_connected: true, ib_issue: null, ib_status_message: null, subscriptions: [] });
          }, 0);
        }
        send(raw: string) {
          const message = JSON.parse(raw) as { action?: string; symbols?: string[]; symbol?: string };
          if (message.action === "subscribe" && message.symbols?.includes("IWM")) {
            this.emit({ type: "batch", updates: { IWM: price } });
          }
          if (message.action === "subscribe-depth" && message.symbol === "IWM") {
            this.emit({ type: "depth-batch", updates: { IWM: depth } });
          }
        }
        close() { this.readyState = 3; this.onclose?.({}); }
        emit(payload: unknown) { this.onmessage?.({ data: JSON.stringify(payload) }); }
      }
      // Only the Radon relay socket is mocked; Turbopack HMR passes through.
      const NativeWebSocket = window.WebSocket;
      const RelayAwareWebSocket = function (url: string | URL, protocols?: string | string[]) {
        return String(url).includes("localhost:8765")
          ? (new MockWebSocket(String(url)) as unknown as WebSocket)
          : new NativeWebSocket(url, protocols);
      } as unknown as typeof WebSocket;
      Object.assign(RelayAwareWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
      window.WebSocket = RelayAwareWebSocket;
    },
    { price: IWM_PRICE, depth: IWM_DEPTH },
  );
}

async function stubApis(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_MOCK) }),
  );
  await page.route("**/api/orders", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ last_sync: now, open_orders: [], executed_orders: [] }) }),
  );
  await page.route("**/api/regime", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, level: "LOW" }) }),
  );
  await page.route("**/api/ib-status", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  await page.route("**/api/blotter", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: now, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
  await page.route("**/api/cash-flows**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows: [], summary: {} }) }),
  );
  await page.route("**/api/flex-token", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, days_until_expiry: 14 }) }),
  );
  await page.route("**/api/ticker/**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) }),
  );
}

async function openCockpit(page: Page) {
  await installMockWebSocket(page);
  await stubApis(page);
  await page.goto("/IWM?posId=12");
  await expect(page.locator(".cockpit--mobile")).toBeVisible();
  await expect(page.locator(".microstructure-strip")).toBeVisible();
}

test("book pane never overflows the viewport horizontally", async ({ page }) => {
  await openCockpit(page);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow, `document is ${overflow}px wider than the viewport`).toBeLessThanOrEqual(0);
});

test("microstructure strip stays a compact one-line readout", async ({ page }) => {
  await openCockpit(page);
  const box = await page.locator(".microstructure-strip").boundingBox();
  expect(box).not.toBeNull();
  // One or two wrapped metric rows — never a flex-stretched half-screen box.
  expect(box!.height).toBeLessThanOrEqual(72);
});

test("the tape toggle is visible inside the viewport, not clipped off the head", async ({ page }) => {
  await openCockpit(page);
  const toggle = page.getByRole("switch", { name: "Toggle Time and Sales" });
  await expect(toggle).toBeVisible();
  const box = await toggle.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x + box!.width).toBeLessThanOrEqual(393);
});

test("the two-sided depth montage fits its card — no inner horizontal scrollbar", async ({ page }) => {
  await openCockpit(page);
  const { client, scroll } = await page.evaluate(() => {
    const sides = document.querySelector(".book-sides") as HTMLElement;
    return { client: sides.clientWidth, scroll: sides.scrollWidth };
  });
  expect(scroll, `montage needs ${scroll}px in a ${client}px card`).toBeLessThanOrEqual(client);
});

test("the book head keeps its intrinsic height on short viewports — never crushed by the montage", async ({ page }) => {
  // Real-device regression (2026-08-04, second report): the flex column
  // shrank the head under height pressure once its 45px floor was removed —
  // the montage colheads rode over the half-clipped MID/BID/ASK row. A 660px
  // viewport reproduces the PWA's effective height with app chrome.
  await page.setViewportSize({ width: 393, height: 660 });
  await openCockpit(page);
  const head = page.locator(".book-window-head");
  const clipped = await head.evaluate(
    (el) => el.scrollHeight - el.clientHeight,
  );
  expect(clipped, `head content is ${clipped}px taller than its box`).toBeLessThanOrEqual(1);
  await expect(page.locator(".book-head-stat", { hasText: "SPRD" })).toBeVisible();
  // The montage must keep its two-row floor AND stay inside the card's box —
  // a floor that escapes the window's overflow clip renders zero rows.
  const { montageHeight, escape } = await page.evaluate(() => {
    const win = document.querySelector(".book-region .book-window")!.getBoundingClientRect();
    const montage = document.querySelector(".book-montage")!.getBoundingClientRect();
    return { montageHeight: montage.height, escape: montage.bottom - win.bottom };
  });
  expect(montageHeight).toBeGreaterThanOrEqual(110);
  expect(escape, `montage extends ${escape}px past the card's clip`).toBeLessThanOrEqual(1);
});

test("a credit combo's AVG ENTRY reads negative, not positive (2026-08-07)", async ({ page }) => {
  // The IWM fixture opened for a NET CREDIT (entry_cost −579.79 over 50x100),
  // so avg entry is −0.12. The card used Math.abs and showed +0.12, reading as
  // a debit paid.
  await openCockpit(page);
  const summary = page.locator(".ckp-pos-summary");
  await expect(summary).toBeVisible();
  const avgEntry = await summary.locator(".m-metric", { hasText: "Avg Entry" }).textContent();
  expect(avgEntry ?? "").toMatch(/-\$?0\.12/);
  expect(avgEntry ?? "").not.toMatch(/(?<![-])\$0\.12/);
});

test("the structure string renders once — summary owns it, the header chip stays compact", async ({ page }) => {
  await openCockpit(page);
  await expect(page.locator(".ckp-pos-summary")).toContainText(STRUCTURE);
  const chip = page.locator(".ckh-poschip");
  await expect(chip).toBeVisible();
  await expect(chip).not.toContainText("Risk Reversal");
});
