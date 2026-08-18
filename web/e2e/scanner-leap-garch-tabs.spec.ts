import { expect, test } from "@playwright/test";

// The scanner page's mode tabs must include LEAP and GARCH, and each mode
// must render its full panel (candidates from /api/leap and
// /api/garch-convergence — Turso-first since migration 0026). Data payloads
// are stubbed so the spec pins the UI contract, not scan freshness.

const leapPayload = {
  scan_time: "2026-07-02T14:00:00Z",
  min_gap: 5,
  results: [
    {
      ticker: "MSFT",
      price: 490.2,
      hv_20: 18.3,
      hv_60: 19.1,
      hv_252: 21.4,
      current_iv: 20.9,
      iv_rank: 44.0,
      leap_count: 5,
      best_gap: 0.5,
      is_mispriced: false,
    },
    {
      ticker: "AAPL",
      price: 210.1,
      hv_20: 22.0,
      hv_60: 21.0,
      hv_252: 24.0,
      current_iv: 18.0,
      iv_rank: 20.0,
      leap_count: 6,
      best_gap: 8.2,
      is_mispriced: true,
    },
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
  ],
};

const garchPayload = {
  scan_time: "2026-07-02T14:05:00Z",
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

test.use({ viewport: { width: 1440, height: 900 } });

test.beforeEach(async ({ page }) => {
  await page.route("**/api/leap", (route) =>
    route.fulfill({ json: leapPayload }),
  );
  await page.route("**/api/garch-convergence", (route) =>
    route.fulfill({ json: garchPayload }),
  );
});

test("scanner page mode tabs include LEAP, GARCH, and VOL CONE", async ({ page }) => {
  await page.goto("/scanner?mode=leap");
  const tabs = page.getByRole("tablist", { name: "Scanner mode" });
  await expect(tabs.getByRole("tab", { name: "LEAP" })).toBeVisible();
  await expect(tabs.getByRole("tab", { name: "GARCH" })).toBeVisible();
  await expect(tabs.getByRole("tab", { name: "VOL CONE" })).toBeVisible();
});

test("mode=leap renders the LEAP panel with candidates", async ({ page }) => {
  await page.goto("/scanner?mode=leap");
  const section = page.getByTestId("leap-scanner-section");
  await expect(section.getByText("LEAP IV Mispricing")).toBeVisible();
  await expect(section.getByRole("link", { name: "NVDA" })).toBeVisible();
  await expect(section.getByText("+13.7")).toBeVisible();
  await expect(section.getByText("MISPRICED", { exact: true }).first()).toBeVisible();
});

test("mode=leap sorts by Best Gap desc by default and Ticker on click", async ({ page }) => {
  await page.goto("/scanner?mode=leap");
  const section = page.getByTestId("leap-scanner-section");
  const tickers = () => section.locator("tbody tr td:first-child a").allTextContents();
  await expect.poll(tickers).toEqual(["NVDA", "AAPL", "MSFT"]);
  await expect(section.getByRole("columnheader", { name: /best gap/i })).toHaveAttribute("aria-sort", "descending");
  await section.getByRole("columnheader", { name: /ticker/i }).click();
  await expect.poll(tickers).toEqual(["AAPL", "MSFT", "NVDA"]);
  await expect(section.getByRole("columnheader", { name: /ticker/i })).toHaveAttribute("aria-sort", "ascending");
});

test("mode=garch renders the GARCH panel with pairs", async ({ page }) => {
  await page.goto("/scanner?mode=garch");
  const section = page.getByTestId("garch-scanner-section");
  await expect(section.getByText("GARCH Convergence")).toBeVisible();
  await expect(section.getByTestId("garch-row-NVDA-AMD")).toBeVisible();
  await expect(section.getByText("STRONG", { exact: true })).toBeVisible();
  await expect(section.getByText("1 ACTIONABLE")).toBeVisible();
});

test("clicking the GARCH tab from flow mode switches the panel and URL", async ({ page }) => {
  await page.goto("/scanner");
  await page.getByRole("tab", { name: "GARCH" }).click();
  await expect(page.getByTestId("garch-scanner-section")).toBeVisible();
  await expect(page).toHaveURL(/mode=garch/);
});
