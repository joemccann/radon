import { expect, test } from "@playwright/test";

test("Radon Chat focuses its composer on open and still dismisses with Escape", async ({ page }) => {
  // A bare {} for every API crashes WorkspaceShell (portfolio.positions is
  // iterated during render) and the launcher never mounts — stub the shapes
  // the shell actually reads.
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    const payloads: Record<string, unknown> = {
      "/api/portfolio": { positions: [], exposure: {}, violations: [], account_summary: null },
      "/api/orders": { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 },
      "/api/watchlist": { watchlist: [] },
      "/api/service-health": { services: [] },
      "/api/profile": { username: "Operator", avatar_url: null },
      "/api/alerts": { alerts: [] },
      "/api/flex-token": { remaining: 240 },
      "/api/previous-close": { closes: {} },
    };
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payloads[path] ?? {}),
    });
  });
  // /alerts is the lightest WorkspaceShell page: the launcher mounts on every
  // route, and /dashboard's cards each need bespoke payload shapes to render
  // without tripping the route error boundary.
  await page.goto("/alerts");

  const dialog = page.getByRole("dialog", { name: "Radon chat" });
  const composer = page.getByLabel("Message Grok assistant");
  // Headless Chromium delivers Meta/Ctrl+j keydowns to document listeners
  // attached in-page but the launcher's React handler never receives the
  // native press (verified: an in-page probe listener sees the event, the
  // launcher does not; a synthetic dispatch opens it). The shortcut handler
  // itself is covered by the jsdom unit test; this spec's subject is the
  // focus-on-open behavior, so open via the synthetic path and keep the
  // dialog/focus/Escape assertions real. Retry across hydration.
  await expect(async () => {
    if (!(await dialog.isVisible())) {
      await page.evaluate(() => {
        document.dispatchEvent(
          new KeyboardEvent("keydown", { key: "j", metaKey: true, bubbles: true }),
        );
      });
    }
    await expect(dialog).toBeVisible({ timeout: 500 });
  }).toPass({ timeout: 15_000 });
  await expect(composer).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});
