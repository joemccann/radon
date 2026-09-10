import { test, expect } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";
import { clearPrimaryNavigation, navItems } from "../lib/data";

for (const width of [360, 390, 768, 1024, 1440]) {
  test(`Clear navigation and controls at ${width}px`, async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 900 });
    await installClearFixtures(page);
    await page.goto("/dashboard");
    const mobile = width <= 640;
    const navigation = page.getByRole("navigation", { name: mobile ? "Primary mobile navigation" : "Primary navigation", exact: true });
    await expect(navigation).toBeVisible();
    await expect(navigation.getByRole("link")).toHaveCount(4);
    for (const item of clearPrimaryNavigation) {
      const link = navigation.getByRole("link", { name: item.label, exact: true });
      await expect(link).toHaveAttribute("href", item.href);
    }
    await expect(navigation.getByRole("link", { name: "Portfolio", exact: true })).toHaveAttribute("aria-current", "page");
    const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(horizontalOverflow).toBeLessThanOrEqual(1);
    await page.screenshot({ path: testInfo.outputPath(`clear-shell-${width}.png`), fullPage: false });

    const more = page.getByRole("button", { name: mobile ? "Open more navigation" : "Open all workspaces", exact: true });
    await more.click();
    const allWorkspaces = page.getByRole("navigation", { name: mobile ? "Overflow navigation" : "All workspaces", exact: true });
    await expect(allWorkspaces).toBeVisible();
    const menuBox = await allWorkspaces.boundingBox();
    expect(menuBox).not.toBeNull();
    expect(menuBox!.x).toBeGreaterThanOrEqual(0);
    expect(menuBox!.x + menuBox!.width).toBeLessThanOrEqual(width);
    for (const item of navItems) {
      await expect(allWorkspaces.getByRole("link", { name: item.label, exact: true })).toHaveAttribute("href", item.href);
    }
    await page.screenshot({ path: testInfo.outputPath(`clear-menu-${width}.png`), fullPage: false });
    await page.keyboard.press("Escape");
    await expect(allWorkspaces).toBeHidden();
    await expect(more).toBeFocused();

    if (mobile) {
      await more.click();
      await page.getByRole("button", { name: "Switch to dark theme" }).click();
      await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
      await page.getByRole("button", { name: "Switch to light theme" }).click();
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Open ticker search" }).click();
      await expect(page.getByTestId("mobile-ticker-search")).toBeVisible();
      await page.getByRole("button", { name: "Close search" }).click();
    } else {
      await expect(page.getByRole("button", { name: "Enter fullscreen" })).toBeVisible();
      await page.getByRole("button", { name: "Toggle theme" }).click();
      await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
      await page.getByRole("button", { name: "Toggle theme" }).click();
      await page.getByRole("button", { name: "Open command palette" }).click();
      await expect(page.getByRole("dialog", { name: "Command palette" })).toBeVisible();
      await page.getByRole("textbox", { name: "Search", exact: true }).fill("AAPL");
      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog", { name: "Command palette" })).toBeHidden();
    }
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

    await more.click();
    await allWorkspaces.getByRole("link", { name: "Orders", exact: true }).click();
    await expect(page).toHaveURL(/\/orders$/);
    await expect(allWorkspaces).toBeHidden();
    await page.goBack();
    await expect(page).toHaveURL(/\/dashboard$/);

    for (const item of clearPrimaryNavigation.filter((item) => item.label !== "Portfolio")) {
      await navigation.getByRole("link", { name: item.label, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`${item.href}$`));
      await expect(navigation.getByRole("link", { name: item.label, exact: true })).toHaveAttribute("aria-current", "page");
      await expect(page.locator(".clear-workstation")).toBeVisible();
    }
    await page.goBack();
    await expect(page).toHaveURL(/\/regime\/cri$/);
  });
}
