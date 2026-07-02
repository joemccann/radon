import { expect, test, type Page } from "@playwright/test";

const MIN_TAB_GAP_PX = 4;
const GAP_CONSISTENCY_TOLERANCE_PX = 1;

async function stubApis(page: Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;

    if (path === "/api/scanner") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ scan_time: "2026-07-01T15:00:00Z", tickers_scanned: 0, signals_found: 0, top_signals: [] }),
      });
      return;
    }

    if (path === "/api/discover") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ discovery_time: "2026-07-01T15:00:00Z", alerts_analyzed: 0, candidates_found: 0, candidates: [] }),
      });
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

async function measureNeighborGaps(page: Page): Promise<number[]> {
  return page.evaluate(() => {
    const tabs = Array.from(document.querySelectorAll<HTMLElement>(".scanner-mode-tabs .scanner-mode-tab"));
    const boxes = tabs.map((tab) => tab.getBoundingClientRect());
    const gaps: number[] = [];
    for (let i = 0; i < boxes.length - 1; i += 1) {
      gaps.push(boxes[i + 1].left - boxes[i].right);
    }
    return gaps;
  });
}

test("scanner mode tabs keep consistent >=4px gaps regardless of which tab is active", async ({ page }) => {
  await stubApis(page);
  await installMockWebSocket(page);

  await page.goto("/scanner");

  const tablist = page.locator(".scanner-mode-tabs");
  await tablist.waitFor();
  const tabs = tablist.getByRole("tab");
  const tabCount = await tabs.count();
  expect(tabCount).toBeGreaterThanOrEqual(2);

  const gapsByActiveTab: number[][] = [];
  for (let active = 0; active < tabCount; active += 1) {
    await tabs.nth(active).click();
    await expect(tabs.nth(active)).toHaveAttribute("aria-selected", "true");
    gapsByActiveTab.push(await measureNeighborGaps(page));
  }

  for (let active = 0; active < tabCount; active += 1) {
    for (const [pair, gap] of gapsByActiveTab[active].entries()) {
      expect
        .soft(gap, `gap after tab ${pair} with tab ${active} active should be >= ${MIN_TAB_GAP_PX}px`)
        .toBeGreaterThanOrEqual(MIN_TAB_GAP_PX);
    }
  }

  const baseline = gapsByActiveTab[0];
  for (let active = 1; active < tabCount; active += 1) {
    for (const [pair, gap] of gapsByActiveTab[active].entries()) {
      expect
        .soft(Math.abs(gap - baseline[pair]), `gap after tab ${pair} drifts between tab 0 active and tab ${active} active`)
        .toBeLessThanOrEqual(GAP_CONSISTENCY_TOLERANCE_PX);
    }
  }
});
