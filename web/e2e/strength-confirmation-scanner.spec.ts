import { expect, test, type Page } from "@playwright/test";

const groups = [
  "Q-SCORES",
  "NET GEX",
  "CALL POSITIONING",
  "TERM STRUCTURE",
  "VOLATILITY SMILE",
  "SYSTEMATIC POSITIONING",
  "MARKET BREADTH",
];

function factor(group: string, passed = true, source: "UW" | "APPROX" = "UW") {
  return {
    group,
    passed,
    checks_passed: passed ? 3 : 2,
    checks_total: 3,
    source,
    notes: source === "APPROX" ? ["Approximation"] : [],
    checks: [
      { label: `${group} check`, passed, value: passed ? 1 : 0, threshold: "pass", note: "Measured", source },
    ],
  };
}

const strengthPayload = {
  scan_time: "2026-06-24T15:00:00Z",
  source: "Unusual Whales + Radon regime caches",
  universe: "fallback:ndx100",
  requested_tickers: ["AAPL", "MSFT"],
  tickers_scanned: 2,
  candidates_found: 2,
  confirmed_strength_count: 1,
  results: [
    {
      ticker: "AAPL",
      verdict: "REAL_STRENGTH_CONFIRMED",
      score: 100,
      groups_passed: 7,
      spot: 212.4,
      factors: groups.map((group) => factor(group, true, group === "SYSTEMATIC POSITIONING" ? "APPROX" : "UW")),
      errors: [],
    },
    {
      ticker: "MSFT",
      verdict: "WATCHLIST",
      score: 86,
      groups_passed: 6,
      spot: 486.1,
      factors: groups.map((group) => factor(group, group !== "TERM STRUCTURE", group === "SYSTEMATIC POSITIONING" ? "APPROX" : "UW")),
      errors: [],
    },
  ],
};

async function stubApis(page: Page, scanBodies: unknown[] = []) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/api/scanner/strength/scan") {
      scanBodies.push(request.postDataJSON());
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(strengthPayload) });
      return;
    }

    if (path === "/api/scanner/strength") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(strengthPayload) });
      return;
    }

    if (path === "/api/scanner/theta") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ scan_time: "2026-06-24T15:00:00Z", tickers_scanned: 0, candidates_found: 0, theta_harvest_count: 0, results: [] }),
      });
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

test.describe("7-step strength scanner", () => {
  test("desktop renders the strength mode table and scan action", async ({ page }) => {
    const scanBodies: unknown[] = [];
    await stubApis(page, scanBodies);

    await page.goto("/scanner?mode=strength");

    const section = page.getByTestId("strength-confirmation-section");
    await expect(section).toBeVisible();
    await expect(section).toContainText("7-Step Strength");
    await expect(section.locator("table")).toBeVisible();
    await expect(section).toContainText("AAPL");
    await expect(section).toContainText("REAL STRENGTH");
    await expect(section).toContainText("7/7");
    await expect(section).toContainText("CONFIRMED");

    for (const key of ["q", "gex", "call", "term", "smile", "sys", "breadth"]) {
      await expect(section.getByTestId(`strength-factor-tooltip-${key}`)).toBeVisible();
    }
    await section.getByTestId("strength-factor-tooltip-gex").hover();
    await expect(section.getByTestId("strength-factor-tooltip-content-gex")).toContainText("Dealer gamma gate");

    await page.getByRole("button", { name: /scan ndx/i }).click();
    await expect.poll(() => scanBodies.length).toBe(1);
    expect(scanBodies[0]).toEqual({ preset: "ndx100" });

    await page.getByLabel("Strength ticker symbol").fill("mu");
    await page.getByRole("button", { name: /^scan$/i }).click();
    await expect.poll(() => scanBodies.length).toBe(2);
    expect(scanBodies[1]).toEqual({ ticker: "MU" });
  });

  test("mobile renders strength cards without horizontal overflow", async ({ page }) => {
    await page.setViewportSize({ width: 393, height: 852 });
    await stubApis(page);

    await page.goto("/scanner?mode=strength");

    await expect(page.getByTestId("mobile-tab-bar")).toBeVisible();
    const section = page.getByTestId("strength-confirmation-section");
    await expect(section).toBeVisible();
    await expect(page.getByTestId("strength-confirmation-mobile-list")).toBeVisible();
    await expect(page.getByLabel("Strength ticker symbol")).toBeVisible();
    await expect(section.locator(".strength-card").first()).toContainText("AAPL");
    await expect(section.locator(".strength-card").first()).toContainText("REAL STRENGTH");

    const geometry = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      tableDisplay: window.getComputedStyle(document.querySelector(".strength-confirmation__table-wrap")!).display,
      cardCount: document.querySelectorAll(".strength-card").length,
    }));
    expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1);
    expect(geometry.tableDisplay).toBe("none");
    expect(geometry.cardCount).toBe(2);
  });
});
