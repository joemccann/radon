import { test, expect } from "../../web/node_modules/@playwright/test";

test.describe("editorial header on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("keeps the demo CTA label on one line", async ({ page }) => {
    await page.goto("/");

    const cta = page.getByRole("link", { name: "Try the demo" });
    await expect(cta).toBeVisible();
    const lineCount = await cta.evaluate((link) => {
      const label = document.createRange();
      label.selectNodeContents(link);
      return new Set([...label.getClientRects()].map((rect) => Math.round(rect.top))).size;
    });
    expect(lineCount).toBe(1);
  });

  test("fits every header control inside the viewport", async ({ page }) => {
    await page.goto("/");

    const rightmostEdge = await page.locator("header").first().evaluate((header) =>
      Math.max(...[...header.querySelectorAll("a, button")].map((el) => el.getBoundingClientRect().right)),
    );
    expect(rightmostEdge).toBeLessThanOrEqual(390);
  });
});
