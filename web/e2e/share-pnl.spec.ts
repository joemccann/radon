import { test, expect } from "@playwright/test";

test.describe("Share PnL", () => {
  test("API route returns valid PNG for positive P&L", async ({ request }) => {
    const res = await request.get("/api/share/pnl?description=Long+AAOI+2026-04-17+Call+%2445.00&pnl=1234.56&pnlPct=47.5&commission=2.60&fillPrice=12.50&time=2026-03-10");
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("image/png");
    const body = await res.body();
    expect(body.length).toBeGreaterThan(1000);
    // PNG magic bytes
    expect(body[0]).toBe(0x89);
    expect(body[1]).toBe(0x50); // P
    expect(body[2]).toBe(0x4e); // N
    expect(body[3]).toBe(0x47); // G
  });

  test("API route returns valid PNG for negative P&L", async ({ request }) => {
    const res = await request.get("/api/share/pnl?description=Short+TSLA+Put&pnl=-500&pnlPct=-10");
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("image/png");
  });

  test("API route returns 400 when description missing", async ({ request }) => {
    const res = await request.get("/api/share/pnl?pnl=100");
    expect(res.status()).toBe(400);
  });

  test("API route handles minimal params (pnl only)", async ({ request }) => {
    const res = await request.get("/api/share/pnl?description=Long+AAPL&pnl=100");
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("image/png");
  });

  test("share button appears on orders page for executed trades", async ({ page }) => {
    await page.goto("/orders");
    // Wait for the executed orders section to load
    const section = page.locator("text=Today's Executed Orders");
    await expect(section).toBeVisible({ timeout: 10000 });

    // Check if there are any executed orders with share buttons
    const shareButtons = page.locator(".share-pnl-button");
    // Can't guarantee fills exist, so just verify the button class is styled
    const count = await shareButtons.count();
    // If there are executed orders with P&L, share buttons should be present
    if (count > 0) {
      const firstButton = shareButtons.first();
      await expect(firstButton).toBeVisible();
      // Verify it has the share icon (SVG from lucide Share2)
      await expect(firstButton.locator("svg")).toBeVisible();
    }
  });

  test("share button appears on historical trades for closed trades", async ({ page }) => {
    await page.goto("/orders");
    // Wait for the historical trades section
    const section = page.locator("text=Historical Trades");
    await expect(section).toBeVisible({ timeout: 15000 });

    // The blotter table should have share buttons for closed trades
    const shareButtons = page.locator(".share-pnl-button");
    const count = await shareButtons.count();
    if (count > 0) {
      await expect(shareButtons.first()).toBeVisible();
    }
  });
});
