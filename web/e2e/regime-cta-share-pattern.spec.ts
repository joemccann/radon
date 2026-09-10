/**
 * E2E: /regime and /cta share modal UX parity.
 *
 * Verifies:
 *  1. Share button opens the same modal pattern on both routes.
 *  2. The modal renders the shared share-modal nodes and labels.
 *  3. Clicking Escape closes the modal.
 */

import { expect, test } from "@playwright/test";

const REGIME_MOCK = {
  scan_time: "2026-03-19T16:00:00Z",
  market_open: false,
  date: "2026-03-19",
  vix: 20.1,
  vvix: 87.3,
  spy: 610.0,
  vix_5d_roc: 1.2,
  vvix_vix_ratio: 4.34,
  realized_vol: 9.5,
  cor1m: 42.0,
  cor1m_5d_change: 0.5,
  spx_100d_ma: 612.0,
  spx_distance_pct: -0.33,
  spy_closes: Array.from({ length: 22 }, (_, i) => 600 + i),
  cri: {
    score: 40,
    level: "ELEVATED",
    components: { vix: 8, vvix: 11, correlation: 9, momentum: 12 },
  },
  crash_trigger: {
    triggered: false,
    conditions: {
      spx_below_100d_ma: true,
      realized_vol_gt_25: false,
      cor1m_gt_60: false,
    },
  },
  history: [],
};

const CTA_MOCK = {
  date: "2026-03-19",
  fetched_at: "2026-03-19T16:10:00Z",
  source: "menthorq_s3_vision",
  tables: {
    main: [
      {
        underlying: "SPX",
        position_today: 0.32,
        position_yesterday: 0.30,
        position_1m_ago: 0.35,
        percentile_1m: 20,
        percentile_3m: 20,
        percentile_1y: 35,
        z_score_3m: -1.1,
      },
    ],
    index: [],
    commodity: [],
    currency: [],
  },
  sync_status: {
    service: "menthorq-cta",
    status: "success",
    target_date: "2026-03-19",
    started_at: "2026-03-19T16:00:00Z",
    finished_at: "2026-03-19T16:01:00Z",
    duration_ms: 1200,
    attempt_count: 1,
    cache_path: "data/menthorq_cache/cta_20260319.json",
    error_type: null,
    error_excerpt: null,
  },
  cache_meta: {
    last_refresh: "2026-03-19T16:10:00Z",
    age_seconds: 60,
    is_stale: false,
    stale_threshold_seconds: null,
    target_date: "2026-03-19",
    latest_cache_date: "2026-03-19",
    stale_reason: "fresh",
  },
};

async function setupShareMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REGIME_MOCK) }),
  );
  await page.route("**/api/menthorq/cta", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CTA_MOCK) }),
  );
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ bankroll: 100_000, positions: [] }) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: false }) }),
  );
  await page.route("**/api/prices", (route) => route.abort());
  await page.route("**/api/regime/share", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ preview_path: "/tmp/regime-share.html" }) }),
  );
  await page.route("**/api/menthorq/cta/share", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ preview_path: "/tmp/cta-share.html" }) }),
  );
  await page.route("**/api/regime/share/content**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<html><body><div>Regime share preview</div></body></html>",
    }),
  );
  await page.route("**/api/menthorq/cta/share/content**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<html><body><div>CTA share preview</div></body></html>",
    }),
  );
}

async function expectShareModal(
  page: import("@playwright/test").Page,
  expectedTitle: string,
) {
  const modal = page.getByTestId("share-modal-backdrop");
  await expect(modal).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("share-modal-panel")).toBeVisible();
  await expect(page.getByTestId("share-modal-title")).toHaveText(expectedTitle);
  await expect(page.getByTestId("share-modal-iframe")).toHaveAttribute("src", /^blob:/);
  // The download link and the close control shared one class, so a bare class
  // locator resolved to two nodes and tripped strict mode. They are distinct
  // controls and now carry distinct ids.
  await expect(page.getByTestId("share-modal-download")).toHaveAttribute("href", /^blob:/);
}

test.describe("/regime and /cta share UX", () => {
  test("opens shared modal pattern on /regime", async ({ page }) => {
    await setupShareMocks(page);
    await page.goto("/regime");

    await page.getByRole("button", { name: "Share to X" }).click();
    await expectShareModal(page, "REGIME REPORT: SHARE TO X");

    await page.keyboard.press("Escape");
    await expect(page.getByTestId("share-modal-backdrop")).toBeHidden();
  });

  test("opens shared modal pattern on /cta", async ({ page }) => {
    await setupShareMocks(page);
    await page.goto("/cta");

    await page.getByRole("button", { name: "Share to X" }).click();
    await expectShareModal(page, "CTA REPORT: SHARE TO X");

    await page.getByTestId("share-modal-close").click();
    await expect(page.getByTestId("share-modal-backdrop")).toBeHidden();
  });
});
