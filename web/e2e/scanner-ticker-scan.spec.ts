import { expect, test, type Page } from "@playwright/test";

/**
 * Scan-by-ticker E2E for the LEAP + GARCH scanner tabs: type "NVDA, AMD",
 * trigger the scan (POST stubbed), assert the forwarded payload carries
 * ["NVDA", "AMD"] and the table renders the stubbed results returned by
 * the GET cache re-read (syncNow after the scan completes).
 */

const LEAP_EMPTY = { scan_time: "", min_gap: null, results: [] };

const LEAP_SCANNED = {
  scan_time: "2026-07-05T15:00:00Z",
  min_gap: 10,
  universe: "explicit",
  requested_tickers: ["NVDA", "AMD"],
  results: [
    {
      ticker: "NVDA",
      price: 181.4,
      hv_20: 42.1,
      hv_60: 38.7,
      hv_252: 44.9,
      current_iv: 31.2,
      iv_rank: 12.5,
      leap_count: 8,
      best_gap: 13.7,
      is_mispriced: true,
    },
    {
      ticker: "AMD",
      price: 140.2,
      hv_20: 38.3,
      hv_60: 36.1,
      hv_252: 41.4,
      current_iv: 35.9,
      iv_rank: 24.0,
      leap_count: 5,
      best_gap: 2.4,
      is_mispriced: false,
    },
  ],
};

const GARCH_EMPTY = { scan_time: "", tickers: {}, pairs: [] };

const GARCH_SCANNED = {
  scan_time: "2026-07-05T15:05:00Z",
  universe: "explicit",
  requested_tickers: ["NVDA", "AMD"],
  tickers: {},
  pairs: [
    {
      pair: ["NVDA", "AMD"],
      leader: "NVDA",
      lagger: "AMD",
      divergence: 2.41,
      lagger_hv_iv_gap: 9.8,
      lagger_iv_rank: 15.0,
      signal: "STRONG",
      gates_passed: true,
      failing_gates: [],
      expected_iv: 47.2,
      expected_move: 6.1,
    },
  ],
};

type ScanCapture = { body: unknown | null };

async function stubApis(
  page: Page,
  state: { leapScanned: boolean; garchScanned: boolean },
  captures: { leap: ScanCapture; garch: ScanCapture },
) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;

    if (path === "/api/leap/scan" && request.method() === "POST") {
      captures.leap.body = request.postDataJSON();
      state.leapScanned = true;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(LEAP_SCANNED) });
      return;
    }

    if (path === "/api/leap") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(state.leapScanned ? LEAP_SCANNED : LEAP_EMPTY),
      });
      return;
    }

    if (path === "/api/garch-convergence/scan" && request.method() === "POST") {
      captures.garch.body = request.postDataJSON();
      state.garchScanned = true;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(GARCH_SCANNED) });
      return;
    }

    if (path === "/api/garch-convergence") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(state.garchScanned ? GARCH_SCANNED : GARCH_EMPTY),
      });
      return;
    }

    if (path === "/api/scanner") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ scan_time: "2026-07-05T15:00:00Z", tickers_scanned: 0, signals_found: 0, top_signals: [] }),
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

function freshState() {
  return {
    state: { leapScanned: false, garchScanned: false },
    captures: { leap: { body: null } as ScanCapture, garch: { body: null } as ScanCapture },
  };
}

test("LEAP tab scan-by-ticker posts the parsed list and renders the results", async ({ page }) => {
  const { state, captures } = freshState();
  await stubApis(page, state, captures);
  await installMockWebSocket(page);

  await page.goto("/scanner?mode=leap");

  const section = page.getByTestId("leap-scanner-section");
  await expect(section.getByText("No LEAP scan on file")).toBeVisible();

  await section.getByLabel("Ticker symbols").fill("NVDA, AMD");
  await section.getByRole("button", { name: "Scan", exact: true }).click();

  await expect(section.getByRole("link", { name: "NVDA" })).toBeVisible();
  await expect(section.getByText("MISPRICED", { exact: true })).toBeVisible();
  expect(captures.leap.body).toEqual({ tickers: ["NVDA", "AMD"] });
});

test("GARCH tab scan-by-ticker posts the parsed pair and renders the results", async ({ page }) => {
  const { state, captures } = freshState();
  await stubApis(page, state, captures);
  await installMockWebSocket(page);

  await page.goto("/scanner?mode=garch");

  const section = page.getByTestId("garch-scanner-section");
  await expect(section.getByText("No GARCH scan on file")).toBeVisible();

  await section.getByLabel("Ticker symbols").fill("NVDA, AMD");
  await section.getByRole("button", { name: "Scan", exact: true }).click();

  await expect(section.getByText("NVDA ↔ AMD")).toBeVisible();
  await expect(section.getByText("STRONG")).toBeVisible();
  expect(captures.garch.body).toEqual({ tickers: ["NVDA", "AMD"] });
});

test("GARCH tab rejects an odd ticker count inline without posting", async ({ page }) => {
  const { state, captures } = freshState();
  await stubApis(page, state, captures);
  await installMockWebSocket(page);

  await page.goto("/scanner?mode=garch");

  const section = page.getByTestId("garch-scanner-section");
  await expect(section.getByText("No GARCH scan on file")).toBeVisible();

  await section.getByLabel("Ticker symbols").fill("NVDA, AMD, TSM");
  await section.getByRole("button", { name: "Scan", exact: true }).click();

  await expect(section.getByRole("alert")).toHaveText("Enter pairs: an even number of tickers.");
  expect(captures.garch.body).toBeNull();
});
