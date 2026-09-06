import { expect, test, type Page } from "@playwright/test";
import { resolve } from "node:path";

const strikes = Array.from({ length: 21 }, (_, index) => 805 + index * 5);
const expirations = [
  { expiration_date: "2026-07-17", dte: 1 },
  { expiration_date: "2026-07-24", dte: 8 },
];

function exposurePayload(frequency: "eod" | "intraday", symbol = "MU") {
  const strike_idx: number[] = [];
  const expiration_idx: number[] = [];
  const net_gex: number[] = [];
  const abs_gex: number[] = [];
  const net_dex: number[] = [];
  const abs_dex: number[] = [];
  const oi_call: number[] = [];
  const oi_put: number[] = [];

  strikes.forEach((strike, strikeIndex) => {
    expirations.forEach((_, expirationIndex) => {
      const distance = strike - 855;
      const signedGex = (distance >= 20 ? 1 : -1) * (350_000 + Math.abs(distance) * 81_000) * (expirationIndex + 1);
      strike_idx.push(strikeIndex);
      expiration_idx.push(expirationIndex);
      net_gex.push(signedGex);
      abs_gex.push(Math.abs(signedGex));
      net_dex.push(signedGex * 0.42);
      abs_dex.push(Math.abs(signedGex * 0.42));
      oi_call.push(800 + strikeIndex * 71 + expirationIndex * 120);
      oi_put.push(2_900 - strikeIndex * 62 + expirationIndex * 140);
    });
  });

  return {
    schema_version: 1,
    symbol,
    source: "menthorq_dashboard",
    source_time: frequency === "eod" ? "2026-07-16T20:00:00" : "2026-07-16T19:30:00",
    fetched_at: "2026-07-17T03:00:00Z",
    frequency,
    spot: frequency === "eod" ? 853.2 : 846.33,
    strikes,
    expirations,
    cells: { strike_idx, expiration_idx, net_gex, abs_gex, net_dex, abs_dex, oi_call, oi_put },
    levels: [
      { key: "hvl", label: "HVL", value: 900 },
      { key: "call_resistance", label: "CR", value: 910 },
      { key: "put_support", label: "PS", value: 815 },
      { key: "call_resistance_0dte", label: "CR 0DTE", value: 910 },
      { key: "put_support_0dte", label: "PS 0DTE", value: 815 },
      { key: "max_1d", label: "1D Max", value: 904.12 },
      { key: "min_1d", label: "1D Min", value: 802.28 },
    ],
    units: {
      spot: "usd_per_share",
      strike: "usd_per_share",
      net_gex: "usd_per_1pct_move",
      abs_gex: "usd_per_1pct_move",
      net_dex: "usd",
      abs_dex: "usd",
      oi_call: "contracts",
      oi_put: "contracts",
    },
    complete: true,
  };
}

async function mockWorkspace(page: Page, { holdExposure = false }: { holdExposure?: boolean } = {}) {
  let exposureRequests = 0;
  let releaseExposure = () => undefined;
  const exposureGate = holdExposure
    ? new Promise<void>((resolve) => { releaseExposure = resolve; })
    : null;

  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" }),
  );
  await page.route("**/api/options/exposure?*", async (route) => {
    exposureRequests += 1;
    const requestUrl = new URL(route.request().url());
    const frequency = requestUrl.searchParams.get("frequency") === "intraday"
      ? "intraday"
      : "eod";
    if (exposureGate) await exposureGate;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(exposurePayload(frequency, requestUrl.searchParams.get("symbol") ?? "MU")),
    });
  });

  return {
    exposureRequests: () => exposureRequests,
    releaseExposure,
  };
}

test("Options waits for a valid ticker, then shows the spectral measurement loader", async ({ page }) => {
  const workspace = await mockWorkspace(page, { holdExposure: true });
  await page.goto("/options/net-gex");

  await expect(page.getByRole("search", { name: "Options ticker" })).toBeVisible();
  await expect(page.getByTestId("options-exposure-panel")).toHaveCount(0);
  expect(workspace.exposureRequests()).toBe(0);

  await page.screenshot({
    path: resolve(process.cwd(), "../tasks/artifacts/options-exposure-entry-desktop.png"),
    fullPage: true,
  });

  await page.getByLabel("Ticker symbol").fill("mu");
  await page.getByRole("button", { name: "Load exposure" }).click();

  await expect(page).toHaveURL(/\/options\/net-gex\?symbol=MU$/);
  await expect(page.getByRole("status", { name: "Sampling options exposure for MU." })).toBeVisible();
  workspace.releaseExposure();
  await expect(page.getByRole("table", { name: "MU options exposure by strike" })).toBeVisible();
});

test("Options workspace opens its Net GEX tab with the captured controls and client-side transforms", async ({ page }) => {
  await mockWorkspace(page);
  await page.goto("/options/net-gex?symbol=MU");

  await expect(page.getByRole("heading", { name: "Options", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Net GEX" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: /DEX/ })).toHaveAttribute("aria-disabled", "true");
  await expect(page.getByRole("heading", { name: "Options exposure" })).toBeVisible();
  await expect(page.getByLabel("Exposure metric")).toHaveValue("net_gex");
  await expect(page.getByLabel("Strike range")).toHaveValue("10");
  await expect(page.getByRole("button", { name: "EOD" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "All Expirations" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("table", { name: "MU options exposure by strike" })).toBeVisible();
  await expect(page.getByTestId("exposure-row-855")).toHaveAttribute("data-spot", "true");
  await expect(page.getByTestId("live-data-degraded")).toHaveCount(0);
  expect(await page.getByRole("columnheader").evaluateAll(
    (headers) => headers.every((header) => getComputedStyle(header).textAlign === "center"),
  )).toBe(true);

  await page.locator(".content").evaluate((content) => content.scrollTo({ top: content.scrollHeight }));
  const stickyHeader = await page.evaluate(() => {
    const content = document.querySelector<HTMLElement>(".content");
    // Strike cells use semantic row headers; only column headers are sticky.
    const headers = [...document.querySelectorAll<HTMLTableCellElement>("[data-testid='options-exposure-table-wrap'] thead th")];
    if (!content || headers.length === 0) return null;
    return {
      contentTop: content.getBoundingClientRect().top + Number.parseFloat(getComputedStyle(content).paddingTop),
      scrollTop: content.scrollTop,
      headerTops: headers.map((header) => header.getBoundingClientRect().top),
    };
  });
  expect(stickyHeader?.scrollTop).toBeGreaterThan(0);
  expect(stickyHeader?.headerTops.every((top) => Math.abs(top - (stickyHeader?.contentTop ?? top)) <= 1), JSON.stringify(stickyHeader)).toBe(true);

  await page.screenshot({
    path: resolve(process.cwd(), "../tasks/artifacts/options-exposure-desktop.png"),
    fullPage: true,
  });

  await page.getByLabel("Exposure metric").selectOption("open_interest");
  await expect(page.getByRole("columnheader", { name: "Open Interest" })).toBeVisible();

  await page.getByLabel("Strike range").selectOption("5");
  await expect(page.locator('tbody tr[data-testid^="exposure-row-"]')).toHaveCount(11);

  await page.getByRole("button", { name: /Jul 17/ }).click();
  await expect(page.getByRole("button", { name: /Jul 17/ })).toHaveAttribute("aria-pressed", "true");

  const intradayResponse = page.waitForResponse((response) =>
    response.url().includes("/api/options/exposure")
      && response.url().includes("frequency=intraday"),
  );
  await page.getByRole("button", { name: "Intraday" }).click();
  await intradayResponse;
  await expect(page.getByRole("button", { name: "Intraday" })).toHaveAttribute("aria-pressed", "true");
});

test("Options workspace canonicalizes root and legacy entry points", async ({ page }) => {
  await mockWorkspace(page);

  await page.goto("/options");
  await expect(page).toHaveURL(/\/options\/net-gex$/);
  await expect(page.getByRole("search", { name: "Options ticker" })).toBeVisible();

  await page.goto("/options?symbol=MU");
  await expect(page).toHaveURL(/\/options\/net-gex\?symbol=MU$/);

  await page.goto("/options/exposure");
  await expect(page).toHaveURL(/\/options\/net-gex$/);

  await page.goto("/options/exposure?symbol=MU");
  await expect(page).toHaveURL(/\/options\/net-gex\?symbol=MU$/);
});

test("Options workspace remains usable without document-level mobile overflow", async ({ page }) => {
  await page.setViewportSize({ width: 393, height: 852 });
  await mockWorkspace(page);
  await page.goto("/options/net-gex?symbol=MU");

  await expect(page.getByTestId("options-workspace")).toBeVisible();
  await expect(page.getByTestId("options-exposure-panel")).toBeVisible();
  await expect(page.getByLabel("Exposure metric")).toBeVisible();
  await expect(page.getByRole("table", { name: "MU options exposure by strike" })).toBeVisible();
  await expect(page.getByTestId("live-data-degraded")).toHaveCount(0);
  const hasDocumentOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  );
  expect(hasDocumentOverflow).toBe(false);

  await page.screenshot({
    path: resolve(process.cwd(), "../tasks/artifacts/options-exposure-mobile.png"),
    fullPage: true,
  });
});
