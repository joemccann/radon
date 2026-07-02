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

test("scanner page mode tabs include LEAP and GARCH", async ({ page }) => {
  await page.goto("/scanner?mode=leap");
  const tabs = page.getByRole("tablist", { name: "Scanner mode" });
  await expect(tabs.getByRole("tab", { name: "LEAP" })).toBeVisible();
  await expect(tabs.getByRole("tab", { name: "GARCH" })).toBeVisible();
});

test("mode=leap renders the LEAP panel with candidates", async ({ page }) => {
  await page.goto("/scanner?mode=leap");
  const section = page.getByTestId("leap-scanner-section");
  await expect(section.getByText("LEAP IV Mispricing")).toBeVisible();
  await expect(section.getByRole("link", { name: "NVDA" })).toBeVisible();
  await expect(section.getByText("+13.7")).toBeVisible();
  await expect(section.getByText("MISPRICED", { exact: true })).toBeVisible();
});

test("mode=garch renders the GARCH panel with pairs", async ({ page }) => {
  await page.goto("/scanner?mode=garch");
  const section = page.getByTestId("garch-scanner-section");
  await expect(section.getByText("GARCH Convergence")).toBeVisible();
  await expect(section.getByText("NVDA ↔ AMD")).toBeVisible();
  await expect(section.getByText("STRONG", { exact: true })).toBeVisible();
  await expect(section.getByText("1 ACTIONABLE")).toBeVisible();
});

test("clicking the GARCH tab from flow mode switches the panel and URL", async ({ page }) => {
  await page.goto("/scanner");
  await page.getByRole("tab", { name: "GARCH" }).click();
  await expect(page.getByTestId("garch-scanner-section")).toBeVisible();
  await expect(page).toHaveURL(/mode=garch/);
});
