import { expect, test, type Page } from "@playwright/test";

/**
 * Desktop book head: the feed pill and tape toggle must not slide left/right as
 * MID/BID/ASK alternate between 2- and 4-decimal ticks, and the toggle must not
 * be clipped by the head's overflow. Repro (2026-09-29, SPCX): MID 148.1150 /
 * BID 148.10 vs MID 148.1350 / BID 148.1100 moved the toggle ~20px per tick and
 * cut it to "Ta" beside the ticket rail.
 *
 * Layout-only, like book-montage-spacing: load the cockpit for the production
 * sheet at real viewports, then write the head's content exactly as OrderBook
 * renders it (value min-widths from depthPriceCh). No live depth socket needed.
 */

const now = new Date().toISOString();

async function stubApis(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  const json = (body: unknown) => (r: { fulfill: (o: object) => Promise<void> }) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/portfolio", json({
    bankroll: 100_000, peak_value: 100_000, last_sync: now, total_deployed_pct: 0,
    total_deployed_dollars: 0, remaining_capacity_pct: 100, position_count: 0,
    defined_risk_count: 0, undefined_risk_count: 0, avg_kelly_optimal: null,
    exposure: {}, violations: [], positions: [],
  }));
  await page.route("**/api/orders", json({ last_sync: now, open_orders: [], executed_orders: [] }));
  await page.route("**/api/regime", json({ score: 15, level: "LOW" }));
  await page.route("**/api/ib-status", json({ connected: true }));
  await page.route("**/api/blotter", json({ as_of: now, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }));
  await page.route("**/api/cash-flows**", json({ rows: [], summary: {} }));
  await page.route("**/api/flex-token", json({ ok: true, days_until_expiry: 14 }));
  await page.route("**/api/ticker/**", json({}));
}

const headInner = (mid: string, bid: string, ask: string, sprd: string) => `
  <span class="book-sym">SPCX<span class="book-kind">STOCK</span></span>
  <span class="book-head-stat">MID <b style="min-width:8ch">${mid}</b></span>
  <span class="book-head-stat bid">BID <b style="min-width:8ch">${bid}</b></span>
  <span class="book-head-stat ask">ASK <b style="min-width:8ch">${ask}</b></span>
  <span class="book-head-stat">SPRD <b style="min-width:4ch">${sprd}</b></span>
  <span class="book-head-spacer"></span>
  <span class="book-feed-pill">SMART DEPTH</span>
  <button type="button" class="book-toggle" role="switch" aria-checked="true" aria-label="Toggle Time and Sales">
    <span class="book-toggle-track"><span class="book-toggle-thumb"></span></span>
    <span class="book-toggle-text">Tape Shown</span>
  </button>`;

// Viewports span the book beside the ticket rail from ~460px (1024) to ~1320px (1920).
for (const viewport of [1024, 1280, 1366, 1920]) {
  test(`feed pill and tape toggle hold still across ticks and stay visible at ${viewport}px`, async ({ page }, testInfo) => {
    await stubApis(page);
    await page.setViewportSize({ width: viewport, height: 900 });
    await page.goto("/SPCX");
    await expect(page.locator(".book-window-head")).toBeVisible();

    async function tick(markup: string) {
      return page.evaluate((html) => {
        const head = document.querySelector<HTMLElement>(".book-window-head")!;
        head.innerHTML = html;
        const win = document.querySelector(".book-window")!.getBoundingClientRect();
        const pill = head.querySelector(".book-feed-pill")!.getBoundingClientRect();
        const toggle = head.querySelector(".book-toggle")!.getBoundingClientRect();
        return {
          pill: [pill.left, pill.top],
          toggle: [toggle.left, toggle.top],
          toggleOverflow: toggle.right - win.right,
          headOverflow: head.scrollWidth - head.clientWidth,
        };
      }, markup);
    }

    const a = await tick(headInner("148.1150", "148.10", "148.13", "0.03"));
    const b = await tick(headInner("148.1350", "148.1100", "148.16", "0.05"));
    expect(b.pill, "pill moved between ticks").toEqual(a.pill);
    expect(b.toggle, "toggle moved between ticks").toEqual(a.toggle);
    expect(b.toggleOverflow, "toggle cut off by the book window").toBeLessThanOrEqual(0);
    expect(b.headOverflow, "head content clipped").toBeLessThanOrEqual(0);

    const shot = testInfo.outputPath(`book-head-${viewport}.png`);
    await page.locator(".book-window-head").screenshot({ path: shot });
    await testInfo.attach(`book-head-${viewport}`, { path: shot, contentType: "image/png" });
  });
}
