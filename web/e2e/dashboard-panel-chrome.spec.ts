import { expect, test } from "@playwright/test";

test("dashboard panels render without the ruled left-edge treatment or its gutter", async ({ page }) => {
  await page.goto("/dashboard");

  await expect(page.getByTestId("dashboard-section-feed")).toBeVisible();
  await expect(page.getByTestId("dashboard-section-signals")).toBeVisible();
  await expect(page.locator(".panel-edge-trace")).toHaveCount(0);

  const panelChrome = await page.locator(".snapshot-card").evaluateAll((panels) =>
    panels.map((panel) => {
      const style = getComputedStyle(panel);
      return {
        paddingLeft: style.paddingLeft,
        paddingRight: style.paddingRight,
        repeatingGradient: style.backgroundImage.includes("repeating-linear-gradient"),
      };
    }),
  );

  expect(panelChrome.length).toBeGreaterThanOrEqual(4);
  expect(panelChrome.every((panel) => panel.paddingLeft === panel.paddingRight)).toBe(true);
  expect(panelChrome.every((panel) => !panel.repeatingGradient)).toBe(true);
});
