import { expect, test, type Page } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";

test.beforeEach(async ({ page }) => {
  const token = process.env.RADON_AUTHLESS_TEST_TOKEN;
  if (token) await page.setExtraHTTPHeaders({ "x-radon-authless-test": token });
});

async function openDashboard(page: Page) {
  await installClearFixtures(page);
  await page.routeWebSocket(/\/ws(?:\?|$)|:8765/, (socket) => {
    socket.send(JSON.stringify({ type: "status", ib_connected: true, market_data_degraded: false, subscriptions: [] }));
  });
  await page.goto("/dashboard");
  await expect(page.locator(".dashboard-surface")).toBeVisible();
}

test.describe("touch platform", () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 393, height: 852 } });

  test("reserves the keyboard viewport, keeps zoom, and exposes press feedback", async ({ page }, testInfo) => {
    await openDashboard(page);
    const viewport = await page.locator('meta[name="viewport"]').getAttribute("content");
    expect(viewport).toContain("viewport-fit=cover");
    expect(viewport).toContain("interactive-widget=resizes-content");
    expect(viewport).not.toMatch(/user-scalable=no|maximum-scale=1(?:,|$)/);
    await expect(page.locator('meta[name="theme-color"]')).toHaveCount(2);
    const search = page.getByTestId("mobile-app-bar-search");
    await expect(search).toBeVisible();
    expect(await search.evaluate((el) => ({
      touch: getComputedStyle(el).touchAction,
      selection: getComputedStyle(el).getPropertyValue("-webkit-user-select") || getComputedStyle(el).userSelect,
      highlight: getComputedStyle(el).getPropertyValue("-webkit-tap-highlight-color"),
    }))).toEqual({ touch: "manipulation", selection: "none", highlight: "rgba(0, 0, 0, 0)" });
    const normalOpacity = await search.evaluate((el) => Number(getComputedStyle(el).opacity));
    // Physical :active is verified with the browser's pointer, not a synthetic event.
    const box = (await search.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    const pressedOpacity = await search.evaluate((el) => Number(getComputedStyle(el).opacity));
    expect(pressedOpacity).toBeLessThan(normalOpacity);
    await page.mouse.up();
    await expect(page.getByTestId("mobile-ticker-search")).toBeVisible();
    const input = page.getByRole("combobox", { name: "Search ticker", exact: true });
    await expect(input).toBeFocused();
    expect(await input.evaluate((el) => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(16);
    expect(await input.evaluate((el) => getComputedStyle(el).getPropertyValue("-webkit-user-select") || getComputedStyle(el).userSelect)).not.toBe("none");
    await page.getByTestId("mobile-ticker-search-close").tap();
    await expect(page.getByTestId("mobile-ticker-search")).toBeHidden();
    if (await page.evaluate(() => CSS.supports("-webkit-text-size-adjust", "100%"))) {
      expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("-webkit-text-size-adjust"))).toBe("100%");
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(393);
    await page.screenshot({ path: testInfo.outputPath("native-phone.png"), fullPage: false });
  });
});

test.describe("touch tablet with desktop navigation", () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 1024, height: 768 } });

  test("keeps desktop-shell inputs at least 16px without disabling content selection", async ({ page }, testInfo) => {
    await openDashboard(page);
    expect(await page.evaluate(() => matchMedia("(any-pointer: coarse)").matches)).toBe(true);
    expect(await page.locator("body").getAttribute("data-mobile")).not.toBe("true");
    const navigation = page.getByRole("navigation", { name: "Primary navigation", exact: true });
    const link = navigation.getByRole("link", { name: "Research", exact: true });
    const beforeHover = await link.evaluate((el) => getComputedStyle(el).color);
    await link.hover();
    await expect.poll(() => link.evaluate((el) => getComputedStyle(el).color)).toBe(beforeHover);
    const search = page.getByRole("combobox").first();
    await expect(search).toBeVisible();
    await search.tap();
    expect(await search.evaluate((el) => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(16);
    expect(await page.locator("body").evaluate((el) => getComputedStyle(el).getPropertyValue("-webkit-user-select") || getComputedStyle(el).userSelect)).not.toBe("none");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1024);
    await page.screenshot({ path: testInfo.outputPath("native-tablet.png"), fullPage: false });
  });
});

test.describe("mouse and keyboard", () => {
  test.use({ hasTouch: false, isMobile: false, viewport: { width: 1440, height: 900 } });

  test("retains desktop hover and keyboard focus presentation", async ({ page }, testInfo) => {
    await openDashboard(page);
    expect(await page.evaluate(() => matchMedia("(hover: hover) and (pointer: fine)").matches)).toBe(true);
    const navigation = page.getByRole("navigation", { name: "Primary navigation", exact: true });
    const control = navigation.getByRole("link", { name: "Research", exact: true });
    await expect(control).toBeVisible();
    const beforeHover = await control.evaluate((el) => getComputedStyle(el).color);
    await control.hover();
    await expect.poll(() => control.evaluate((el) => getComputedStyle(el).color)).not.toBe(beforeHover);
    await page.keyboard.press("Tab");
    await control.focus();
    expect(await control.evaluate((el) => getComputedStyle(el).outlineStyle)).not.toBe("none");
    await page.screenshot({ path: testInfo.outputPath("native-desktop.png"), fullPage: false });
  });
});
