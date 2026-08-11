import { expect, test, type Page } from "@playwright/test";

const thetaPayload = {
  scan_time: "2026-06-24T15:00:00Z",
  source: "Unusual Whales",
  universe: "fallback:ndx100",
  requested_tickers: ["AAPL", "MSFT"],
  tickers_scanned: 2,
  candidates_found: 1,
  theta_harvest_count: 1,
  results: [
    {
      ticker: "AAPL",
      score: 87.5,
      verdict: "THETA_HARVEST",
      spot: 100,
      iv: 35,
      hv20: 12,
      hv60: 15,
      iv_rv_edge: 23,
      iv_rv_ratio: 2.92,
      trend_20d_pct: 0.2,
      range_score: 0.86,
      dealer_support: "SUPPORT",
      net_gex: 1_500_000,
      gex_flip: 95,
      setup: "TRUE_THETA",
      gates: {
        delta_near_zero: true,
        iv_rich_vs_rv: true,
        dealer_support: true,
        theta_positive: true,
        range_bound: true,
      },
      errors: [],
      structure: {
        expiry: "20260717",
        dte: 23,
        net_delta: -0.01,
        theta: 0.075,
        gamma: -0.0042,
        vega: -0.038,
        credit: 1.9,
        short_put: {
          symbol: "AAPL260717P00095000",
          expiry: "20260717",
          strike: 95,
          right: "P",
          iv: 35,
          delta: -0.15,
          theta: -0.04,
          gamma: 0.002,
          vega: 0.018,
          bid: 0.9,
          ask: 1.1,
          volume: 200,
          open_interest: 900,
        },
        short_call: {
          symbol: "AAPL260717C00105000",
          expiry: "20260717",
          strike: 105,
          right: "C",
          iv: 35,
          delta: 0.16,
          theta: -0.035,
          gamma: 0.0022,
          vega: 0.02,
          bid: 0.8,
          ask: 1.0,
          volume: 180,
          open_interest: 850,
        },
      },
    },
  ],
};

async function stubApis(page: Page, scanBodies: unknown[] = []) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/api/scanner/theta/scan") {
      scanBodies.push(request.postDataJSON());
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(thetaPayload) });
      return;
    }

    if (path === "/api/scanner/theta") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(thetaPayload) });
      return;
    }

    if (path === "/api/scanner") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ scan_time: "2026-06-24T15:00:00Z", tickers_scanned: 0, signals_found: 0, top_signals: [] }),
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

    if (path === "/api/profile") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ username: "Operator" }) });
      return;
    }

    if (path === "/api/watchlist") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ symbols: [] }) });
      return;
    }

    if (path === "/api/flex-token") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ remaining: 240 }) });
      return;
    }

    if (path === "/api/prices" || path === "/api/ib/ws-ticket") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ prices: {}, ticket: "test" }) });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

test.describe("theta harvester scanner", () => {
  test("desktop renders the theta mode table and scan action", async ({ page }, testInfo) => {
    const scanBodies: unknown[] = [];
    await stubApis(page, scanBodies);

    await page.goto("/scanner?mode=theta");

    const section = page.getByTestId("theta-harvester-section");
    await expect(section).toBeVisible();
    await expect(section).toContainText("Theta Harvester");
    await expect(section.getByTestId("theta-grid")).toBeVisible();
    await expect(section).toContainText("AAPL");
    await expect(section).toContainText("SHORT 95P / 105C");
    await expect(section).toContainText("TRUE THETA");
    await expect(section).toContainText("+23.0 pt");
    await expect(section).toContainText("2.92×");

    const headerGeometry = await section.locator(".instrument-section__header").evaluate((header) => {
      const heading = header.querySelector<HTMLElement>(".instrument-section__heading");
      const controls = header.querySelector<HTMLElement>(".instrument-section__controls");
      if (!heading || !controls) throw new Error("Theta scanner header regions are missing");
      const headerRect = header.getBoundingClientRect();
      const headingRect = heading.getBoundingClientRect();
      const controlsRect = controls.getBoundingClientRect();
      return {
        headingShare: headingRect.width / headerRect.width,
        controlsRightGap: headerRect.right - controlsRect.right,
      };
    });
    expect(headerGeometry.headingShare).toBeLessThan(0.25);
    expect(headerGeometry.controlsRightGap).toBeLessThanOrEqual(17);

    await page.getByRole("button", { name: /scan ndx/i }).click();
    await expect.poll(() => scanBodies.length).toBe(1);
    expect(scanBodies[0]).toEqual({ preset: "ndx100", min_dte: 7, max_dte: 45, min_credit: 0 });

    await page.getByLabel("Ticker symbol").fill("mu");
    await page.getByRole("button", { name: /^scan$/i }).click();
    await expect.poll(() => scanBodies.length).toBe(2);
    expect(scanBodies[1]).toEqual({ ticker: "MU" });

    await page.screenshot({ path: testInfo.outputPath("theta-harvester-aligned.png"), fullPage: true });
  });

  test("mobile renders theta cards without horizontal overflow", async ({ page }) => {
    await page.setViewportSize({ width: 393, height: 852 });
    await stubApis(page);

    await page.goto("/scanner?mode=theta");

    await expect(page.getByTestId("mobile-tab-bar")).toBeVisible();
    const section = page.getByTestId("theta-harvester-section");
    await expect(section).toBeVisible();
    await expect(page.getByTestId("theta-harvester-mobile-list")).toBeVisible();
    await expect(page.getByLabel("Ticker symbol")).toBeVisible();
    await expect(section.locator(".theta-card").first()).toContainText("AAPL");
    await expect(section.locator(".theta-card").first()).toContainText("TRUE THETA");

    const geometry = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      tableDisplay: window.getComputedStyle(document.querySelector(".theta-harvester__table-wrap")!).display,
      cardCount: document.querySelectorAll(".theta-card").length,
    }));
    expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1);
    expect(geometry.tableDisplay).toBe("none");
    expect(geometry.cardCount).toBe(1);
  });
});
