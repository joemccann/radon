import { expect, test } from "@playwright/test";

import { lastCompletedSessionDate } from "../lib/marketSession";

/**
 * The strip's day-change baseline is read off the payload's OWN daily history
 * and anchored to `lastCompletedSessionDate()`; `resolvePreviousSessionClose`
 * withholds it when the newest history row is more than
 * MAX_PREVIOUS_CLOSE_GAP_DAYS (7) off that anchor (web/lib/regimeLiveStrip.ts).
 * Hard-coded 2026-02 fixture dates therefore aged out permanently, `<DayChange>`
 * stopped rendering, and this layout spec — which measures cell heights and the
 * `regime-day-chg` node itself — failed on every run. The fixture is now
 * window-relative so it stays valid whenever the suite runs.
 */
const HISTORY_ANCHOR = lastCompletedSessionDate();
const HISTORY_LENGTH = 20;

function daysBefore(anchor: string, daysBack: number): string {
  const d = new Date(`${anchor}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - daysBack);
  return d.toISOString().slice(0, 10);
}

const CRI_MOCK = {
  scan_time: "2026-03-12T08:18:32",
  market_open: true,
  date: "2026-03-12",
  vix: 24.79,
  vvix: 125.03,
  spy: 667.39,
  vix_5d_roc: 16.28,
  vvix_vix_ratio: 5.34,
  realized_vol: 12.97,
  cor1m: 34.04,
  cor1m_previous_close: 29.14,
  cor1m_5d_change: 11.23,
  spx_100d_ma: 682.30,
  spx_distance_pct: -2.19,
  spy_closes: Array.from({ length: 40 }, (_, index) => 640 + index),
  cri: {
    score: 36,
    level: "ELEVATED",
    components: {
      vix: 9.5,
      vvix: 13.3,
      correlation: 7.9,
      momentum: 5.5,
    },
  },
  crash_trigger: {
    triggered: false,
    conditions: {
      spx_below_100d_ma: true,
      realized_vol_gt_25: false,
      cor1m_gt_60: false,
    },
  },
  cta: {
    exposure_pct: 74,
    forced_reduction_pct: 0,
    est_selling_bn: 0,
  },
  menthorq_cta: null,
  history: Array.from({ length: HISTORY_LENGTH }, (_, index) => ({
    date: daysBefore(HISTORY_ANCHOR, HISTORY_LENGTH - 1 - index),
    vix: 20 + index * 0.3,
    vvix: 105 + index * 1.1,
    spy: 650 + index,
    realized_vol: 11.2 + index * 0.1,
    cor1m: 18 + index * 0.7,
    spx_vs_ma_pct: -1.8 + index * 0.1,
    vix_5d_roc: 0.4 + index * 0.2,
  })),
};

const PORTFOLIO_EMPTY = {
  bankroll: 100_000,
  positions: [],
  account_summary: {},
  exposure: {},
  violations: [],
};

const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const LIVE_BATCH = {
  VIX: {
    symbol: "VIX",
    last: 26.21,
    lastIsCalculated: false,
    bid: 26.1,
    ask: 26.3,
    bidSize: null,
    askSize: null,
    volume: null,
    high: 26.8,
    low: 24.9,
    open: 25.1,
    close: 24.23,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: "2026-03-12T17:05:00.000Z",
  },
  VVIX: {
    symbol: "VVIX",
    last: 126.26,
    lastIsCalculated: false,
    bid: 126.1,
    ask: 126.4,
    bidSize: null,
    askSize: null,
    volume: null,
    high: 127.0,
    low: 121.4,
    open: 122.2,
    close: 122.49,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: "2026-03-12T17:05:00.000Z",
  },
  SPY: {
    symbol: "SPY",
    last: 668.15,
    lastIsCalculated: false,
    bid: 668.0,
    ask: 668.3,
    bidSize: 100,
    askSize: 100,
    volume: 1000,
    high: 671.2,
    low: 664.8,
    open: 670.2,
    close: 676.33,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: "2026-03-12T17:05:00.000Z",
  },
  COR1M: {
    symbol: "COR1M",
    last: 31.60,
    lastIsCalculated: false,
    bid: 31.5,
    ask: 31.7,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: 31.29,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: "2026-03-12T17:05:00.000Z",
  },
};

async function setupMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/regime", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(CRI_MOCK),
    }),
  );
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_EMPTY),
    }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_EMPTY),
    }),
  );
  await page.route("**/api/prices", (route) => route.abort());
  await page.route("**/api/previous-close", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ closes: {} }),
    }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: true }),
    }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  await page.route("**/api/menthorq/cta", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ tables: [] }),
    }),
  );

  await page.addInitScript((liveBatch) => {
    class MockWebSocket {
      static OPEN = 1;
      url: string;
      readyState = 0;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;
      onclose: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;

      constructor(url: string) {
        this.url = url;
        window.setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.(new Event("open"));
        }, 0);
        window.setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({
              type: "status",
              ib_connected: true,
              subscriptions: ["SPY", "VIX", "VVIX", "COR1M"],
            }),
          } as MessageEvent<string>);
        }, 10);
        window.setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({ type: "batch", updates: liveBatch }),
          } as MessageEvent<string>);
        }, 25);
      }

      send(_message: string) {}

      close() {
        this.readyState = 3;
        this.onclose?.(new Event("close"));
      }
    }

    Object.defineProperty(window, "WebSocket", {
      configurable: true,
      writable: true,
      value: MockWebSocket,
    });
  }, LIVE_BATCH);
}

test.describe("/regime page — responsive strip collapse", () => {
  test("collapses to a symmetric 3x2 strip at narrower desktop widths and abbreviates REALIZED VOL to RVOL", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 1400 });
    await setupMocks(page);
    await page.goto("/regime");

    const strip = page.locator(".regime-strip");
    const rvolLabelShort = page.locator('[data-testid="strip-rvol"] .regime-strip-label-text-short');
    const rvolLabelFull = page.locator('[data-testid="strip-rvol"] .regime-strip-label-text-full');
    await expect(strip).toBeVisible();
    await expect(rvolLabelShort).toHaveText("RVOL");
    await expect(rvolLabelShort).toBeVisible();
    await expect(rvolLabelFull).toBeHidden();

    // Cell order is VIX, VVIX, COR1M, RVOL, SPY (d3acdd57), so the 3-column
    // grid puts COR1M on row 1 beside VIX and leaves RVOL + SPY on row 2.
    const [vixBox, cor1mBox, rvolBox, spyBox] = await Promise.all([
      page.locator('[data-testid="strip-vix"]').boundingBox(),
      page.locator('[data-testid="strip-cor1m"]').boundingBox(),
      page.locator('[data-testid="strip-rvol"]').boundingBox(),
      page.locator('[data-testid="strip-spy"]').boundingBox(),
    ]);

    expect(vixBox).not.toBeNull();
    expect(cor1mBox).not.toBeNull();
    expect(rvolBox).not.toBeNull();
    expect(spyBox).not.toBeNull();
    expect(Math.abs((cor1mBox?.y ?? 0) - (vixBox?.y ?? 0))).toBeLessThan(4);
    expect((rvolBox?.y ?? 0)).toBeGreaterThan((vixBox?.y ?? 0) + 20);
    expect((spyBox?.y ?? 0)).toBeGreaterThan((vixBox?.y ?? 0) + 20);
    expect(Math.abs((spyBox?.y ?? 0) - (rvolBox?.y ?? 0))).toBeLessThan(4);
    expect(Math.abs((spyBox?.width ?? 0) - (rvolBox?.width ?? 0))).toBeLessThan(4);
    expect((rvolBox?.width ?? 0)).toBeGreaterThan((vixBox?.width ?? 0) * 1.4);

    const stripMetrics = await strip.evaluate((node) => ({
      clientWidth: node.clientWidth,
      scrollWidth: node.scrollWidth,
    }));
    expect(stripMetrics.scrollWidth).toBeLessThanOrEqual(stripMetrics.clientWidth + 1);
  });

  test("stacks the strip vertically on smaller screens", async ({ page }) => {
    await page.setViewportSize({ width: 700, height: 1400 });
    await setupMocks(page);
    await page.goto("/regime");

    // Single column: every cell stacks in the shipped order VIX, VVIX, COR1M,
    // RVOL, SPY (d3acdd57) and shares the same left edge.
    const [vixBox, vvixBox, cor1mBox, rvolBox, spyBox] = await Promise.all([
      page.locator('[data-testid="strip-vix"]').boundingBox(),
      page.locator('[data-testid="strip-vvix"]').boundingBox(),
      page.locator('[data-testid="strip-cor1m"]').boundingBox(),
      page.locator('[data-testid="strip-rvol"]').boundingBox(),
      page.locator('[data-testid="strip-spy"]').boundingBox(),
    ]);

    expect(vixBox).not.toBeNull();
    expect(vvixBox).not.toBeNull();
    expect(cor1mBox).not.toBeNull();
    expect(rvolBox).not.toBeNull();
    expect(spyBox).not.toBeNull();
    expect((vvixBox?.y ?? 0)).toBeGreaterThan((vixBox?.y ?? 0) + 20);
    expect((cor1mBox?.y ?? 0)).toBeGreaterThan((vvixBox?.y ?? 0) + 20);
    expect((rvolBox?.y ?? 0)).toBeGreaterThan((cor1mBox?.y ?? 0) + 20);
    expect((spyBox?.y ?? 0)).toBeGreaterThan((rvolBox?.y ?? 0) + 20);
    expect(Math.abs((vvixBox?.x ?? 0) - (vixBox?.x ?? 0))).toBeLessThan(4);
    expect(Math.abs((cor1mBox?.x ?? 0) - (vixBox?.x ?? 0))).toBeLessThan(4);
    expect(Math.abs((rvolBox?.x ?? 0) - (vixBox?.x ?? 0))).toBeLessThan(4);
    expect(Math.abs((spyBox?.x ?? 0) - (vixBox?.x ?? 0))).toBeLessThan(4);

    const [vixCellBox, vixChangeTextBox, vixChangeArrowBox] = await Promise.all([
      page.locator('[data-testid="strip-vix"]').boundingBox(),
      page.locator('[data-testid="strip-vix"] [data-testid="regime-day-chg"] span').boundingBox(),
      page.locator('[data-testid="strip-vix"] [data-testid="regime-day-chg"] svg').boundingBox(),
    ]);

    expect(vixCellBox).not.toBeNull();
    expect(vixChangeTextBox).not.toBeNull();
    expect(vixChangeArrowBox).not.toBeNull();
    expect(Math.abs((vixChangeArrowBox?.y ?? 0) - (vixChangeTextBox?.y ?? 0))).toBeLessThan(6);
    expect((vixChangeArrowBox?.x ?? 0) - ((vixChangeTextBox?.x ?? 0) + (vixChangeTextBox?.width ?? 0))).toBeLessThan(14);
    expect((vixChangeArrowBox?.x ?? 0)).toBeLessThan((vixCellBox?.x ?? 0) + (vixCellBox?.width ?? 0) * 0.75);
  });

  test("uses a left-aligned second-column telemetry rail when the strip stacks on smaller screens", async ({ page }) => {
    await page.setViewportSize({ width: 700, height: 1400 });
    await setupMocks(page);
    await page.goto("/regime");

    const [cellBox, primaryBox, labelBox, valueBox, metaRowBox, changeBox, subBox, tsBox] = await Promise.all([
      page.locator('[data-testid="strip-vix"]').boundingBox(),
      page.locator('[data-testid="strip-vix"] .regime-strip-primary').boundingBox(),
      page.locator('[data-testid="strip-vix"] .regime-strip-label').boundingBox(),
      page.locator('[data-testid="strip-vix"] .regime-strip-value').boundingBox(),
      page.locator('[data-testid="strip-vix"] .regime-strip-meta-row').boundingBox(),
      page.locator('[data-testid="strip-vix"] [data-testid="regime-day-chg"]').boundingBox(),
      page.locator('[data-testid="strip-vix"] .regime-strip-sub').boundingBox(),
      page.locator('[data-testid="strip-vix"] .regime-strip-ts').boundingBox(),
    ]);

    expect(cellBox).not.toBeNull();
    expect(primaryBox).not.toBeNull();
    expect(labelBox).not.toBeNull();
    expect(valueBox).not.toBeNull();
    expect(metaRowBox).not.toBeNull();
    expect(changeBox).not.toBeNull();
    expect(subBox).not.toBeNull();
    expect(tsBox).not.toBeNull();

    expect((labelBox?.x ?? 0)).toBeLessThan((valueBox?.x ?? 0) + 4);
    expect((metaRowBox?.x ?? 0)).toBeGreaterThanOrEqual((primaryBox?.x ?? 0) + (primaryBox?.width ?? 0) - 4);
    expect((metaRowBox?.x ?? 0)).toBeLessThan((cellBox?.x ?? 0) + (cellBox?.width ?? 0) * 0.55);
    expect((metaRowBox?.width ?? 0)).toBeGreaterThan((cellBox?.width ?? 0) * 0.45);
    expect((changeBox?.x ?? 0) - (metaRowBox?.x ?? 0)).toBeLessThan(18);
    expect((subBox?.x ?? 0)).toBeGreaterThan((changeBox?.x ?? 0));
    expect((tsBox?.x ?? 0)).toBeGreaterThan((subBox?.x ?? 0));
    expect((cellBox?.height ?? 0)).toBeLessThan(92);
  });
});
