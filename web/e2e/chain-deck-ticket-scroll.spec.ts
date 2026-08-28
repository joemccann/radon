/**
 * E2E: the chain deck's order ticket scrolls ITSELF.
 *
 * Repro: open the chain deck, stage legs until the ticket is taller than the
 * deck. The ticket used to be sized off the viewport (`100dvh`) while its
 * scrollport was the much shorter deck body, so it overflowed without ever
 * growing its own scrollbar — the risk block and TRANSMIT CTA were reachable
 * only by scrolling the panel AROUND the form.
 */

import { test, expect } from "@playwright/test";

const TICKER = "MU";

function fridayInDays(days: number): string {
  const d = new Date();
  d.setUTCHours(0, 0, 0, 0);
  d.setUTCDate(d.getUTCDate() + days);
  while (d.getUTCDay() !== 5) d.setUTCDate(d.getUTCDate() + 1);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}${m}${day}`;
}

const NEAR = fridayInDays(14);

const PORTFOLIO_EMPTY = {
  bankroll: 1_000_000,
  peak_value: 1_000_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [],
};

const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

function quote(symbol: string, bid: number, ask: number, last: number) {
  return {
    symbol,
    last,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 25,
    askSize: 25,
    volume: 1_000,
    high: null,
    low: null,
    open: null,
    close: last,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: 0.4,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.45,
    undPrice: 120,
    timestamp: new Date().toISOString(),
  };
}

const STRIKES = [110, 115, 120, 125, 130];

const PRICE_FIXTURES: Record<string, unknown> = { [TICKER]: quote(TICKER, 119.9, 120.1, 120) };
for (const strike of STRIKES) {
  for (const right of ["C", "P"] as const) {
    const key = `${TICKER}_${NEAR}_${strike}_${right}`;
    PRICE_FIXTURES[key] = quote(key, 3.1, 3.5, 3.3);
  }
}

function installMockWebSocket(page: import("@playwright/test").Page) {
  return page.addInitScript((priceFixtures) => {
    const NativeWebSocket = window.WebSocket;
    const isRelay = (url: string) => url.includes(":8765") || url.includes("/ws");

    class MockWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;
      url: string;
      readyState = MockWebSocket.CONNECTING;
      onopen: ((event?: unknown) => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onclose: ((event?: unknown) => void) | null = null;
      onerror: ((event?: unknown) => void) | null = null;
      constructor(url: string) {
        this.url = url;
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.({});
          this.emit({
            type: "status",
            ib_connected: true,
            ib_issue: null,
            ib_status_message: null,
            subscriptions: [],
          });
        }, 0);
      }
      addEventListener(type: string, listener: (event: unknown) => void) {
        if (type === "open") this.onopen = listener;
        if (type === "message") this.onmessage = listener as (e: { data: string }) => void;
        if (type === "close") this.onclose = listener;
        if (type === "error") this.onerror = listener;
      }
      removeEventListener() {}
      send(raw: string) {
        const message = JSON.parse(raw) as {
          action?: string;
          symbols?: string[];
          contracts?: Array<{ symbol: string; expiry: string; strike: number; right: "C" | "P" }>;
        };
        if (message.action !== "subscribe") return;
        const updates: Record<string, unknown> = {};
        for (const symbol of message.symbols ?? []) {
          if (priceFixtures[symbol]) updates[symbol] = priceFixtures[symbol];
        }
        for (const contract of message.contracts ?? []) {
          const expiry = String(contract.expiry).replace(/-/g, "");
          const key = `${String(contract.symbol).toUpperCase()}_${expiry}_${Number(contract.strike)}_${contract.right}`;
          if (priceFixtures[key]) updates[key] = priceFixtures[key];
        }
        if (Object.keys(updates).length > 0) this.emit({ type: "batch", updates });
      }
      close() {
        this.readyState = MockWebSocket.CLOSED;
        this.onclose?.({});
      }
      emit(payload: unknown) {
        this.onmessage?.({ data: JSON.stringify(payload) });
      }
    }
    // @ts-expect-error test-only replacement
    window.WebSocket = function (url: string, protocols?: string | string[]) {
      // @ts-expect-error test-only replacement
      return isRelay(String(url)) ? new MockWebSocket(String(url)) : new NativeWebSocket(url, protocols);
    };
    Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  }, PRICE_FIXTURES);
}

async function stubApis(page: import("@playwright/test").Page) {
  const json = (body: unknown) => ({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  await page.route("**/api/portfolio", (route) => route.fulfill(json(PORTFOLIO_EMPTY)));
  await page.route("**/api/orders", (route) => route.fulfill(json(ORDERS_EMPTY)));
  await page.route("**/api/regime", (route) => route.fulfill(json({ score: 15, cri: { score: 15 } })));
  await page.route("**/api/ib-status", (route) => route.fulfill(json({ connected: true })));
  await page.route("**/api/blotter", (route) =>
    route.fulfill(
      json({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    ),
  );
  await page.route("**/api/ticker/**", (route) =>
    route.fulfill(json({ uw_info: { name: "Micron Technology", sector: "Technology" }, stock_state: {}, profile: {}, stats: {} })),
  );
  await page.route("**/api/options/expirations*", (route) =>
    route.fulfill(json({ symbol: TICKER, expirations: [NEAR] })),
  );
  await page.route("**/api/options/chain*", (route) => {
    const expiry = new URL(route.request().url()).searchParams.get("expiry") ?? NEAR;
    return route.fulfill(
      json({ symbol: TICKER, expiry, exchange: "SMART", strikes: STRIKES, multiplier: "100" }),
    );
  });
}

test.describe("Chain deck ticket scroll", () => {
  test.use({ viewport: { width: 1440, height: 600 } });

  test("keeps the ticket inside the deck and scrolls it internally", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await installMockWebSocket(page);
    await stubApis(page);

    await page.goto(`/${TICKER}?deck=c`);

    const deckBody = page.locator(".asset-deck.open .asset-deck-body");
    await deckBody.waitFor({ timeout: 20_000 });
    await deckBody.locator(".chain-grid").waitFor();

    // Toolbar rides in the chain column so the ticket starts level with it.
    await expect(deckBody.locator(".chain-rail-main > .chain-expiry-bar")).toHaveCount(1);

    for (const strike of [110, 115, 120, 125, 130]) {
      const row = deckBody.getByRole("row", { name: new RegExp(`\\$${strike}\\.00`) }).first();
      await row.locator(".chain-mid.chain-clickable").first().click();
    }

    const rail = deckBody.locator(".order-builder--rail");
    await expect(rail).toBeVisible();

    const box = await deckBody.evaluate((body) => {
      const ticket = body.querySelector(".order-builder--rail") as HTMLElement;
      return {
        bodyScrolls: body.scrollHeight > body.clientHeight + 1,
        ticketOverflows: ticket.scrollHeight > ticket.clientHeight + 1,
        ticketBottom: Math.round(ticket.getBoundingClientRect().bottom),
        bodyBottom: Math.round(body.getBoundingClientRect().bottom),
      };
    });

    // The panel around the form never scrolls…
    expect(box.bodyScrolls).toBe(false);
    // …the ticket does, and is fully inside the deck.
    expect(box.ticketOverflows).toBe(true);
    expect(box.ticketBottom).toBeLessThanOrEqual(box.bodyBottom + 1);

    // Scrolling the ticket alone reaches the transmit CTA.
    await rail.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });
    const submitVisible = await rail.evaluate((el) => {
      const submit = el.querySelector(".order-builder-submit") as HTMLElement | null;
      if (!submit) return false;
      const s = submit.getBoundingClientRect();
      const r = el.getBoundingClientRect();
      return s.bottom <= r.bottom + 1 && s.top >= r.top - 1;
    });
    expect(submitVisible).toBe(true);

    await page.screenshot({ path: "test-results/chain-deck-ticket-scroll.png", fullPage: false });

    // Roomy window: the same layout with the ticket no longer needing to scroll.
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(rail).toBeVisible();
    await page.screenshot({ path: "test-results/chain-deck-roomy.png", fullPage: false });
  });
});
