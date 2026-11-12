import { test, expect } from "@playwright/test";

test.describe("libraries.dev pack C — terminal kit", () => {
  test("shows evaluating gate beam and thinking wait in dark theme", async ({ page }, testInfo) => {
    await page.addInitScript(() => {
      localStorage.setItem("theme", "dark");
    });
    await page.goto("/kit");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(page.getByTestId("libraries-fx-demo")).toBeVisible();
    await expect(page.getByTestId("four-gate-chips")).toBeVisible();
    await expect(page.getByTestId("gate-chip-01")).toHaveAttribute("data-gate-state", "evaluating");
    await expect(page.getByTestId("thinking-wait")).toHaveAttribute("data-kind", "evaluate");
    await page.locator("[data-testid='libraries-fx-demo']").screenshot({
      path: testInfo.outputPath("app_kit_gates_orbs_dark.png"),
    });
  });
});
