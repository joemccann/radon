/**
 * E2E: Flow Surprise card on the dashboard.
 *
 * Stubs /api/flow-surprise with a ranked payload and confirms the
 * FlowSurpriseCard renders the header, the top ticker, and the EXCESS chip.
 * Also confirms the empty-state copy when there are no results.
 *
 * Runs under the desktop (chromium) project — the dashboard left column
 * (where FlowSurpriseCard lives) is full-width and visible at desktop sizes.
 * Mobile collapses the sections; the desktop project is the canonical check.
 */

import { test, expect, type Page } from "@playwright/test";

const FLOW_SURPRISE = {
  results: [
    {
      ticker: "AAPL",
      metric: "flow_strength",
      status: "ok",
      flag: "EXCESS",
      actual: 8.2,
      expected_median: 3.1,
      surprise_pit: 0.97,
    },
    {
      ticker: "TSLA",
      metric: "flow_strength",
      status: "ok",
      flag: "DEFICIT",
      actual: 0.4,
      expected_median: 4.0,
      surprise_pit: 0.03,
    },
  ],
  metric: "flow_strength",
  scan_time: new Date().toISOString(),
  count_ok: 2,
  skipped: 0,
  cache_meta: { last_refresh: new Date().toISOString(), age_seconds: 10, is_stale: false, stale_threshold_seconds: 600 },
};

const FLOW_SURPRISE_EMPTY = {
  results: [],
  missing: true,
  metric: "flow_strength",
  scan_time: "",
  count_ok: 0,
  skipped: 0,
};

async function stubFlowSurprise(page: Page, body: unknown) {
  await page.route("**/api/flow-surprise", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) }),
  );
}

test.describe("dashboard Flow Surprise card", () => {
  test("renders the card with ranked surprises", async ({ page }) => {
    await stubFlowSurprise(page, FLOW_SURPRISE);
    await page.goto("/");

    const section = page.locator('[data-testid="dashboard-section-flow-surprise"]');
    await section.waitFor({ timeout: 15_000 });

    await expect(section).toContainText("Flow Surprise");
    await expect(section).toContainText("AAPL");
    await expect(section).toContainText("EXCESS");
  });

  test("renders the empty-state copy when there are no results", async ({ page }) => {
    await stubFlowSurprise(page, FLOW_SURPRISE_EMPTY);
    await page.goto("/");

    const section = page.locator('[data-testid="dashboard-section-flow-surprise"]');
    await section.waitFor({ timeout: 15_000 });

    await expect(section).toContainText("No flow surprises yet");
  });
});
