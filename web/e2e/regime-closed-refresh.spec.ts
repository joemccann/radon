import { expect, test } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { buildDemoCriFixture } = require("../lib/demo/fixtures/regime") as typeof import("../lib/demo/fixtures/regime");

test.use({ timezoneId: "America/New_York" });

test("actual CRI route refreshes a cached EOD reading with GET during active market hours", async ({ page }) => {
  await installClearFixtures(page);
  // The default Clear clock is Friday 14:00 ET: the real hook polls every 60s.
  // A fully closed clock pauses polling; do not simulate a second algorithm.
  await page.clock.install({ time: new Date("2026-09-04T18:00:00Z") });
  let settled = false;
  const methods: string[] = [];
  await page.route("**/api/regime", async route => {
    methods.push(route.request().method());
    await route.fulfill({ json: {
      ...buildDemoCriFixture(new Date("2026-09-04T18:00:00Z")),
      market_open: false,
      vix: settled ? 26.72 : 24.23,
      scan_time: settled ? "2026-09-04T18:01:00Z" : "2026-09-04T18:00:00Z",
    } });
  });
  await page.goto("/regime/cri");
  const vix = page.locator('[data-testid="strip-vix"]:visible').first();
  await expect(vix).toContainText("24.23");
  await expect(page.locator(".regime-hero-timestamp")).toContainText("2:00:00 PM");
  const initialRequests = methods.length;
  settled = true;
  await page.clock.fastForward(60_000);
  await expect(vix).toContainText("26.72");
  await expect(page.locator(".regime-hero-timestamp")).toContainText("2:01:00 PM");
  expect(methods.length).toBeGreaterThan(initialRequests);
  expect(methods.every(method => method === "GET")).toBe(true);
});


test("actual CRI route pauses timer refreshes when the market clock is closed", async ({ page }) => {
  await installClearFixtures(page);
  await page.clock.install({ time: new Date("2026-09-05T18:00:00Z") });
  await page.clock.setFixedTime(new Date("2026-09-05T18:00:00Z"));
  const methods: string[] = [];
  await page.route("**/api/regime", async route => {
    methods.push(route.request().method());
    await route.fulfill({ json: { ...buildDemoCriFixture(new Date("2026-09-04T20:00:00Z")), market_open: false } });
  });
  await page.goto("/regime/cri");
  await expect(page.locator('[data-testid="strip-vix"]:visible').first()).toBeVisible();
  await expect(page.getByTestId("market-closed-indicator").first()).toBeVisible();
  const initialRequests = methods.length;
  expect(initialRequests).toBeGreaterThan(0);
  await page.clock.fastForward(300_000);
  expect(methods).toHaveLength(initialRequests);
  expect(methods.every(method => method === "GET")).toBe(true);
});
