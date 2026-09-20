/**
 * Mobile toasts must sit above the tab bar, not over hero metrics.
 * Repro: IB Gateway uplink toast covering Portfolio Total account / balance.
 */
import { expect, test } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";

type Box = { x: number; y: number; width: number; height: number };

function boxesOverlap(a: Box, b: Box): boolean {
  return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
}

test.describe("Mobile toast placement", () => {
  test("sits above the tab bar and leaves Total account readable", async ({ page }, testInfo) => {
    await installClearFixtures(page);
    await page.addInitScript(() => {
      localStorage.setItem("theme", "dark");
    });
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });

    await expect(page.getByTestId("mobile-tab-bar")).toBeVisible();
    await expect(page.getByTestId("clear-account-value")).toBeVisible();
    await expect(page.locator("body")).toHaveAttribute("data-mobile", "true");

    await page.evaluate(() => {
      let viewport = document.querySelector<HTMLElement>(".toast-container");
      if (!viewport) {
        viewport = document.createElement("div");
        viewport.className = "toast-container";
        viewport.id = "radon-toast-viewport";
        viewport.style.zIndex = "10002";
        viewport.style.maxHeight = "var(--toast-viewport-max-height, calc(100dvh - 48px - var(--safe-bottom, 0px)))";
        viewport.style.maxWidth = "var(--toast-viewport-max-width, calc(100vw - 40px))";
        viewport.style.overflowY = "auto";
        document.body.appendChild(viewport);
      }
      viewport.innerHTML = [
        '<div class="toast toast-success" role="status">',
        '<span class="toast-message">IB Gateway · uplink restored</span>',
        '<button type="button" class="toast-close" aria-label="Dismiss">×</button>',
        "</div>",
      ].join("");
      viewport.querySelector(".toast-close")?.addEventListener("click", () => {
        viewport?.querySelector(".toast")?.remove();
      });
    });

    const toast = page.locator(".toast-container .toast.toast-success").first();
    await expect(toast).toBeVisible();
    await expect(toast).toContainText("IB Gateway · uplink restored");
    await expect(toast.locator(".toast-close")).toBeVisible();

    const toastBox = await toast.boundingBox();
    const heroBox = await page.getByTestId("clear-account-value").boundingBox();
    const labelBox = await page.getByText("Total account value").first().boundingBox();
    const tabBox = await page.getByTestId("mobile-tab-bar").boundingBox();
    expect(toastBox).toBeTruthy();
    expect(heroBox).toBeTruthy();
    expect(tabBox).toBeTruthy();

    expect(boxesOverlap(toastBox!, heroBox!)).toBe(false);
    if (labelBox) expect(boxesOverlap(toastBox!, labelBox)).toBe(false);
    expect(toastBox!.y).toBeGreaterThan(heroBox!.y + heroBox!.height);
    expect(toastBox!.y + toastBox!.height).toBeLessThanOrEqual(tabBox!.y + 1);

    const placement = await page.locator(".toast-container").evaluate((el) => {
      const style = getComputedStyle(el);
      return { left: style.left, right: style.right, bottom: style.bottom };
    });
    expect(parseFloat(placement.left)).toBeCloseTo(12, 0);
    expect(parseFloat(placement.right)).toBeCloseTo(12, 0);
    expect(parseFloat(placement.bottom)).toBeGreaterThanOrEqual(80);

    const screenshotPath = testInfo.outputPath("mobile-toast-above-tab-bar.png");
    await page.screenshot({ path: screenshotPath, fullPage: false });

    await toast.locator(".toast-close").click();
    await expect(page.locator(".toast-container .toast")).toHaveCount(0);
  });
});
