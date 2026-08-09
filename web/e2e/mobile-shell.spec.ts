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
    await expect(page.getByTestId("mobile-drawer-discover")).toHaveCount(0);
    await expect(page.getByTestId("mobile-drawer-operator")).toHaveAttribute("href", "/admin");

    await page.getByTestId("mobile-drawer-close").click({ force: true });
    await expect(page.getByTestId("mobile-more-drawer")).toBeHidden();
  });

  test("desktop sidebar is hidden on mobile via body[data-mobile=true]", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByTestId("mobile-tab-bar")).toBeVisible();

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

  test("bottom nav stays pinned and the ticker cockpit fills the viewport (no dead gap)", async ({ page }) => {
    // Regression: on the ticker-detail cockpit the mobile height chain collapsed
    // (.main only had min-height, .content flex:1 grew to its content), so a short
    // flat position left a dead gap between the content and the fixed bottom nav.
    // The cockpit must fill the space between the fixed app bar and tab bar, and
    // the nav must sit flush at the visual-viewport bottom with no body scroll.
    await page.goto("/GOOGL");

    const tabBar = page.getByTestId("mobile-tab-bar");
    await expect(tabBar).toBeVisible();
    await expect(page.locator(".cockpit-host")).toBeVisible();

    const geo = await page.evaluate(() => {
      const bar = document.querySelector(".mobile-tab-bar");
      const cockpit = document.querySelector(".cockpit-host");
      const vpBottom = window.visualViewport ? window.visualViewport.height : window.innerHeight;
      const barRect = bar!.getBoundingClientRect();
      const cockpitRect = cockpit!.getBoundingClientRect();
      return {
        navPosition: getComputedStyle(bar!).position,
        navBottom: barRect.bottom,
        navTop: barRect.top,
        cockpitBottom: cockpitRect.bottom,
        cockpitHeight: cockpitRect.height,
        vpBottom,
        innerH: window.innerHeight,
        docScrollH: document.scrollingElement!.scrollHeight,
      };
    });

    // Nav is fixed and flush with the bottom of the visible viewport.
    expect(geo.navPosition).toBe("fixed");
    expect(Math.abs(geo.navBottom - geo.vpBottom)).toBeLessThanOrEqual(2);
    // Cockpit fills down to the nav — no dead gap.
    expect(geo.navTop - geo.cockpitBottom).toBeLessThanOrEqual(8);
    // Cockpit occupies most of the viewport (not collapsed to content height).
    expect(geo.cockpitHeight).toBeGreaterThan(geo.innerH * 0.5);
    // The cockpit scrolls internally — the document itself does not scroll.
    expect(geo.docScrollH).toBeLessThanOrEqual(geo.innerH + 2);
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

  test("dashboard shows Live Market Feed as section 01 with contained Refresh control", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByTestId("mobile-tab-bar")).toBeVisible();

    const visualOrder = await page.locator(".dashboard-section").evaluateAll((sections) =>
      sections
        .map((section) => ({
          id: section.getAttribute("data-testid"),
          label: [
            section.querySelector(".dashboard-section__title")?.textContent?.trim() ??
              section.querySelector(".dashboard-section__toggle")?.getAttribute("aria-label")?.trim(),
            section.querySelector(".dashboard-section__meta span")?.textContent?.trim(),
          ].filter(Boolean).join(" "),
          top: section.getBoundingClientRect().top,
        }))
        .sort((a, b) => a.top - b.top)
        .map(({ id, label }) => ({ id, label })),
    );

    expect(visualOrder).toEqual([
      { id: "dashboard-section-feed", label: "Live Market Feed 01" },
      { id: "dashboard-section-signals", label: "Top Candidates 02" },
      { id: "dashboard-section-catalysts", label: "Upcoming Catalysts 03" },
      { id: "dashboard-section-engine", label: "Engine State 04" },
    ]);

    const newsSection = page.getByTestId("dashboard-section-feed");
    const newsPanel = newsSection.locator(".dashboard-news");
    const refresh = newsPanel.locator(".news-feed-refresh");

    await expect(refresh).toBeVisible();
    const geometry = await page.evaluate(() => {
      const panel = document.querySelector('[data-testid="dashboard-section-feed"] .dashboard-news');
      const portfolioPanel = document.querySelector('[data-testid="dashboard-section-signals"] .snapshot-card');
      const headerEl = panel?.querySelector(".dashboard-news__header");
      const refreshEl = panel?.querySelector(".news-feed-refresh");
      const panelRect = panel?.getBoundingClientRect();
      const portfolioPanelRect = portfolioPanel?.getBoundingClientRect();
      const headerRect = headerEl?.getBoundingClientRect();
      const refreshRect = refreshEl?.getBoundingClientRect();
      const sectionRects = [
        "dashboard-section-feed",
        "dashboard-section-signals",
        "dashboard-section-catalysts",
        "dashboard-section-engine",
      ].map((id) => {
        const rect = document.querySelector(`[data-testid="${id}"]`)?.getBoundingClientRect();
        return rect
          ? {
              id,
              left: rect.left,
              right: rect.right,
              width: rect.width,
            }
          : null;
      });
      return {
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        sectionRects,
        portfolioPanel: portfolioPanelRect
          ? {
              left: portfolioPanelRect.left,
              right: portfolioPanelRect.right,
              width: portfolioPanelRect.width,
            }
          : null,
        panel: panelRect
          ? {
              left: panelRect.left,
              right: panelRect.right,
              width: panelRect.width,
            }
          : null,
        header: headerRect ? { left: headerRect.left, right: headerRect.right } : null,
        refresh: refreshRect
          ? {
              left: refreshRect.left,
              right: refreshRect.right,
              height: refreshRect.height,
            }
          : null,
      };
    });

    expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1);
    expect(geometry.sectionRects.every(Boolean)).toBe(true);
    const sectionRects = geometry.sectionRects.filter((rect): rect is NonNullable<typeof rect> => Boolean(rect));
    const portfolioRect = sectionRects.find((rect) => rect.id === "dashboard-section-feed")!;
    for (const rect of sectionRects) {
      expect(Math.abs(rect.left - portfolioRect.left)).toBeLessThanOrEqual(1);
      expect(Math.abs(rect.right - portfolioRect.right)).toBeLessThanOrEqual(1);
      expect(Math.abs(rect.width - portfolioRect.width)).toBeLessThanOrEqual(1);
    }
    expect(geometry.panel).not.toBeNull();
    expect(geometry.portfolioPanel).not.toBeNull();
    expect(Math.abs(geometry.panel!.left - geometry.portfolioPanel!.left)).toBeLessThanOrEqual(1);
    expect(Math.abs(geometry.panel!.right - geometry.portfolioPanel!.right)).toBeLessThanOrEqual(1);
    expect(Math.abs(geometry.panel!.width - geometry.portfolioPanel!.width)).toBeLessThanOrEqual(1);
    expect(geometry.header).not.toBeNull();
    expect(geometry.refresh).not.toBeNull();
    expect(geometry.refresh!.height).toBeGreaterThanOrEqual(44);
    expect(geometry.refresh!.left).toBeGreaterThanOrEqual(geometry.panel!.left - 1);
    expect(geometry.refresh!.right).toBeLessThanOrEqual(geometry.panel!.right + 1);
    expect(geometry.refresh!.left).toBeGreaterThanOrEqual(geometry.header!.left - 1);
    expect(geometry.refresh!.right).toBeLessThanOrEqual(geometry.header!.right + 1);
  });
});
