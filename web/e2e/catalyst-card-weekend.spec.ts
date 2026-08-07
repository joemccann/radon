import { expect, test, type Page } from "@playwright/test";

/**
 * Catalysts weekend-fossil regression (2026-07-05).
 *
 * The catalysts writer skips weekends/holidays, so on Sunday the snapshot
 * still carries Thursday/Friday rows with days_until=0 baked at scan time.
 * The card must recompute days_until from each row's date at render time:
 *  - past events never render
 *  - future events render their true distance, never a TODAY badge on a
 *    closed market day
 *  - an all-past snapshot renders the existing empty state, not last week's
 *    events badged TODAY.
 *
 * Page clock is pinned to Sunday 2026-07-05 15:00 ET (the day after the
 * observed July-4 holiday weekend started fossilizing the snapshot).
 */

const SUNDAY_ET = new Date("2026-07-05T19:00:00Z");

function catalystRow(date: string, title: string, overrides: Record<string, unknown> = {}) {
  return {
    ticker: null,
    type: "economic",
    title,
    date,
    source: "economic",
    days_until: 0, // frozen at scan time — must be ignored by the card
    ...overrides,
  };
}

async function stubApis(page: Page, catalysts: unknown[]) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;

    if (path === "/api/catalysts") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          scan_time: "2026-07-02T23:07:38Z",
          count: catalysts.length,
          catalysts,
        }),
      });
      return;
    }

    if (path === "/api/scanner") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ scan_time: "2026-07-02T15:00:00Z", tickers_scanned: 0, signals_found: 0, top_signals: [] }),
      });
      return;
    }

    if (path === "/api/discover") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ discovery_time: "2026-07-02T15:00:00Z", alerts_analyzed: 0, candidates_found: 0, candidates: [] }),
      });
      return;
    }

    if (path === "/api/leap" || path === "/api/scanner/theta") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ results: [] }) });
      return;
    }

    if (path === "/api/garch-convergence") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ pairs: [] }) });
      return;
    }

    if (path === "/api/flow-surprise") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ scan_time: null, rows: [] }) });
      return;
    }

    if (path === "/api/portfolio") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ positions: [], account_summary: {}, exposure: {}, violations: [] }),
      });
      return;
    }

    if (path === "/api/orders") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 }),
      });
      return;
    }

    if (path === "/api/service-health") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ services: [] }) });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
}

async function installMockWebSocket(page: Page) {
  await page.addInitScript(() => {
    class MockWebSocket {
      static OPEN = 1;
      readyState = MockWebSocket.OPEN;
      onopen: ((event?: unknown) => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onclose: ((event?: unknown) => void) | null = null;
      onerror: ((event?: unknown) => void) | null = null;

      constructor() {
        setTimeout(() => {
          this.onopen?.({});
          this.onmessage?.({
            data: JSON.stringify({ type: "status", ib_connected: true, ib_issue: null, ib_status_message: null, subscriptions: [] }),
          });
        }, 0);
      }

      send() {}

      close() {
        this.onclose?.({});
      }
    }

    // Only the Radon relay socket is mocked; Turbopack HMR and any other
    // sockets pass through to the real implementation.
    const NativeWebSocket = window.WebSocket;
    const RelayAwareWebSocket = function (url: string | URL, protocols?: string | string[]) {
      return String(url).includes("localhost:8765")
        ? (new MockWebSocket() as unknown as WebSocket)
        : new NativeWebSocket(url, protocols);
    } as unknown as typeof WebSocket;
    Object.assign(RelayAwareWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    window.WebSocket = RelayAwareWebSocket;
  });
}

function catalystCard(page: Page) {
  return page.locator("section.snapshot-card", {
    has: page.getByRole("heading", { name: /upcoming catalysts/i }),
  });
}

test("Sunday: past events absent, future event visible without a TODAY badge", async ({ page }) => {
  await page.clock.install({ time: SUNDAY_ET });
  await installMockWebSocket(page);
  await stubApis(page, [
    catalystRow("2026-07-02", "Initial jobless claims"),
    catalystRow("2026-07-02", "LINDSAY", { ticker: "LNN", type: "earnings", source: "earnings_premarket" }),
    catalystRow("2026-07-03", "Factory orders"),
    catalystRow("2026-07-07", "ISM services"),
  ]);

  await page.goto("/");

  const card = catalystCard(page);
  await expect(card.getByText("ISM services")).toBeVisible();

  await expect(card.getByText("Initial jobless claims")).toHaveCount(0);
  await expect(card.getByText("LINDSAY")).toHaveCount(0);
  await expect(card.getByText("Factory orders")).toHaveCount(0);

  // Tuesday event is 2 ET calendar days out — never TODAY on a closed Sunday.
  await expect(card.getByText("2d", { exact: true })).toBeVisible();
  await expect(card.getByText("Today", { exact: true })).toHaveCount(0);
});

test("Sunday: all-past fossil snapshot renders the empty state", async ({ page }) => {
  await page.clock.install({ time: SUNDAY_ET });
  await installMockWebSocket(page);
  await stubApis(page, [
    catalystRow("2026-07-02", "Initial jobless claims"),
    catalystRow("2026-07-02", "U.S. unemployment rate"),
    catalystRow("2026-07-03", "Factory orders"),
  ]);

  await page.goto("/");

  const card = catalystCard(page);
  await expect(card.getByText(/no upcoming catalysts/i)).toBeVisible();
  await expect(card.getByText("Today", { exact: true })).toHaveCount(0);
});

test("trading day: elapsed exact-time events disappear while later events remain", async ({ page }, testInfo) => {
  await page.clock.install({ time: new Date("2026-08-07T21:59:00Z") });
  await installMockWebSocket(page);
  await stubApis(page, [
    catalystRow("2026-08-07", "U.S. employment report", {
      event_time: "2026-08-07T12:30:00Z",
    }),
    catalystRow("2026-08-07", "Evening Fed speaker", {
      event_time: "2026-08-07T22:30:00Z",
    }),
  ]);

  await page.goto("/");

  const card = catalystCard(page);
  await expect(card.getByText("Evening Fed speaker")).toBeVisible();
  await expect(card.getByText("U.S. employment report")).toHaveCount(0);
  await expect(card.getByText("Today", { exact: true })).toBeVisible();
  await card.screenshot({ path: testInfo.outputPath("catalyst-exact-time.png") });
});
