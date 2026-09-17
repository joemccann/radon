import { expect, test, type Page } from "@playwright/test";

const payload = {
  scan_time: "2026-09-16T15:00:00Z",
  source: "Unusual Whales + Radon vol/skew feeds",
  universe: "fallback:ndx100",
  requested_tickers: ["AAPL", "INTC", "NVDA"],
  tickers_scanned: 3,
  candidates_found: 3,
  actionable_count: 2,
  results: [
    {
      ticker: "AAPL",
      verdict: "TOP_MR",
      spot: 212.4,
      rsi: 78,
      pct_b: 1.04,
      extension: "HIGH",
      iv_path: "falling",
      skew_path: "falling",
      suggested_structure: "put spread",
      gates: { technicals: true, iv: true, skew: true },
      errors: [],
    },
    {
      ticker: "INTC",
      verdict: "BOTTOM_MR",
      spot: 22.1,
      rsi: 24,
      pct_b: -0.05,
      extension: "LOW",
      iv_path: "flat",
      skew_path: "falling",
      suggested_structure: "call spread",
      gates: { technicals: true, iv: true, skew: true },
      errors: [],
    },
    {
      ticker: "NVDA",
      verdict: "BREAKOUT",
      spot: 181.4,
      rsi: 74,
      pct_b: 1.1,
      extension: "HIGH",
      iv_path: "rising",
      skew_path: "rising",
      suggested_structure: null,
      gates: { technicals: true, iv: true, skew: false },
      errors: [],
    },
  ],
};

async function stubApis(page: Page) {
  await page.routeWebSocket(/(?:localhost|127\.0\.0\.1):(?:18765|8765)|\/ws(?:\?|$)/, socket => {
    socket.onMessage(() => socket.send(JSON.stringify({ type: "status", ib_connected: true, subscriptions: [] })));
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/scanner/vol-skew-mr" || path === "/api/scanner/vol-skew-mr/scan") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
      return;
    }
    if (path === "/api/ib/ws-ticket") {
      await route.fulfill({ json: { ticket: "isolated-scanner-ticket" } });
      return;
    }
    if (path === "/api/scanner") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ scan_time: "2026-09-16T15:00:00Z", tickers_scanned: 0, signals_found: 0, top_signals: [] }),
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
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

test.describe("/scanner?mode=vol-skew-mr", () => {
  test("mode tab and table show TOP_MR, BOTTOM_MR, and continue labels", async ({ page }) => {
    await stubApis(page);
    await page.goto("/scanner?mode=vol-skew-mr");

    const tab = page.getByRole("tab", { name: "Vol/Skew MR" });
    await expect(tab).toHaveAttribute("aria-selected", "true");

    const section = page.getByTestId("vol-skew-mr-section");
    await expect(section).toBeVisible();
    await expect(section).toContainText("Vol/Skew MR");
    await expect(section).toContainText("AAPL");
    await expect(section).toContainText("TOP MR");
    await expect(section).toContainText("BOTTOM MR");
    await expect(section).toContainText("BREAKOUT");
    await expect(section).toContainText("put spread");
    await expect(section.getByTestId("vol-skew-mr-title-tooltip")).toBeVisible();

    const chainLink = section.getByTestId("vol-skew-mr-order-link-AAPL").first();
    await expect(chainLink).toHaveAttribute("href", "/AAPL?deck=c&src=vol-skew-mr");
  });
});

test("desktop header bubbles explain readings without sorting the table", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await stubApis(page);
  await page.goto("/scanner?mode=vol-skew-mr");
  const section = page.getByTestId("vol-skew-mr-section");
  const tickerHeader = section.getByRole("columnheader", { name: "Ticker" });
  await tickerHeader.click();
  await tickerHeader.click();
  const rows = section.locator("tbody tr");
  await expect(rows.first()).toHaveAttribute("data-testid", "vol-skew-mr-row-NVDA");
  const rowOrder = await rows.evaluateAll(elements => elements.map(element => element.getAttribute("data-testid")));

  for (const [label, id, explanation] of [
    ["Spot ext", "extension", /RSI/i],
    ["IV path", "iv-path", /implied volatility/i],
    ["Skew path", "skew-path", /put/i],
    ["Verdict", "verdict", /TOP MR/i],
    ["Structure", "structure", /spread/i],
  ] as const) {
    const trigger = section.getByTestId(`vol-skew-mr-${id}-tooltip`);
    const content = section.getByTestId(`vol-skew-mr-${id}-tooltip-content`);
    await expect(trigger).toHaveAttribute("aria-label", `${label} details`);
    await trigger.focus();
    await expect(content).toBeVisible();
    await expect(content).toContainText(explanation);
    await trigger.press("Enter");
    await expect(tickerHeader).toHaveAttribute("aria-sort", "descending");
    await trigger.getByRole("button").click();
    await expect(content).toHaveCount(0);
    await trigger.focus();
    await expect(content).toBeVisible();
    expect(await rows.evaluateAll(elements => elements.map(element => element.getAttribute("data-testid")))).toEqual(rowOrder);
    await expect(tickerHeader).toHaveAttribute("aria-sort", "descending");
    if (id === "skew-path") {
      await testInfo.attach("vol-skew-header-info-desktop", {
        body: await page.screenshot({ path: testInfo.outputPath("vol-skew-header-info-desktop.png") }),
        contentType: "image/png",
      });
    }
    await tickerHeader.focus();
    await page.mouse.move(0, 0);
    await expect(content).toHaveCount(0);
  }

  const ivHeader = section.getByRole("columnheader", { name: /^IV path/ });
  await ivHeader.getByText("IV path", { exact: true }).click();
  await expect(ivHeader).toHaveAttribute("aria-sort", "ascending");
  await expect(rows.first()).toHaveAttribute("data-testid", "vol-skew-mr-row-AAPL");
  await ivHeader.focus();
  await ivHeader.press("Enter");
  await expect(ivHeader).toHaveAttribute("aria-sort", "descending");
  await expect(rows.first()).toHaveAttribute("data-testid", "vol-skew-mr-row-NVDA");
});

for (const width of [390, 1440]) {
  test(`missing skew is explained at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await stubApis(page);
    await page.route("**/api/scanner/vol-skew-mr", route => route.fulfill({ json: { ...payload, results: [{ ...payload.results[0], skew_path: "unknown", suggested_structure: null, gates: { technicals: true, iv: true, skew: false } }] } }));
    await page.goto("/scanner?mode=vol-skew-mr");
    const section = page.getByTestId("vol-skew-mr-section");
    await expect(section.getByRole("status")).toContainText("Skew history unavailable for 1 of 1 names");
    await expect(section.getByText("Insufficient history", { exact: true }).or(section.getByText("IV falling · SKEW Insufficient history")).filter({ visible: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`vol-skew-missing-${width}.png`) });
  });
}

for (const width of [390, 1440]) {
  test(`failed scan retains snapshot and retries explicit tickers at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await stubApis(page);
    const requests: unknown[] = [];
    await page.route("**/api/scanner/vol-skew-mr/scan", async route => {
      expect(route.request().url()).toBe(new URL("/api/scanner/vol-skew-mr/scan", page.url()).href);
      expect(route.request().method()).toBe("POST");
      requests.push(route.request().postDataJSON());
      await route.fulfill(requests.length === 1
        ? { status: 502, json: { scan_time: "", scan_succeeded: false, results: [], error: "Radon API 502: Subprocess capacity exhausted" } }
        : { status: 200, json: payload });
    });
    await page.goto("/scanner?mode=vol-skew-mr");
    const section = page.getByTestId("vol-skew-mr-section");
    const input = section.getByRole("textbox");
    await input.fill("AAPL, NVDA");
    await input.press("Enter");
    const alert = page.locator(".toast-container").getByRole("alert").filter({ hasText: "This service is busy" });
    await expect(section.getByRole("alert")).toHaveCount(0);
    await expect(alert).toBeVisible();
    await expect(alert).toContainText("This service is busy");
    await expect(alert).toContainText("Showing the last available data");
    await expect(alert).not.toContainText("Subprocess");
    await expect(alert).not.toContainText("scan_succeeded");
    await expect(section).toContainText("TOP MR");
    await expect(alert).toHaveCSS("opacity", "1");
    await page.screenshot({ path: testInfo.outputPath(`vol-skew-safe-error-${width}.png`), animations: "disabled" });
    await alert.getByRole("button", { name: /retry|try again/i }).click();
    await expect(alert).toHaveCount(0);
    expect(requests).toEqual([{ tickers: ["AAPL", "NVDA"] }, { tickers: ["AAPL", "NVDA"] }]);
  });
}
