import { expect, test } from "../../web/node_modules/@playwright/test";

test.describe("site surface previews", () => {
  test("keeps current regime illustrations inside their cards", async ({ page }) => {
    await page.setViewportSize({ width: 2048, height: 900 });
    await page.goto("/");
    const regime = page.locator("#regime");
    await regime.scrollIntoViewIfNeeded();
    const cards = regime.locator(".grid > div");
    await expect(cards).toHaveCount(4);
    for (const card of await cards.all()) {
      await expect(card).toBeVisible();
      const graphic = card.getByRole("img");
      await expect(graphic).toBeVisible();
      const cardBox = await card.boundingBox();
      const graphicBox = await graphic.boundingBox();
      expect(cardBox).not.toBeNull();
      expect(graphicBox).not.toBeNull();
      expect(graphicBox!.x).toBeGreaterThanOrEqual(cardBox!.x);
      expect(graphicBox!.x + graphicBox!.width).toBeLessThanOrEqual(cardBox!.x + cardBox!.width);
    }
  });
});
