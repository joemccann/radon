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
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/scanner/vol-skew-mr" || path === "/api/scanner/vol-skew-mr/scan") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
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
    const alert = section.getByRole("alert");
    await expect(alert).toBeVisible();
    await expect(alert).not.toContainText("Subprocess");
    await expect(alert).not.toContainText("scan_succeeded");
    await expect(section).toContainText("TOP MR");
    await page.screenshot({ path: testInfo.outputPath(`vol-skew-safe-error-${width}.png`) });
    await alert.getByRole("button", { name: /retry|try again/i }).click();
    await expect(alert).toHaveCount(0);
    expect(requests).toEqual([{ tickers: ["AAPL", "NVDA"] }, { tickers: ["AAPL", "NVDA"] }]);
  });
}
