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
  // The shortcut listener attaches on hydration, which races page load —
  // retry the keypress, guarding against toggling an already-open dialog.
  await expect(async () => {
    if (!(await dialog.isVisible())) await page.keyboard.press("Meta+j");
    await expect(dialog).toBeVisible({ timeout: 500 });
  }).toPass({ timeout: 15_000 });
  await expect(composer).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});
