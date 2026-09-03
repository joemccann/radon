import { expect, test, type Page } from "@playwright/test";

const SAFE_ASSISTANT_ERROR =
  "The assistant couldn't complete this turn. No order was placed. Try again or choose another model.";

const PROVIDER_ERROR =
  'OpenAI request failed (400): {"error":{"message":"Unsupported parameter: max_tokens","type":"invalid_request_error","code":"unsupported_parameter"}}';

async function stubShellApis(page: Page, assistantError = false) {
  // A bare {} for every API crashes WorkspaceShell (portfolio.positions is
  // iterated during render) and the launcher never mounts. Stub the shapes the
  // shell reads, plus an optional hostile assistant error frame.
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (assistantError && path === "/api/assistant") {
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: [
          "event: start",
          "data: {}",
          "",
          "event: error",
          `data: ${JSON.stringify({ error: PROVIDER_ERROR })}`,
          "",
          "",
        ].join("\n"),
      });
    }

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
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payloads[path] ?? {}),
    });
  });
}

async function openChat(page: Page) {
  const dialog = page.getByRole("dialog", { name: "Radon chat" });
  // Headless Chromium delivers Meta/Ctrl+j keydowns to document listeners
  // attached in-page but the launcher's React handler never receives the
  // native press. Retry the synthetic path across hydration.
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
  return dialog;
}

test("Radon Chat focuses its composer on open and still dismisses with Escape", async ({ page }) => {
  await stubShellApis(page);
  // /alerts is the lightest WorkspaceShell page: the launcher mounts on every
  // route without dashboard cards needing bespoke payloads.
  await page.goto("/alerts");

  const dialog = await openChat(page);
  const composer = page.getByLabel("Ask Radon");
  await expect(composer).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

test("Radon Chat renders safe recovery copy instead of provider JSON", async ({ page }, testInfo) => {
  await stubShellApis(page, true);
  await page.goto("/alerts");

  const dialog = await openChat(page);
  const composer = page.getByLabel("Ask Radon");
  await composer.fill("Check DRAM IV rank");
  await composer.press("Enter");

  const assistantMessage = dialog.locator(".chat-message.assistant").last();
  await expect(assistantMessage.locator(".chat-role")).toHaveText("Radon");
  await expect(assistantMessage.locator(".chat-message-body")).toHaveText(SAFE_ASSISTANT_ERROR);
  await expect(dialog.locator('.chat-messages[aria-busy="false"]')).toBeVisible();
  for (const internalDetail of [
    "OpenAI request failed",
    "max_tokens",
    "max_completion_tokens",
    "invalid_request_error",
    "unsupported_parameter",
  ]) {
    await expect(assistantMessage).not.toContainText(internalDetail);
  }

  await dialog.locator(".chat-launcher__panel").screenshot({
    path: testInfo.outputPath("assistant-provider-error.png"),
  });
});
