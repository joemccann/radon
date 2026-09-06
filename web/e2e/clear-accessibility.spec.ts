import { expect, test } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";

test.use({ serviceWorkers: "block", reducedMotion: "reduce" });

for (const viewport of [
  { width: 1440, height: 1000, theme: "dark", label: "desktop dark" },
  { width: 390, height: 844, theme: "dark", label: "mobile dark" },
  // 720 CSS pixels is the reflow width of a 1440px desktop at 200% zoom.
  { width: 720, height: 500, theme: "light", label: "200-percent reflow equivalent" },
] as const) {
  test(`Clear ${viewport.label} retains readable account and keyboard navigation`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    await installClearFixtures(page);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("slider", { name: "Inspect account value history" })).toBeVisible();
    const mobile = viewport.width <= 640;
    const more = page.getByRole("button", { name: mobile ? "Open more navigation" : "Open all workspaces", exact: true });
    if (viewport.theme === "dark") {
      if (mobile) {
        await more.click();
        await page.getByRole("button", { name: "Switch to dark theme" }).click();
        await page.keyboard.press("Escape");
      } else await page.getByRole("button", { name: "Toggle theme" }).click();
    }
    await expect(page.locator("html")).toHaveAttribute("data-theme", viewport.theme);
    await expect(page.getByTestId("clear-account-value")).toContainText("1,246,820.42");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
    await more.focus();
    await page.keyboard.press("Enter");
    const navigation = page.getByRole("navigation", { name: mobile ? "Overflow navigation" : "All workspaces", exact: true });
    await expect(navigation).toBeVisible();
    const bounds = await navigation.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width);
    await page.keyboard.press("Escape");
    await expect(more).toBeFocused();
    await page.screenshot({ path: testInfo.outputPath(`${viewport.width}-${viewport.theme}.png`), fullPage: false });
  });
}
