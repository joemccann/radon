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
  // Wait for the launcher to report its ⌘J keydown listener attached, then send
  // ONE real key press. A retry loop around a synthetic document-level event
  // passed even with the launcher's handler deleted, so it verified nothing.
  await expect(page.getByTestId("chat-launcher-ready")).toBeAttached({ timeout: 30_000 });
  await page.keyboard.press("ControlOrMeta+j");
  await expect(dialog).toBeVisible();
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

  const assistantMessage = dialog.getByTestId("chat-message-assistant").last();
  await expect(assistantMessage.getByTestId("chat-role")).toHaveText("Radon");
  await expect(assistantMessage.getByTestId("chat-message-body")).toHaveText(SAFE_ASSISTANT_ERROR);
  await expect(dialog.getByTestId("chat-messages")).toHaveAttribute("aria-busy", "false");
  for (const internalDetail of [
    "OpenAI request failed",
    "max_tokens",
    "max_completion_tokens",
    "invalid_request_error",
    "unsupported_parameter",
  ]) {
    await expect(assistantMessage).not.toContainText(internalDetail);
  }

  await dialog.getByTestId("chat-launcher-panel").screenshot({
    path: testInfo.outputPath("assistant-provider-error.png"),
  });
});
