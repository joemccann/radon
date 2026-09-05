import { test, expect } from "../../web/node_modules/@playwright/test";

const SHOT_DIR = process.env.FX_SHOT_DIR ?? "/opt/cursor/artifacts";

test.describe("libraries.dev pack C — marketing", () => {
  test("dark theme CTA beam, hero beam, and gate markers", async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem("theme", "dark");
    });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(page.getByTestId("cta-beam").first()).toBeVisible();
    await expect(page.getByTestId("hero-beam")).toBeVisible();
    await page.locator("header").screenshot({
      path: `${SHOT_DIR}/site_header_cta_beam_dark.png`,
    });
    await page.locator("[data-testid='hero-beam']").screenshot({
      path: `${SHOT_DIR}/site_hero_flow_beam_dark.png`,
    });
    await page.locator("#convexity").scrollIntoViewIfNeeded();
    await expect(page.locator(".gates")).toBeVisible();
    await page.locator("#convexity").screenshot({
      path: `${SHOT_DIR}/site_gates_metal_dark.png`,
    });
    await page.locator("#pipeline").scrollIntoViewIfNeeded();
    await expect(page.locator(".pipeline")).toBeVisible();
    await page.locator("#pipeline").screenshot({
      path: `${SHOT_DIR}/site_method_markers_dark.png`,
    });
  });
});
