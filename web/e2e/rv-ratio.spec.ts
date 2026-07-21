import { expect, test, type Page } from "@playwright/test";

import {
  buildCompleteRvRatioFixture,
  buildMissingRvRatioPayload,
  buildPartialRvRatioFixture,
} from "../tests/rv-ratio-fixture";
import type { RvRatioMissingPayload, RvRatioPayload } from "../lib/rvRatio";

const COMPLETE = buildCompleteRvRatioFixture({ symbol: "SMH" });
const PARTIAL = buildPartialRvRatioFixture({ symbol: "RDDT" });

type RvRatioStubOptions = {
  get?: RvRatioPayload | RvRatioMissingPayload;
  post?: RvRatioPayload;
  holdGet?: boolean;
  holdPost?: boolean;
};

/**
 * Stub the WorkspaceShell's ambient APIs with real minimal shapes (a bare {}
 * for /api/portfolio crashes the shell — positions is iterated during render)
 * and the RV-ratio route with the shared section 6 fixture. The GET/POST can
 * be held open to observe the loader states deterministically.
 */
async function mockRvRatioWorkspace(page: Page, options: RvRatioStubOptions = {}) {
  const { get = COMPLETE, post = COMPLETE, holdGet = false, holdPost = false } = options;
  let releaseGet = () => undefined;
  let releasePost = () => undefined;
  const getGate = holdGet ? new Promise<void>((resolve) => { releaseGet = resolve; }) : null;
  const postGate = holdPost ? new Promise<void>((resolve) => { releasePost = resolve; }) : null;
  let postRequests = 0;

  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    const payloads: Record<string, unknown> = {
      "/api/portfolio": { positions: [], exposure: {}, violations: [], account_summary: null },
      "/api/orders": { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 },
      "/api/watchlist": { watchlist: [] },
      "/api/service-health": { services: [] },
      "/api/profile": { username: "Operator", avatar_url: null },
      "/api/alerts": { alerts: [] },
      "/api/flex-token": { remaining: 240 },
      "/api/previous-close": { closes: {} },
    };
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payloads[path] ?? {}),
    });
  });

  await page.route("**/api/options/rv-ratio?*", async (route) => {
    if (route.request().method() === "POST") {
      postRequests += 1;
      if (postGate) await postGate;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(post),
      });
      return;
    }
    if (getGate) await getGate;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(get),
    });
  });

  return {
    releaseGet: () => releaseGet(),
    releasePost: () => releasePost(),
    postRequests: () => postRequests,
  };
}

test("Rel Vol tab is reachable from Net GEX with the symbol carried through", async ({ page }) => {
  await mockRvRatioWorkspace(page);
  await page.goto("/options/net-gex?symbol=SMH");

  const relVolTab = page.getByRole("tab", { name: "Rel Vol" });
  await expect(relVolTab).toBeVisible();
  await expect(relVolTab).toHaveAttribute("aria-selected", "false");
  // The reserved VIX/term-structure slot stays planned and disabled.
  await expect(page.getByRole("tab", { name: /VIX \/ Volatility/ })).toHaveAttribute(
    "aria-disabled",
    "true",
  );

  await relVolTab.click();
  await expect(page).toHaveURL(/\/options\/rv-ratio\?symbol=SMH$/);
  await expect(relVolTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("rv-ratio-panel")).toBeVisible();
  await expect(page.getByText("SMH / SPY")).toBeVisible();
});

test("Rel Vol shows the sampling loader, then stats, chart, band, and brush", async ({ page }) => {
  const workspace = await mockRvRatioWorkspace(page, { holdGet: true });
  await page.goto("/options/rv-ratio?symbol=SMH");

  await expect(
    page.getByRole("status", { name: "Sampling 252-session realized vol" }),
  ).toBeVisible();

  workspace.releaseGet();

  // Header: regime badge from the fixture's precomputed divergence, no PARTIAL.
  await expect(page.getByTestId("rv-ratio-regime-badge")).toBeVisible();
  await expect(page.getByText("PARTIAL", { exact: true })).toHaveCount(0);
  const telemetry = page.getByTestId("rv-ratio-telemetry");
  await expect(telemetry).toContainText(`AS OF ${COMPLETE.coverage.last_date} CLOSE`);
  await expect(telemetry).toContainText("BENCHMARK SPY");

  // Six stat tiles with fixture-derived values.
  await expect(page.getByTestId("rv-ratio-stat-high")).toContainText(
    `${COMPLETE.stats.high.toFixed(2)}x`,
  );
  await expect(page.getByTestId("rv-ratio-stat-low")).toContainText(
    `${COMPLETE.stats.low.toFixed(2)}x`,
  );
  await expect(page.getByTestId("rv-ratio-stat-avg")).toContainText(
    `${COMPLETE.stats.avg.toFixed(2)}x`,
  );
  await expect(page.getByTestId("rv-ratio-stat-last")).toContainText(
    `${COMPLETE.stats.last.toFixed(2)}x`,
  );
  await expect(page.getByTestId("rv-ratio-stat-stddev")).toContainText(
    COMPLETE.stats.stddev.toFixed(2),
  );
  await expect(page.getByTestId("rv-ratio-stat-pctl")).toContainText(
    `${Math.round(COMPLETE.stats.last_percentile * 100)}%`,
  );

  // Chart with the full-history sigma band, line, and brush minimap.
  await expect(page.getByTestId("rv-ratio-chart-svg")).toBeVisible();
  await expect(page.locator('[data-testid="rv-ratio-band"]')).toHaveCount(1);
  await expect(page.locator('[data-testid="rv-ratio-band-avg"]')).toHaveCount(1);
  await expect(page.locator('[data-testid="rv-ratio-line"]')).toHaveCount(1);
  await expect(page.getByTestId("rv-ratio-brush")).toBeVisible();
  await expect(page.getByText("VOL RATIO (x SPY)")).toBeVisible();
});

test("Rel Vol preset chips flip the visible range", async ({ page }) => {
  await mockRvRatioWorkspace(page);
  await page.goto("/options/rv-ratio?symbol=SMH");

  const oneYear = page.getByTestId("rv-ratio-range-chips-1y");
  const threeYear = page.getByTestId("rv-ratio-range-chips-3y");
  await expect(oneYear).toHaveAttribute("aria-pressed", "true");
  await expect(threeYear).toBeVisible();
  await expect(page.getByTestId("rv-ratio-range-chips-5y")).toBeVisible();

  await threeYear.click();
  await expect(threeYear).toHaveAttribute("aria-pressed", "true");
  await expect(oneYear).toHaveAttribute("aria-pressed", "false");
});

test("Rel Vol cold symbol shows the backfill loader while the scan POST runs", async ({ page }) => {
  const workspace = await mockRvRatioWorkspace(page, {
    get: buildMissingRvRatioPayload("SMH"),
    post: COMPLETE,
    holdPost: true,
  });
  await page.goto("/options/rv-ratio?symbol=SMH");

  await expect(
    page.getByRole("status", { name: "First measurement. Fetching 12 years of closes" }),
  ).toBeVisible();

  workspace.releasePost();
  await expect(page.getByTestId("rv-ratio-stats")).toBeVisible();
  await expect(page.getByTestId("rv-ratio-chart-svg")).toBeVisible();
  expect(workspace.postRequests()).toBe(1);
});

test("Rel Vol partial coverage renders the PARTIAL badge and hides deep presets", async ({ page }) => {
  await mockRvRatioWorkspace(page, { get: PARTIAL });
  await page.goto("/options/rv-ratio?symbol=RDDT");

  await expect(page.getByText("RDDT / SPY")).toBeVisible();
  await expect(page.getByText("PARTIAL", { exact: true })).toBeVisible();
  // A ~300-session listing cannot offer the deep multi-year presets.
  await expect(page.getByTestId("rv-ratio-range-chips-1y")).toBeVisible();
  await expect(page.getByTestId("rv-ratio-range-chips-3y")).toHaveCount(0);
  await expect(page.getByTestId("rv-ratio-range-chips-5y")).toHaveCount(0);
});

test("Rel Vol stays usable at 393x852 without document-level horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 393, height: 852 });
  await mockRvRatioWorkspace(page);
  await page.goto("/options/rv-ratio?symbol=SMH");

  await expect(page.getByTestId("options-workspace")).toBeVisible();
  await expect(page.getByTestId("rv-ratio-panel")).toBeVisible();
  await expect(page.getByTestId("rv-ratio-stats")).toBeVisible();
  await expect(page.getByTestId("rv-ratio-chart-svg")).toBeVisible();

  const hasDocumentOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  );
  expect(hasDocumentOverflow).toBe(false);
});
