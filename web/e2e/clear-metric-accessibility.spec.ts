import { expect, test } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";

test.use({ serviceWorkers: "block", reducedMotion: "reduce", colorScheme: "light" });

for (const width of [390, 1440]) {
  test(`account metrics support keyboard and pointer at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const requests = await installClearFixtures(page);
    await page.goto("/portfolio");
    const risk = page.getByRole("button", { name: "RISK metrics", exact: true });
    await risk.focus();
    await page.keyboard.press("Enter");
    await expect(risk).toHaveAttribute("aria-expanded", "true");
    const buyingPower = page.getByRole("button", { name: "View Buying Power breakdown", exact: true });
    await buyingPower.focus();
    const focusStyle = await buyingPower.evaluate((element) => ({ style: getComputedStyle(element).outlineStyle, width: Number.parseFloat(getComputedStyle(element).outlineWidth) }));
    expect(focusStyle.style).not.toBe("none");
    expect(focusStyle.width).toBeGreaterThanOrEqual(1);
    await page.keyboard.press("Space");
    const dialog = page.getByRole("dialog", { name: "Buying Power", exact: true });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("Interactive Brokers");
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(buyingPower).toBeFocused();
    await risk.click();
    await expect(risk).toHaveAttribute("aria-expanded", "false");
    const netLiq = page.getByRole("button", { name: "View Net Liquidation breakdown", exact: true });
    await netLiq.click();
    await expect(page.getByRole("dialog", { name: "Net Liquidation Value" })).toBeVisible();
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await expect(page.getByRole("dialog")).toBeHidden();
    expect(requests.filter((request) => /\/api\/orders\/(place|cancel|modify)$/.test(request))).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`metrics-${width}.png`), fullPage: false, animations: "disabled" });
  });
}
