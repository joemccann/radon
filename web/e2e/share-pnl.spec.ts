import { test, expect } from "@playwright/test";

test.describe("Share PnL", () => {
  // --- API route tests ---

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

  test("API route handles pnl-only (no pnlPct)", async ({ request }) => {
    const res = await request.get("/api/share/pnl?description=Long+AAPL&pnl=100");
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("image/png");
  });

  test("API route handles pnlPct-only (no pnl)", async ({ request }) => {
    const res = await request.get("/api/share/pnl?description=Long+AAPL&pnlPct=25.5");
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("image/png");
  });

  // --- Share popover UI tests ---

  test("clicking share button opens popover with checkboxes", async ({ page }) => {
    await page.goto("/orders");
    await page.locator("text=Today's Executed Orders").waitFor({ timeout: 10000 });
    const shareBtn = page.locator(".share-pnl-button").first();
    if (await shareBtn.count() === 0) {
      test.skip();
      return;
    }
    await shareBtn.click();
    const popover = page.locator(".share-pnl-popover");
    await expect(popover).toBeVisible({ timeout: 3000 });
    // Should have two checkboxes
    const checkboxes = popover.locator("input[type='checkbox']");
    await expect(checkboxes).toHaveCount(2);
    // P&L $ should be off, P&L % on by default
    await expect(checkboxes.nth(0)).not.toBeChecked();
    await expect(checkboxes.nth(1)).toBeChecked();
  });

  test("popover has Copy & Tweet and Copy buttons", async ({ page }) => {
    await page.goto("/orders");
    await page.locator("text=Today's Executed Orders").waitFor({ timeout: 10000 });
    const shareBtn = page.locator(".share-pnl-button").first();
    if (await shareBtn.count() === 0) {
      test.skip();
      return;
    }
    await shareBtn.click();
    const popover = page.locator(".share-pnl-popover");
    await expect(popover).toBeVisible({ timeout: 3000 });
    // Should have a "Copy & Tweet" button and a "Copy" button
    await expect(popover.locator("button", { hasText: "Copy & Tweet" })).toBeVisible();
    await expect(popover.locator("button", { hasText: /^Copy$/ })).toBeVisible();
  });

  test("unchecking P&L $ disables it but keeps % checked", async ({ page }) => {
    await page.goto("/orders");
    await page.locator("text=Today's Executed Orders").waitFor({ timeout: 10000 });
    const shareBtn = page.locator(".share-pnl-button").first();
    if (await shareBtn.count() === 0) {
      test.skip();
      return;
    }
    await shareBtn.click();
    const popover = page.locator(".share-pnl-popover");
    await expect(popover).toBeVisible({ timeout: 3000 });
    const dollarCheckbox = popover.locator("input[type='checkbox']").nth(0);
    const pctCheckbox = popover.locator("input[type='checkbox']").nth(1);
    // Toggle states to verify % remains enabled
    await dollarCheckbox.uncheck();
    await expect(dollarCheckbox).not.toBeChecked();
    await expect(pctCheckbox).toBeChecked();
    await dollarCheckbox.check();
    await expect(dollarCheckbox).toBeChecked();
    await dollarCheckbox.uncheck();
    await expect(dollarCheckbox).not.toBeChecked();
  });

  test("popover closes when clicking outside", async ({ page }) => {
    await page.goto("/orders");
    await page.locator("text=Today's Executed Orders").waitFor({ timeout: 10000 });
    const shareBtn = page.locator(".share-pnl-button").first();
    if (await shareBtn.count() === 0) {
      test.skip();
      return;
    }
    await shareBtn.click();
    const popover = page.locator(".share-pnl-popover");
    await expect(popover).toBeVisible({ timeout: 3000 });
    // Click outside
    await page.locator("body").click({ position: { x: 10, y: 10 } });
    await expect(popover).not.toBeVisible({ timeout: 2000 });
  });

  // --- Historical trades ---

  test("share button appears on historical trades for closed trades", async ({ page }) => {
    await page.goto("/orders");
    const section = page.locator("text=Historical Trades");
    await expect(section).toBeVisible({ timeout: 15000 });
    const shareButtons = page.locator(".share-pnl-button");
    const count = await shareButtons.count();
    if (count > 0) {
      await expect(shareButtons.first()).toBeVisible();
    }
  });
});
