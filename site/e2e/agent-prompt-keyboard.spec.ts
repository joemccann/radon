import { expect, test } from "../../web/node_modules/@playwright/test";

test("agent prompt modal owns focus, traps Tab, and restores focus on Escape", async ({ page }) => {
  await page.goto("/crash-risk-index");
  const trigger = page.getByRole("button", { name: "View prompt" }).first();
  await trigger.click();
  const close = page.getByRole("button", { name: "Close", exact: true });
  await expect(close).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(close).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(close).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("mobile pages contain wide figures and support keyboard table scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of ["/", "/fractional-kelly-position-sizing", "/unusual-whales-interactive-brokers"]) {
    await page.goto(path);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  }
  for (const [path, name] of [["/fractional-kelly-position-sizing", "Kelly worked example"], ["/unusual-whales-interactive-brokers", "Data source requirements"]]) {
    await page.goto(path);
    const region = page.getByRole("region", { name });
    await region.focus();
    await expect(region).toBeFocused();
    await region.press("ArrowRight");
    await expect.poll(() => region.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
  }
});

test("mobile prompt dialog contains content and closes through backdrop", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/developers/recipes");
  const trigger = page.getByRole("button", { name: "View prompt" }).first();
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Agent prompt" });
  await expect(dialog).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await dialog.getByRole("heading", { name: "Agent prompt" }).click();
  await expect(dialog).toBeVisible();
  await page.mouse.click(2, 2);
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});
