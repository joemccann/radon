/**
 * E2E: Mobile P2 polish items at 393×852 (tasks/mobile-parity-audit.md).
 *
 * 1. RRV brush handles carry a finger-sized hit halo (visual stays 8px).
 */

import { test, expect, type Page } from "@playwright/test";

const REGIME = {
  scan_time: new Date().toISOString(),
  market_open: false,
  date: "2026-05-06",
  vix: 18.2,
  vvix: 95.4,
  spy: 580.0,
  vix_5d_roc: 1.2,
  vvix_vix_ratio: 5.24,
  realized_vol: 12.0,
  cor1m: 38.0,
  cor1m_5d_change: 0.5,
  spx_100d_ma: 575.0,
  spx_distance_pct: 0.87,
  spy_closes: Array.from({ length: 22 }, (_, i) => 575 + i * 0.5),
  cri: { score: 18, level: "LOW", components: { vix: 4, vvix: 5, correlation: 5, momentum: 4 } },
  cta: { exposure_pct: 92, forced_reduction_pct: 0, est_selling_bn: 0.5, realized_vol: 12.0 },
  crash_trigger: { triggered: false, conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, cor1m_gt_60: false } },
  history: Array.from({ length: 20 }, (_, i) => ({
    date: `2026-04-${(i + 10).toString().padStart(2, "0")}`,
    cri: 15 + i * 0.4,
    vix: 17 + i * 0.1,
    vvix: 90 + i * 0.5,
    cor1m: 35 + i * 0.3,
    realized_vol: 10 + i * 0.2,
  })),
};

const FLOW = {
  analysis_time: new Date().toISOString(),
  positions_scanned: 1,
  supports: [
    {
      ticker: "AAPL",
      position: "Long Call",
      direction: "LONG",
      flow_direction: "ACCUMULATION",
      flow_label: "ACCUM",
      flow_class: "accum",
      strength: 82,
      buy_ratio: 0.62,
      daily_buy_ratios: [
        { date: "2026-06-27", buy_ratio: 0.62 },
        { date: "2026-06-30", buy_ratio: 0.4 },
        { date: "2026-07-01", buy_ratio: 0.7 },
      ],
      note: "steady accumulation",
    },
  ],
  against: [],
  watch: [],
  neutral: [],
};

async function setupMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REGIME) }),
  );
  await page.route("**/api/flow-analysis", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(FLOW) }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
}

test.describe("Mobile P2 polish", () => {
  test("RRV brush handle hit area is finger-sized at 393px", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/cri", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("mobile-tab-bar")).toBeVisible();

    const handle = page.getByTestId("regime-spread-brush-handle-left");
    await handle.scrollIntoViewIfNeeded();
    await expect(handle).toBeVisible();

    // At the default range both handles sit at the container edges, where the
    // outward halo is clipped by overflow:hidden — probe the interior side,
    // which is where a finger actually lands.
    const probe = await handle.evaluate((el) => {
      const left = el;
      const right = document.querySelector('[data-testid="regime-spread-brush-handle-right"]')!;
      const lr = left.getBoundingClientRect();
      const rr = right.getBoundingClientRect();
      const midY = lr.top + lr.height / 2;
      return {
        visualWidth: Math.round(lr.width),
        leftHandleInteriorHit: document.elementFromPoint(lr.right + 10, midY) === left,
        rightHandleInteriorHit: document.elementFromPoint(rr.left - 10, midY) === right,
      };
    });

    expect(probe.visualWidth).toBeLessThanOrEqual(10);
    expect(probe.leftHandleInteriorHit).toBe(true);
    expect(probe.rightHandleInteriorHit).toBe(true);
  });

  test("flow cards carry a tappable buy-ratio sparkline at 393px", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/flow-analysis", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("mobile-tab-bar")).toBeVisible();

    const spark = page.getByTestId("mobile-flow-sparkline").first();
    await spark.scrollIntoViewIfNeeded();
    await expect(spark).toBeVisible();

    const caption = page.getByTestId("mobile-flow-sparkline-caption").first();
    await expect(caption).toHaveText("5D BUY RATIO");

    await page.getByTestId("mobile-flow-sparkline-svg").first().tap();
    await expect(caption).toContainText("%");
  });

  test("workflow canvas: add node + drag works at 393px, no page overflow", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await page.route("**/api/workflow", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ graphs: [] }) }),
    );
    await page.route("**/api/prices**", (route) => route.abort());
    await page.goto("/workflow", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("mobile-tab-bar")).toBeVisible();

    await page.getByRole("button", { name: "Scanner", exact: true }).tap();
    const node = page.locator(".react-flow__node").first();
    await expect(node).toBeVisible();

    const before = await node.boundingBox();
    expect(before).not.toBeNull();
    const cx = before!.x + before!.width / 2;
    const cy = before!.y + before!.height / 2;
    await page.mouse.move(cx, cy);
    await page.mouse.down();
    await page.mouse.move(cx + 70, cy + 50, { steps: 8 });
    await page.mouse.up();

    const after = await node.boundingBox();
    expect(after).not.toBeNull();
    const moved = Math.hypot(after!.x - before!.x, after!.y - before!.y);
    expect(moved).toBeGreaterThan(30);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
