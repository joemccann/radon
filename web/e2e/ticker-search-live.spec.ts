/**
 * E2E: Ticker Search — live browser verification.
 *
 * Validates:
 * 1. Search input exists and accepts typing
 * 2. WS connection is established (or graceful fallback)
 * 3. Typing a ticker shows results dropdown (with mocked WS)
 * 4. Arrow key navigation + Enter selects a result
 * 5. Selection navigates to /{TICKER} page
 */

import { test, expect } from "@playwright/test";

const PORTFOLIO = {
  bankroll: 100_000,
  peak_value: 100_000,
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

const ORDERS = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

function stubApis(page: import("@playwright/test").Page) {
  page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS) }),
  );
  page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: false }) }),
  );
  page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  page.route("**/api/ticker/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        uw_info: { name: "Apple Inc.", sector: "Technology", description: "Consumer electronics" },
        stock_state: {},
        profile: {},
        stats: {},
      }),
    }),
  );
  page.route("**/api/options/expirations*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ symbol: "AAPL", expirations: ["20260320", "20260417"] }),
    }),
  );
  page.route("**/api/options/chain*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        symbol: "AAPL",
        expiry: "20260320",
        exchange: "SMART",
        strikes: [195, 200, 205, 210, 215],
        multiplier: "100",
      }),
    }),
  );
}

test.describe("Ticker Search E2E", () => {
  test("search box is visible, accepts input, and shows dropdown with results", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);

    // Mock WebSocket to inject search results
    await page.addInitScript(() => {
      const fakeResults = [
        { conId: 265598, symbol: "AAPL", secType: "STK", primaryExchange: "NASDAQ", currency: "USD", derivativeSecTypes: ["OPT"] },
        { conId: 100, symbol: "AAPLD", secType: "STK", primaryExchange: "NYSE", currency: "USD", derivativeSecTypes: [] },
      ];

      // @ts-expect-error - intentional override
      window.WebSocket = class FakeWebSocket extends EventTarget {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        CONNECTING = 0;
        OPEN = 1;
        CLOSING = 2;
        CLOSED = 3;
        readyState = 0;
        url: string;
        onopen: ((ev: Event) => void) | null = null;
        onmessage: ((ev: MessageEvent) => void) | null = null;
        onclose: ((ev: CloseEvent) => void) | null = null;
        onerror: ((ev: Event) => void) | null = null;
        binaryType = "blob";
        bufferedAmount = 0;
        extensions = "";
        protocol = "";

        constructor(url: string) {
          super();
          this.url = url;
          setTimeout(() => {
            this.readyState = 1;
            if (this.onopen) this.onopen(new Event("open"));
          }, 50);
        }

        send(data: string) {
          try {
            const msg = JSON.parse(data);
            if (msg.action === "search") {
              setTimeout(() => {
                const response = JSON.stringify({
                  type: "searchResults",
                  pattern: msg.pattern,
                  results: fakeResults,
                });
                if (this.onmessage) {
                  this.onmessage(new MessageEvent("message", { data: response }));
                }
              }, 50);
            } else if (msg.action === "subscribe") {
              setTimeout(() => {
                const status = JSON.stringify({
                  type: "status",
                  ib_connected: false,
                  subscriptions: [],
                });
                if (this.onmessage) {
                  this.onmessage(new MessageEvent("message", { data: status }));
                }
              }, 50);
            }
          } catch {}
        }

        close() {
          this.readyState = 3;
        }
      };
    });

    await page.goto("/portfolio", { waitUntil: "domcontentloaded" });

    // 1. Search input exists
    const searchInput = page.locator('input[role="combobox"]');
    await expect(searchInput).toBeVisible();

    // 2. Type "AAPL"
    await searchInput.fill("AAPL");
    // Wait for debounce + fake WS response
    await page.waitForTimeout(500);

    // 3. Dropdown should show results
    const dropdown = page.locator('#ticker-search-listbox');
    await expect(dropdown).toBeVisible();

    // Should show AAPL result (filtered to STK only)
    const resultItems = dropdown.locator('[role="option"]');
    await expect(resultItems).toHaveCount(2);

    // First result should be AAPL
    await expect(resultItems.first()).toContainText("AAPL");
  });

  test("arrow keys navigate results and Enter selects — navigates to ticker page", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);

    await page.addInitScript(() => {
      const fakeResults = [
        { conId: 265598, symbol: "AAPL", secType: "STK", primaryExchange: "NASDAQ", currency: "USD", derivativeSecTypes: ["OPT"] },
        { conId: 100, symbol: "AMZN", secType: "STK", primaryExchange: "NASDAQ", currency: "USD", derivativeSecTypes: [] },
      ];

      // @ts-expect-error - intentional override
      window.WebSocket = class FakeWebSocket extends EventTarget {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        CONNECTING = 0;
        OPEN = 1;
        CLOSING = 2;
        CLOSED = 3;
        readyState = 0;
        url: string;
        onopen: ((ev: Event) => void) | null = null;
        onmessage: ((ev: MessageEvent) => void) | null = null;
        onclose: ((ev: CloseEvent) => void) | null = null;
        onerror: ((ev: Event) => void) | null = null;
        binaryType = "blob";
        bufferedAmount = 0;
        extensions = "";
        protocol = "";

        constructor(url: string) {
          super();
          this.url = url;
          setTimeout(() => {
            this.readyState = 1;
            if (this.onopen) this.onopen(new Event("open"));
          }, 50);
        }

        send(data: string) {
          try {
            const msg = JSON.parse(data);
            if (msg.action === "search") {
              setTimeout(() => {
                const response = JSON.stringify({
                  type: "searchResults",
                  pattern: msg.pattern,
                  results: fakeResults,
                });
                if (this.onmessage) {
                  this.onmessage(new MessageEvent("message", { data: response }));
                }
              }, 50);
            } else if (msg.action === "subscribe") {
              setTimeout(() => {
                if (this.onmessage) {
                  this.onmessage(new MessageEvent("message", {
                    data: JSON.stringify({ type: "status", ib_connected: false, subscriptions: [] }),
                  }));
                }
              }, 50);
            }
          } catch {}
        }

        close() {
          this.readyState = 3;
        }
      };
    });

    await page.goto("/portfolio", { waitUntil: "domcontentloaded" });

    const searchInput = page.locator('input[role="combobox"]');
    await searchInput.fill("A");
    await page.waitForTimeout(500);

    // Arrow down to first result
    await searchInput.press("ArrowDown");
    const firstItem = page.locator('[role="option"]').first();
    await expect(firstItem).toHaveAttribute("aria-selected", "true");

    // Arrow down to second result
    await searchInput.press("ArrowDown");
    const secondItem = page.locator('[role="option"]').nth(1);
    await expect(secondItem).toHaveAttribute("aria-selected", "true");

    // Enter to select — should navigate to ticker page
    await searchInput.press("Enter");

    // URL should change to /AMZN (second result was selected)
    await page.waitForURL("**/AMZN", { timeout: 5000 });
    expect(page.url()).toContain("/AMZN");
  });

  test("selecting a ticker navigates to ticker page with Company tab", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);

    await page.addInitScript(() => {
      const fakeResults = [
        { conId: 265598, symbol: "AAPL", secType: "STK", primaryExchange: "NASDAQ", currency: "USD", derivativeSecTypes: ["OPT"] },
      ];

      // @ts-expect-error - intentional override
      window.WebSocket = class FakeWebSocket extends EventTarget {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        CONNECTING = 0;
        OPEN = 1;
        CLOSING = 2;
        CLOSED = 3;
        readyState = 0;
        url: string;
        onopen: ((ev: Event) => void) | null = null;
        onmessage: ((ev: MessageEvent) => void) | null = null;
        onclose: ((ev: CloseEvent) => void) | null = null;
        onerror: ((ev: Event) => void) | null = null;
        binaryType = "blob";
        bufferedAmount = 0;
        extensions = "";
        protocol = "";

        constructor(url: string) {
          super();
          this.url = url;
          setTimeout(() => {
            this.readyState = 1;
            if (this.onopen) this.onopen(new Event("open"));
          }, 50);
        }

        send(data: string) {
          try {
            const msg = JSON.parse(data);
            if (msg.action === "search") {
              setTimeout(() => {
                if (this.onmessage) {
                  this.onmessage(new MessageEvent("message", {
                    data: JSON.stringify({ type: "searchResults", pattern: msg.pattern, results: fakeResults }),
                  }));
                }
              }, 50);
            } else if (msg.action === "subscribe") {
              setTimeout(() => {
                if (this.onmessage) {
                  this.onmessage(new MessageEvent("message", {
                    data: JSON.stringify({ type: "status", ib_connected: false, subscriptions: [] }),
                  }));
                }
              }, 50);
            }
          } catch {}
        }

        close() {
          this.readyState = 3;
        }
      };
    });

    await page.goto("/portfolio", { waitUntil: "domcontentloaded" });

    // Search and select AAPL
    const searchInput = page.locator('input[role="combobox"]');
    await searchInput.fill("AAPL");
    await page.waitForTimeout(500);

    // Click the first result
    const firstResult = page.locator('[role="option"]').first();
    await expect(firstResult).toBeVisible();
    await firstResult.click();

    // URL should change to /AAPL
    await page.waitForURL("**/AAPL", { timeout: 5000 });
    expect(page.url()).toContain("/AAPL");

    // The current ticker route is a cockpit workspace; the legacy
    // `.ticker-detail-content` wrapper no longer exists.
    await expect(page.getByTestId("cockpit-head")).toContainText("AAPL", { timeout: 5000 });
  });
});
