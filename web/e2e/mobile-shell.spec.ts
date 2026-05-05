import { test, expect } from "@playwright/test";

test.describe("MobileShell — phase 1 foundation", () => {
  test("renders top app bar and bottom tab bar at iPhone 16 viewport with no horizontal scroll", async ({ page }) => {
    await page.goto("/dashboard");

    const appBar = page.getByTestId("mobile-app-bar");
    const tabBar = page.getByTestId("mobile-tab-bar");

    await expect(appBar).toBeVisible();
    await expect(tabBar).toBeVisible();

    const widths = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(widths.scrollWidth).toBeLessThanOrEqual(widths.clientWidth + 1);
  });

  test("primary tabs link to expected routes", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByTestId("mobile-tab-bar")).toBeVisible();

    await expect(page.getByTestId("mobile-tab-dashboard")).toHaveAttribute("href", "/dashboard");
    await expect(page.getByTestId("mobile-tab-positions")).toHaveAttribute("href", "/portfolio");
    await expect(page.getByTestId("mobile-tab-orders")).toHaveAttribute("href", "/orders");
    await expect(page.getByTestId("mobile-tab-scanner")).toHaveAttribute("href", "/scanner");
    await expect(page.getByTestId("mobile-tab-more")).toBeVisible();
  });

  test("clicking Positions tab navigates to /portfolio", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByTestId("mobile-tab-positions").click({ force: true });
    await page.waitForURL("**/portfolio");
  });

  test("More tab opens drawer with overflow nav links", async ({ page }) => {
    await page.goto("/dashboard");

    await page.getByTestId("mobile-tab-more").click({ force: true });
    await expect(page.getByTestId("mobile-more-drawer")).toBeVisible();

    await expect(page.getByTestId("mobile-drawer-journal")).toHaveAttribute("href", "/journal");
    await expect(page.getByTestId("mobile-drawer-performance")).toHaveAttribute("href", "/performance");
    await expect(page.getByTestId("mobile-drawer-discover")).toHaveAttribute("href", "/discover");

    await page.getByTestId("mobile-drawer-close").click({ force: true });
    await expect(page.getByTestId("mobile-more-drawer")).toBeHidden();
  });

  test("desktop sidebar is hidden on mobile via body[data-mobile=true]", async ({ page }) => {
    await page.goto("/dashboard");

    const dataMobile = await page.evaluate(() => document.body.dataset.mobile);
    expect(dataMobile).toBe("true");

    const sidebar = page.locator(".sidebar");
    if (await sidebar.count()) {
      await expect(sidebar.first()).toBeHidden();
    }
  });

  test("manifest and service worker assets are reachable", async ({ request }) => {
    const manifestRes = await request.get("/manifest.webmanifest");
    expect(manifestRes.status()).toBe(200);
    const manifest = await manifestRes.json();
    expect(manifest.name).toBe("Radon Terminal");
    expect(manifest.display).toBe("standalone");
    expect(Array.isArray(manifest.icons)).toBe(true);
    expect(manifest.icons.length).toBeGreaterThanOrEqual(2);

    const swRes = await request.get("/sw.js");
    expect(swRes.status()).toBe(200);
    const swBody = await swRes.text();
    expect(swBody).toContain("CACHE_NAME");
    expect(swBody).toContain("/api/");

    const icon192 = await request.get("/icons/icon-192.png");
    expect(icon192.status()).toBe(200);
    const icon512 = await request.get("/icons/icon-512.png");
    expect(icon512.status()).toBe(200);
  });

  test("interactive elements meet 44px touch target floor", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByTestId("mobile-tab-bar")).toBeVisible();

    const moreButton = page.getByTestId("mobile-tab-more");
    const box = await moreButton.boundingBox();
    expect(box).not.toBeNull();
    if (box) {
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.width).toBeGreaterThanOrEqual(44);
    }
  });
});
